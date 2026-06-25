"""Audio-grounded subtitle correction stage (production).

This is the productionized form of the ``subtitles/audio_refine_prototype.py``
experiment. It re-translates already-timestamped segments *from the original
Telugu audio* with a stronger audio model (``gpt-audio-1.5``), holding each
segment's {start, end} FIXED, then applies a deterministic **diff-gate** so the
audio version only overrides the existing wording when it materially corrects
meaning or doctrine -- otherwise the cleaner upstream text is kept.

Why this stage exists
---------------------
``whisper-1`` translations give accurate timing but are lossy on meaning and
can produce *confident* mistranslations (e.g. "head" for a word meaning
strength) that no text-only pass (the gpt-4o ``--refine`` step) can detect,
because it never hears the audio. Audio grounding is the only way to catch
those. Running it on every segment would also rewrite the ~90% that were
already fine, adding cosmetic churn -- so the diff-gate keeps the audio
correction only where it actually changes meaning or fixes a doctrinal term,
and keeps the upstream (gpt-4o-refined) wording everywhere else.

Pipeline position (see ``transcript_to_srt.py --refine-audio``)::

    raw whisper segments
      -> refine_segments        (gpt-4o: wording + natural flow)
      -> audio_refine_segments  (THIS: correct meaning from audio, gated)
      -> normalize_recited_slokas
      -> build_cues -> SRT

Design guarantees carried over from the prototype:
  * ONE corrected string per input segment -- timestamps never move.
  * ZERO silent fallback: a window that fails parsing is retried (strict) then
    split into halves and retried; a single segment that still fails keeps its
    upstream text but is recorded as ``unresolved`` (a human-review queue),
    never silently passed through as if it were verified.

The audio comes from the same ``cache/audio/<video_id>.*`` file that
``youtube_pipeline.get_transcript`` already downloads, so no extra fetch is
needed for any future video.
"""
from __future__ import annotations

import base64
import difflib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from common import CACHE_DIR

AUDIO_DIR = CACHE_DIR / "audio"

# --- audio model knobs -------------------------------------------------------
AUDIO_MODEL = "gpt-audio-1.5"
WINDOW_SECONDS = 45.0       # max audio span per request (longer clips 500)
MAX_SEGMENTS_PER_WINDOW = 12
AUDIO_TEMPERATURE = 0.2
MAX_RETRIES = 3             # attempts per window before splitting it
MAX_SPLIT_DEPTH = 4         # how many times a stuck window may be halved
CLIP_PAD = 0.3             # seconds of audio padding kept on each side

# --- diff-gate knobs ---------------------------------------------------------
# Below this similarity the audio is treated as a real meaning change and wins.
GATE_SIM_THRESHOLD = 0.62
# Whisper artifacts the audio is expected to fix (audio wins if it removes one).
_BANNED_TERMS = ("book", "idol", "idols")
# Preferred VT Seva doctrine/glossary terms; audio wins if it introduces one
# the upstream wording was missing (a genuine doctrinal correction).
_PREFERRED_TERMS = (
    "scripture", "deity", "deities", "bhagava:n",
    "bha:rata", "pa:rtha", "kaunte:ya",
    "sa:thvic", "ra:jasic", "tha:masic",
    "ja:thi", "a:sraya", "nimitta",
)

SYSTEM = (
    "You are a faithful Telugu->English subtitle translator for HH Sri Chinna "
    "Jeeyar Swamiji's Bhagavad Gi:tha discourse (pravachanam). You are given "
    "the ORIGINAL AUDIO for a short window plus the existing machine-English "
    "cues for that same window. Each cue is locked to a fixed on-screen "
    "timestamp.\n"
    "\n"
    "TASK: Using the AUDIO as the source of truth (the existing English is "
    "often mistranslated), return a corrected English translation for EACH "
    "cue, conveying what HH actually says in that cue's time span.\n"
    "\n"
    "HARD CONSTRAINTS:\n"
    "  - Return JSON {\"cues\": [...]} with EXACTLY one string per input cue, "
    "in the SAME order. Length MUST equal the input count.\n"
    "  - A cue is tied to its timestamp: put the meaning spoken DURING that "
    "cue in that cue. Do not move content between cues. Small connective words "
    "for flow are fine.\n"
    "  - Keep each cue roughly its spoken length (subtitle-sized).\n"
    "\n"
    "STYLE / DOCTRINE (VT Seva guidelines):\n"
    "  - Convey meaning in natural English, not a word-for-word gloss.\n"
    "  - Call the text a 'scripture', never a 'book'. God is 'Bhagava:n'. The "
    "worshipped form is a 'deity', never an 'idol'.\n"
    "  - 'Bha:rata'/'Pa:rtha'/'Kaunte:ya' are vocative address to Arjuna, NOT "
    "the country or a proper noun.\n"
    "  - Food qualities: sa:thvic / ra:jasic / tha:masic. Food faults: ja:thi "
    "(by nature), a:sraya (by association), nimitta (incidental, e.g. an "
    "insect/hair falls in).\n"
    "\n"
    "TRANSLITERATION (Prajna colon scheme, NOT IAST diacritics):\n"
    "  - Mark long vowels with ':' e.g. Na:ra:yana, Bhagavad Gi:tha, jna:na, "
    "ja:thi, vive:ka, a:thma, sa:dhana, thatthva.\n"
    "  - No trailing 'm' on Ve:da/sa:sthra/slo:ka. Plurals take plain 's' "
    "(Ve:das, Pa:ndavas).\n"
    "  - Recited Sanskrit verse lines: render in clean colon transliteration, "
    "do NOT translate them into English.\n"
    "  - 'Jai Sri:manna:ra:yana!' is the standard opening/closing salutation.\n"
)

_NUM_LINE = re.compile(r"^\s*(\d+)[.)]\s*(.*)$")


# --- model-reply parsing (tolerant: JSON array first, numbered list fallback) -


def _parse_numbered(raw: str) -> list[str]:
    """Parse a '1. text' numbered list into strings (handles wrapped lines)."""
    items: list[str] = []
    cur: str | None = None
    for line in raw.replace("\r\n", "\n").split("\n"):
        m = _NUM_LINE.match(line)
        if m:
            if cur is not None:
                items.append(cur.strip())
            cur = m.group(2)
        elif cur is not None and line.strip():
            cur += " " + line.strip()
    if cur is not None:
        items.append(cur.strip())
    return items


def _extract_json_array(raw: str) -> list | None:
    """Pull a JSON list out of a model reply (code fences / {"cues": []} / prose)."""
    s = raw.strip()
    s = re.sub(r"^```(?:json)?", "", s).strip()
    s = re.sub(r"```$", "", s).strip()
    try:
        obj = json.loads(s)
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict) and isinstance(obj.get("cues"), list):
            return obj["cues"]
    except Exception:
        pass
    i, j = s.find("["), s.rfind("]")
    if 0 <= i < j:
        try:
            obj = json.loads(s[i : j + 1])
            if isinstance(obj, list):
                return obj
        except Exception:
            pass
    return None


def _parse_cues(raw: str, n: int) -> list[str] | None:
    """Return exactly n non-empty cue strings, or None if the reply is unusable."""
    arr = _extract_json_array(raw)
    if arr is not None:
        strs: list[str] = []
        for item in arr:
            if isinstance(item, str):
                strs.append(item.strip())
            elif isinstance(item, dict):
                strs.append(str(item.get("text") or item.get("cue") or "").strip())
            else:
                strs.append(str(item).strip())
        if len(strs) == n and all(strs):
            return strs
    items = _parse_numbered(raw)
    if len(items) == n and all(items):
        return items
    return None


# --- audio windowing + slicing -----------------------------------------------


def make_windows(segments: list[dict]) -> list[list[int]]:
    """Group consecutive segment indices into <= WINDOW_SECONDS audio windows."""
    windows: list[list[int]] = []
    cur: list[int] = []
    for idx, seg in enumerate(segments):
        if cur:
            span = float(seg["end"]) - float(segments[cur[0]]["start"])
            if span > WINDOW_SECONDS or len(cur) >= MAX_SEGMENTS_PER_WINDOW:
                windows.append(cur)
                cur = []
        cur.append(idx)
    if cur:
        windows.append(cur)
    return windows


def _slice_audio(src: Path, start_s: float, end_s: float, out: Path) -> None:
    s = max(0.0, start_s - CLIP_PAD)
    dur = (end_s - start_s) + 2 * CLIP_PAD
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{s:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
         "-ac", "1", "-ar", "16000", str(out)],
        capture_output=True,
        check=True,
    )


def _clip_b64(src: Path, start_s: float, end_s: float) -> str:
    fd, name = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    p = Path(name)
    try:
        _slice_audio(src, start_s, end_s, p)
        return base64.b64encode(p.read_bytes()).decode()
    finally:
        p.unlink(missing_ok=True)


def _accum(totals: dict, usage: dict) -> None:
    pd = usage.get("prompt_tokens_details", {}) or {}
    totals["audio_in"] += pd.get("audio_tokens", 0)
    totals["text_in"] += pd.get("text_tokens", 0)
    totals["text_out"] += usage.get("completion_tokens", 0)


def _refine_window(
    client, audio_b64: str, win_segments: list[dict], strict: bool = False
) -> tuple[list[str] | None, dict]:
    """One audio call for a window. Returns (cues_or_None, usage).

    Each cue is tagged with its time span INSIDE the clip so the model aligns
    meaning to timestamps instead of redistributing it across cues.
    """
    win_start = float(win_segments[0]["start"])
    lines = []
    for i, seg in enumerate(win_segments):
        rs = float(seg["start"]) - win_start + CLIP_PAD
        re_ = float(seg["end"]) - win_start + CLIP_PAD
        lines.append(f'{i + 1}. [{rs:5.1f}-{re_:5.1f}s] {str(seg.get("text", ""))}')
    listing = "\n".join(lines)
    extra = (
        "\nReturn ONLY the JSON array. No prose, no markdown, no code fence."
        if strict
        else ""
    )
    n = len(win_segments)
    user = [
        {
            "type": "text",
            "text": (
                f"This audio window has {n} cues. The clip begins ~{CLIP_PAD:.1f}s "
                f"before cue 1. Each line below shows the cue's time span WITHIN "
                f"this clip and its existing machine-English (often wrong):\n"
                f"{listing}\n\n"
                f"Listen to the audio and translate what HH actually says. Put "
                f"the meaning spoken DURING each cue's time span into that cue, "
                f"using the timings to align. Keep each cue subtitle-sized.\n"
                f"Return a JSON array of EXACTLY {n} strings in cue order: "
                f'["cue 1 text", "cue 2 text", ...].{extra}'
            ),
        },
        {"type": "input_audio", "input_audio": {"data": audio_b64, "format": "mp3"}},
    ]
    resp = client.chat.completions.create(
        model=AUDIO_MODEL,
        modalities=["text"],
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=AUDIO_TEMPERATURE,
    )
    usage = resp.usage.model_dump()
    raw = resp.choices[0].message.content or ""
    return _parse_cues(raw, n), usage


def _process_window(
    client,
    src: Path,
    segments: list[dict],
    win_idx: list[int],
    totals: dict,
    progress_cb=None,
    depth: int = 0,
) -> tuple[dict[int, str], set[int]]:
    """Resolve a window, retrying then splitting. Never silently drops a segment.

    Returns (results, unresolved) where results maps segment index -> audio
    text and unresolved is the set of indices that could not be translated from
    audio after every retry/split (those keep upstream text but are flagged).
    """
    win = [segments[i] for i in win_idx]
    b64 = _clip_b64(src, float(win[0]["start"]), float(win[-1]["end"]))
    for attempt in range(MAX_RETRIES):
        try:
            parsed, usage = _refine_window(client, b64, win, strict=attempt > 0)
            _accum(totals, usage)
        except Exception as e:  # noqa: BLE001 - API/transport error, retry
            if progress_cb:
                progress_cb(f"      attempt {attempt + 1} error: {e}")
            parsed = None
        if parsed is not None:
            return {idx: t for idx, t in zip(win_idx, parsed)}, set()

    if len(win_idx) > 1 and depth < MAX_SPLIT_DEPTH:
        mid = len(win_idx) // 2
        if progress_cb:
            progress_cb(
                f"      splitting {len(win_idx)} cues -> {mid} + {len(win_idx) - mid}"
            )
        r1, u1 = _process_window(
            client, src, segments, win_idx[:mid], totals, progress_cb, depth + 1
        )
        r2, u2 = _process_window(
            client, src, segments, win_idx[mid:], totals, progress_cb, depth + 1
        )
        r1.update(r2)
        return r1, (u1 | u2)

    # Single segment still failing: keep upstream text, but FLAG it.
    return ({idx: str(segments[idx].get("text", "")) for idx in win_idx},
            set(win_idx))


# --- diff-gate ----------------------------------------------------------------


def _has_word(text: str, term: str) -> bool:
    """Word-boundary-aware membership test that tolerates colon-scheme terms."""
    return re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", text) is not None


def _gate(refined_text: str, audio_text: str, prev_refined: str) -> tuple[str, str, bool]:
    """Decide whether the audio correction overrides the upstream wording.

    Returns (chosen_text, reason, used_audio). The audio wins when it
    materially changes meaning (low similarity), fixes a doctrinal/glossary
    term, or the upstream cue is a known whisper artifact (duplicate of the
    previous cue). Otherwise the cleaner upstream wording is kept to avoid
    cosmetic churn.
    """
    r = (refined_text or "").strip()
    a = (audio_text or "").strip()
    if not a:
        return refined_text, "empty-audio:keep-upstream", False
    rl, al = r.lower(), a.lower()
    if rl == al:
        return refined_text, "identical", False

    banned_fixed = any(_has_word(rl, t) and not _has_word(al, t) for t in _BANNED_TERMS)
    preferred_added = any(
        _has_word(al, t) and not _has_word(rl, t) for t in _PREFERRED_TERMS
    )
    dup_artifact = bool(prev_refined) and (
        difflib.SequenceMatcher(None, rl, prev_refined.strip().lower()).ratio() > 0.9
    )
    sim = difflib.SequenceMatcher(None, rl, al).ratio()

    if sim < GATE_SIM_THRESHOLD:
        return audio_text, f"meaning-change(sim={sim:.2f})", True
    if banned_fixed:
        return audio_text, "doctrine:removed-banned-term", True
    if preferred_added:
        return audio_text, "doctrine:added-preferred-term", True
    if dup_artifact:
        return audio_text, "whisper-duplicate-artifact", True
    return refined_text, f"minor-diff(sim={sim:.2f}):keep-upstream", False


def find_cached_audio(video_id: str) -> Path | None:
    """Locate the cached source audio downloaded by youtube_pipeline."""
    direct = AUDIO_DIR / f"{video_id}.mp4"
    if direct.exists():
        return direct
    cands = sorted(AUDIO_DIR.glob(f"{video_id}.*"))
    return cands[0] if cands else None


def audio_refine_segments(
    segments: list[dict], video_id: str, progress_cb=None
) -> tuple[list[dict], dict]:
    """Audio-ground and gate ``segments`` (already gpt-4o-refined wording).

    Each segment keeps its {start, end}; only ``text`` may change, and only
    when the diff-gate decides the audio materially corrects it. Returns
    ``(new_segments, info)`` where ``info`` carries the human-review queue and
    QA log::

        {
          "unresolved": [{index, start, end, text}, ...],   # audio failed
          "overrides":  [{index, start, end, before, after, reason}, ...],
          "totals":     {audio_in, text_in, text_out},
          "kept": int, "changed": int,
        }
    """
    from openai import OpenAI

    src = find_cached_audio(video_id)
    if src is None:
        raise FileNotFoundError(
            f"No cached audio for {video_id} in {AUDIO_DIR}. Run the transcript "
            "step first so youtube_pipeline downloads it."
        )

    client = OpenAI()
    windows = make_windows(segments)
    if progress_cb:
        progress_cb(
            f"Audio-refining {len(segments)} segments in {len(windows)} windows "
            f"(<= {WINDOW_SECONDS:.0f}s each) with {AUDIO_MODEL}..."
        )

    audio_map: dict[int, str] = {}
    unresolved_idx: set[int] = set()
    totals = {"audio_in": 0, "text_in": 0, "text_out": 0}
    for wi, win_idx in enumerate(windows, 1):
        results, win_unresolved = _process_window(
            client, src, segments, win_idx, totals, progress_cb
        )
        audio_map.update(results)
        unresolved_idx |= win_unresolved
        if progress_cb:
            flag = f" UNRESOLVED {sorted(win_unresolved)}" if win_unresolved else ""
            progress_cb(f"  window {wi}/{len(windows)} done ({len(win_idx)} cues){flag}")

    new_segments: list[dict] = []
    overrides: list[dict] = []
    prev_text = ""
    for idx, seg in enumerate(segments):
        upstream = str(seg.get("text", ""))
        if idx in unresolved_idx:
            chosen = upstream  # audio unavailable -> keep upstream, flagged below
        else:
            audio_text = audio_map.get(idx, upstream)
            chosen, reason, used_audio = _gate(upstream, audio_text, prev_text)
            if used_audio:
                overrides.append(
                    {
                        "index": idx,
                        "start": float(seg["start"]),
                        "end": float(seg["end"]),
                        "before": upstream,
                        "after": chosen,
                        "reason": reason,
                    }
                )
        new_segments.append(
            {"start": float(seg["start"]), "end": float(seg["end"]), "text": chosen}
        )
        prev_text = chosen

    unresolved = [
        {
            "index": idx,
            "start": float(segments[idx]["start"]),
            "end": float(segments[idx]["end"]),
            "text": str(segments[idx].get("text", "")),
        }
        for idx in sorted(unresolved_idx)
    ]
    info = {
        "unresolved": unresolved,
        "overrides": overrides,
        "totals": totals,
        "kept": len(segments) - len(overrides),
        "changed": len(overrides),
    }
    if progress_cb:
        progress_cb(
            f"Audio gate: {len(overrides)} segment(s) corrected from audio, "
            f"{info['kept']} kept upstream wording, "
            f"{len(unresolved)} unresolved (flagged for human review)."
        )
    return new_segments, info
