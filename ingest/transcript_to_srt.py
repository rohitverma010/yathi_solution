"""YouTube → editor-ready English **subtitle** (.srt) exporter.

Reuses the existing Whisper *translations* pipeline in
``youtube_pipeline.get_transcript`` (yt-dlp → ffmpeg chunks → OpenAI
``whisper-1`` translations with ``response_format="verbose_json"``), which
already yields *real* per-segment timestamps. This script adds the missing
piece: turning those raw segments into SubRip (``.srt``) cues that an editor
(Premiere / Resolve / CapCut / YouTube) can drop straight onto a timeline.

Why a re-segmentation pass is needed:
  Raw Whisper segments are sentence-ish and frequently too long for on-screen
  subtitles. Broadcast-style subtitle hygiene is applied here:
    * <= MAX_LINE_CHARS characters per line, <= MAX_LINES lines per cue
    * cues split on word boundaries, time distributed proportional to text
    * cue duration clamped to [MIN_CUE_SECONDS, MAX_CUE_SECONDS]
    * no overlapping cues

The English wording still benefits from a human review pass for Sanskrit
terms / proper nouns -- that is the documented tradeoff of the Whisper
translations endpoint (timestamp accuracy over shloka fidelity).

Usage (from repo root):
    python ingest/transcript_to_srt.py https://youtu.be/UqggcPidPUU
    python ingest/transcript_to_srt.py UqggcPidPUU --out subs/talk.en.srt

The transcript JSON is cached under cache/transcripts/<video_id>.json, so a
re-run only re-formats and costs nothing.

Optional ``--refine`` pass (best of both "accurate timing" and "good
wording"): the literal Whisper English is sent to gpt-4o for a
*boundary-preserving* cleanup -- it may fix grammar, proper nouns, and
Sanskrit transliteration, but must return exactly one polished string per
input segment, so Whisper's real timestamps never move. The refined
transcript is cached separately as cache/transcripts/<video_id>.en.refined.json.

Optional ``--refine-audio`` pass (highest accuracy): implies ``--refine``,
then re-translates each segment FROM THE ORIGINAL TELUGU AUDIO with
``gpt-audio-1.5`` (the same cached ``cache/audio/<video_id>.*`` file the
transcript step already downloaded) and applies a deterministic diff-gate so
the audio version only overrides the gpt-4o wording where it materially
corrects meaning or a doctrinal term -- catching confident whisper
mistranslations a text-only pass cannot see. Segments the audio model cannot
resolve keep the gpt-4o wording and are listed in a
``<id>.en.audio-refined.unresolved.txt`` sidecar (a human-review queue);
every audio override is logged to ``<id>.en.audio-refined.overrides.txt`` for
QA. The result is cached as cache/transcripts/<video_id>.en.audio-refined.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import REPO_ROOT, TRANSCRIPT_DIR


try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env.local")
    load_dotenv(REPO_ROOT / ".env")
except Exception:
    pass

from youtube_pipeline import extract_video_id, get_transcript  # noqa: E402

# --- subtitle hygiene knobs --------------------------------------------------
MAX_LINE_CHARS = 42       # max characters per displayed line
MAX_LINES = 2             # max lines per cue
MAX_CUE_CHARS = MAX_LINE_CHARS * MAX_LINES
MIN_CUE_SECONDS = 1.0     # extend very short cues up to the next cue's start
MAX_CUE_SECONDS = 7.0     # split cues longer than this
GAP_EPSILON = 0.04        # minimum gap kept between adjacent cues (seconds)

# --- refinement (--refine) knobs ---------------------------------------------
REFINE_MODEL = "gpt-4o"
REFINE_BATCH = 30         # segments per gpt-4o request
REFINE_TEMPERATURE = 0.2
FLOW_CONTEXT = 4          # neighbouring cues shown read-only for natural flow

REFINE_SYSTEM = (
    "You polish an existing English machine-translation of HH Sri Chinna "
    "Jeeyar Swamiji's Telugu devotional discourse (pravachanam) into clean "
    "English SUBTITLES, following the official VT Seva subtitling guidelines. "
    "Each subtitle segment is aligned to a fixed on-screen timestamp.\n"
    "\n"
    "INPUT is a JSON object with three arrays:\n"
    "  - \"context_before\": already-finalized cues that play JUST BEFORE this "
    "batch (read-only).\n"
    "  - \"segments\": the cues you must polish, in order.\n"
    "  - \"context_after\": raw cues that play JUST AFTER this batch (read-only).\n"
    "Use context_before / context_after ONLY to make \"segments\" read as a "
    "smooth continuation of them. NEVER translate, return, merge in, or "
    "renumber the context arrays.\n"
    "\n"
    "Return JSON of the form {\"segments\": [...]} containing EXACTLY one "
    "polished string per input segment, in the SAME order. This is an "
    "absolute constraint:\n"
    "  - Do NOT merge, split, add, remove, or reorder segments.\n"
    "  - Output array length MUST equal the input \"segments\" length.\n"
    "  - Keep each segment roughly its original spoken length, and do NOT "
    "relocate substantive content from one segment into another -- each is "
    "tied to a fixed video timestamp, so a word must stay in the frame where "
    "it is spoken. (Adding small connective words for flow is fine.)\n"
    "\n"
    "NATURAL FLOW (most important -- viewers read these cues in sequence with "
    "no audio, so they must connect like one person speaking, NOT a list of "
    "disconnected lines):\n"
    "  - Read the whole batch (plus context) as ONE continuous spoken "
    "passage, then polish each cue so it flows from the previous one into the "
    "next.\n"
    "  - CAPITALIZATION: only start a cue with a capital letter when it begins "
    "a NEW sentence. If a cue continues the sentence from the previous cue, "
    "start it in lowercase (e.g. previous ends 'When we start a task,' -> "
    "next begins 'we ask why we are doing it.').\n"
    "  - PUNCTUATION: end a cue that continues into the next with a comma or "
    "no terminal punctuation; reserve the full stop for where a thought "
    "actually ends. Use '...' only for a genuine trailing pause.\n"
    "  - CONNECTIVES: add light linking words where natural (So, Then, But, "
    "And, Because, Which, That is why) so cause-and-effect and contrast carry "
    "across cues.\n"
    "  - REFERENTS: thread pronouns and consistent terms instead of re-naming "
    "the same thing every cue ('that person... he... him'); keep a recurring "
    "term translated the SAME way throughout.\n"
    "  - NO ADJACENT REPETITION: if two neighbouring machine cues say nearly "
    "the same thing, rephrase the second to advance the thought (or render "
    "HH's intentional repetition as a deliberate echo), never as an identical "
    "duplicate line.\n"
    "  - Questions HH asks in a series should read as a connected rhetorical "
    "build, not isolated one-offs.\n"
    "\n"
    "STYLE (per VT Seva guidelines):\n"
    "  - Convey HH's meaning in natural English -- NOT a word-for-word "
    "transliteration. Each segment should read as a clean sentence or "
    "meaningful phrase, not a literal Telugu-to-English word string.\n"
    "  - Be crisp and concise: drop filler/unessential words ('actually', "
    "'you know', repeated words) but never drop any point HH makes.\n"
    "  - Do NOT add interpretation or outside knowledge; only render what HH "
    "actually says.\n"
    "\n"
    "SANSKRIT / TRANSLITERATION (Prajna colon scheme -- NOT IAST diacritics):\n"
    "  - Use the colon ':' to mark long vowels, e.g. Ve:da, sa:sthra, "
    "slo:ka, upade:sa, Na:ra:yana, Bhagavad Gi:tha, Duryo:dhana, "
    "a:thma, jna:na, thatthva, sara:nagathi.\n"
    "  - Do NOT add a trailing 'm' to words like Ve:da, sa:sthra, slo:ka.\n"
    "  - Plurals take a plain 's' with NO colon before it: Pa:ndavas, "
    "Kauravas, Ve:das, de:vathas, rahasyas.\n"
    "  - If a segment is part of a recited Sanskrit verse (slo:ka), render "
    "it in clean, consistent Prajna colon transliteration; do NOT translate "
    "the verse line into English.\n"
    "  - Fix proper nouns / deity names (e.g. 'Manarayana' -> 'Na:ra:yana', "
    "'Jai Shri Manarayana' -> 'Jai Shri:man Na:ra:yana').\n"
    "\n"
    "PUNCTUATION CONVENTIONS (plain ASCII -- italics are omitted because SRT "
    "cannot render them):\n"
    "  - Square brackets [ ] for implied words not spoken by HH but needed "
    "to complete the thought, e.g. 'Then, he [Sanjaya] said...'. Use sparingly.\n"
    "  - Double quotes for direct speech HH attributes to someone, e.g. "
    "Sanjaya said \"...\".\n"
    "  - Single quotes for naming references, e.g. the message is known as "
    "'Bhagavad Gi:tha'.\n"
    "  - Parentheses to gloss a Sanskrit word in English when needed, e.g. "
    "kavi (poet).\n"
    "\n"
    "PREFERRED ENGLISH FOR COMMON TELUGU TERMS:\n"
    "  upade:sa=teaching/preaching; thatthva=philosophy (or nature/reality "
    "by context); Bhagava:n=God; daya=grace/compassion; krupa=grace; "
    "kshama=forgiveness; sa:rathi=the Captain; rushis=sages; "
    "shishya=disciple; avata:ra=incarnation/form; grandha=epic/scripture; "
    "gunas=qualities; sa:ramu/tha:tparyam=essence/summary; srushti=creation; "
    "rakshaka=savior/protector; anugraham=God's grace; uddharana=upliftment; "
    "sara:nagathi=surrender; mu:lamu=root/source; paramapadam=the ultimate "
    "eternal abode; antarya:mi=indweller/omnipresent; utthama=noble/the best; "
    "yo:gyatha=qualification; sa:dhana=means/tool; sto:thra=eulogize/praise; "
    "agra pu:ja=initial prayer; pa:pam=sin; do:sham=flaw.\n"
    "\n"
    "Preserve meaning faithfully -- never summarize away a point, add "
    "commentary, or refuse. The content is benign religious teaching."
)


# --- boundary-preserving refinement pass -------------------------------------


def _refine_batch(
    client,
    texts: list[str],
    context_before: list[str] | None = None,
    context_after: list[str] | None = None,
) -> list[str]:
    """Refine one batch of segment strings, returning the same count or raising.

    ``context_before`` (already-refined preceding cues) and ``context_after``
    (upcoming raw cues) are passed read-only so the model can make the active
    segments flow naturally out of what came before and into what follows --
    it must NOT return or translate them, only the ``segments`` array.
    """
    user = json.dumps(
        {
            "context_before": context_before or [],
            "segments": texts,
            "context_after": context_after or [],
        },
        ensure_ascii=False,
    )
    comp = client.chat.completions.create(
        model=REFINE_MODEL,
        messages=[
            {"role": "system", "content": REFINE_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=REFINE_TEMPERATURE,
        response_format={"type": "json_object"},
    )
    payload = json.loads(comp.choices[0].message.content or "{}")
    out = payload.get("segments")
    if not isinstance(out, list) or len(out) != len(texts):
        raise ValueError(
            f"refine returned {len(out) if isinstance(out, list) else 'non-list'} "
            f"items for {len(texts)} inputs"
        )
    return [str(s) for s in out]


def _refine_with_split(
    client,
    texts: list[str],
    progress_cb=None,
    context_before: list[str] | None = None,
    context_after: list[str] | None = None,
) -> list[str]:
    """Refine ``texts``, halving the batch on a count mismatch before giving up.

    The gpt-4o JSON pass occasionally returns the wrong number of segments for
    a batch (e.g. 29 for 30). Rather than dropping the whole batch back to raw
    machine text, retry on progressively smaller halves so only a truly
    un-refinable single segment falls back to its literal text -- this keeps
    timing perfectly aligned while maximizing how much of the transcript gets
    the guideline-compliant wording pass. The flow context follows each half so
    continuity is preserved even when a batch is split.
    """
    if not texts:
        return []
    try:
        return _refine_batch(client, texts, context_before, context_after)
    except Exception as exc:  # noqa: BLE001
        if len(texts) == 1:
            if progress_cb:
                progress_cb(f"  1 segment fell back to literal text ({exc})")
            return texts
        if progress_cb:
            progress_cb(f"  splitting batch of {len(texts)} after: {exc}")
        mid = len(texts) // 2
        left = _refine_with_split(
            client, texts[:mid], progress_cb, context_before, texts[mid:]
        )
        right = _refine_with_split(
            client, texts[mid:], progress_cb, left[-FLOW_CONTEXT:], context_after
        )
        return left + right



def refine_segments(segments: list[dict], progress_cb=None) -> list[dict]:
    """Improve segment *wording* with gpt-4o while preserving every timestamp.

    Processes in fixed-size batches. Each segment keeps its {start, end}; only
    `text` changes. If a batch fails validation (length mismatch / API error),
    that batch falls back to the original literal text so timing can never
    drift out of sync.
    """
    from openai import OpenAI

    client = OpenAI()
    refined: list[dict] = []
    total = len(segments)
    for start in range(0, total, REFINE_BATCH):
        batch = segments[start:start + REFINE_BATCH]
        if progress_cb:
            progress_cb(
                f"Refining segments {start + 1}-{start + len(batch)}/{total} "
                f"with {REFINE_MODEL}..."
            )
        texts = [str(s.get("text", "")) for s in batch]
        context_before = [c["text"] for c in refined[-FLOW_CONTEXT:]]
        after_slice = segments[start + len(batch):start + len(batch) + FLOW_CONTEXT]
        context_after = [str(s.get("text", "")) for s in after_slice]
        polished = _refine_with_split(
            client, texts, progress_cb, context_before, context_after
        )
        for seg, new_text in zip(batch, polished):
            refined.append(
                {
                    "start": float(seg["start"]),
                    "end": float(seg["end"]),
                    "text": new_text.strip() or str(seg.get("text", "")).strip(),
                }
            )
    return refined



def normalize_recited_slokas(segments: list[dict], progress_cb=None) -> list[dict]:
    """Correct recited Bhagavad Gi:tha verse lines to authoritative srikaryam text.

    Best-effort: imports the self-contained normalizer that ships in the
    ``subtitles/`` kit (``subtitles/sloka_normalize.py`` +
    ``subtitles/slokas_authoritative.json``). Only cues that confidently match a
    contiguous span of a known verse are rewritten -- prose is never touched --
    so this is safe to run unconditionally. If the kit is unavailable, segments
    are returned unchanged.
    """
    subtitles_dir = REPO_ROOT / "subtitles"
    try:
        if str(subtitles_dir) not in sys.path:
            sys.path.insert(0, str(subtitles_dir))
        from sloka_normalize import load_sloka_index, normalize_segments
    except Exception as exc:  # noqa: BLE001
        if progress_cb:
            progress_cb(f"Skipping sloka normalization (unavailable): {exc}")
        return segments

    index, kgram_map = load_sloka_index()
    new_segments, changes = normalize_segments(segments, index, kgram_map)
    if progress_cb:
        if changes:
            progress_cb(
                f"Normalized {len(changes)} recited Gi:tha verse line(s) "
                "to authoritative text:"
            )
            for c in changes:
                progress_cb(f"  [{c['index']}] Gi:tha {c['verse']} -> {c['after']!r}")
        else:
            progress_cb("No recited Gi:tha verse lines needed normalization.")
    return new_segments


def _split_text(text: str, max_chars: int) -> list[str]:
    """Greedily pack words into pieces of at most ``max_chars`` characters."""
    words = text.split()
    pieces: list[str] = []
    cur = ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= max_chars:
            cur = f"{cur} {w}"
        else:
            pieces.append(cur)
            cur = w
    if cur:
        pieces.append(cur)
    return pieces or [text.strip()]


def _wrap_lines(text: str) -> str:
    """Word-wrap one cue's text into <= MAX_LINES lines of <= MAX_LINE_CHARS."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= MAX_LINE_CHARS:
            cur = f"{cur} {w}"
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    # If we overflowed past MAX_LINES (very long words), keep all lines rather
    # than dropping text -- an editor can fix the rare overflow by hand.
    return "\n".join(lines[:MAX_LINES] + (["\n".join(lines[MAX_LINES:])] if len(lines) > MAX_LINES else []))


def _segment_to_cues(start: float, end: float, text: str) -> list[dict]:
    """Break one Whisper segment into one or more subtitle cues."""
    text = " ".join(text.split())
    if not text:
        return []
    dur = max(0.0, end - start)
    needs_split = len(text) > MAX_CUE_CHARS or dur > MAX_CUE_SECONDS
    if not needs_split:
        return [{"start": start, "end": end, "text": _wrap_lines(text)}]

    pieces = _split_text(text, MAX_CUE_CHARS)
    # Further split by time budget: if the segment is long, ensure no piece
    # would exceed MAX_CUE_SECONDS once time is distributed proportionally.
    total_chars = sum(len(p) for p in pieces) or 1
    cues: list[dict] = []
    cursor = start
    for i, piece in enumerate(pieces):
        share = len(piece) / total_chars
        piece_dur = dur * share
        piece_end = end if i == len(pieces) - 1 else cursor + piece_dur
        cues.append({"start": cursor, "end": piece_end, "text": _wrap_lines(piece)})
        cursor = piece_end
    return cues


def build_cues(segments: list[dict]) -> list[dict]:
    """Convert raw Whisper segments into clean, non-overlapping subtitle cues."""
    cues: list[dict] = []
    for seg in segments:
        cues.extend(
            _segment_to_cues(
                float(seg["start"]), float(seg["end"]), str(seg.get("text", ""))
            )
        )

    # Enforce ordering, minimum duration, and no overlaps.
    cues.sort(key=lambda c: (c["start"], c["end"]))
    for i, cue in enumerate(cues):
        next_start = cues[i + 1]["start"] if i + 1 < len(cues) else None
        if cue["end"] - cue["start"] < MIN_CUE_SECONDS:
            target = cue["start"] + MIN_CUE_SECONDS
            if next_start is not None:
                target = min(target, next_start - GAP_EPSILON)
            cue["end"] = max(cue["end"], target)
        if next_start is not None and cue["end"] > next_start - GAP_EPSILON:
            cue["end"] = max(cue["start"], next_start - GAP_EPSILON)
    return [c for c in cues if c["end"] > c["start"] and c["text"].strip()]


def _fmt_ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def cues_to_srt(cues: list[dict]) -> str:
    blocks = []
    for i, cue in enumerate(cues, start=1):
        blocks.append(
            f"{i}\n{_fmt_ts(cue['start'])} --> {_fmt_ts(cue['end'])}\n{cue['text']}\n"
        )
    return "\n".join(blocks)


def _write_audio_sidecars(out_dir: Path, video_id: str, info: dict) -> None:
    """Write the audio-refine human-review queue and QA override log.

    ``<id>.en.audio-refined.unresolved.txt`` lists segments the audio model
    could not resolve (kept upstream wording -- a human should listen).
    ``<id>.en.audio-refined.overrides.txt`` logs every segment the audio gate
    actually corrected (before -> after + reason) for QA. Each file is removed
    when empty so a stale list never lingers between runs.
    """
    unresolved = info.get("unresolved", [])
    overrides = info.get("overrides", [])

    unresolved_path = out_dir / f"{video_id}.en.audio-refined.unresolved.txt"
    if unresolved:
        lines = [
            f"[{u['index']}] {_fmt_ts(u['start'])} --> {_fmt_ts(u['end'])}\t{u['text']}"
            for u in unresolved
        ]
        unresolved_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  {len(unresolved)} unresolved segment(s) -> {unresolved_path}")
    elif unresolved_path.exists():
        unresolved_path.unlink()

    overrides_path = out_dir / f"{video_id}.en.audio-refined.overrides.txt"
    if overrides:
        lines = []
        for o in overrides:
            ts = f"{_fmt_ts(o['start'])} --> {_fmt_ts(o['end'])}"
            lines.append(f"[{o['index']}] {ts}  ({o['reason']})")
            lines.append(f"  -  {o['before']}")
            lines.append(f"  +  {o['after']}")
            lines.append("")
        overrides_path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
        print(f"  {len(overrides)} audio override(s) -> {overrides_path}")
    elif overrides_path.exists():
        overrides_path.unlink()

    totals = info.get("totals", {})
    if totals:
        print(f"  audio token totals: {totals}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url", help="YouTube URL or 11-char video ID")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output .srt path (default: cache/transcripts/<id>.en.srt)",
    )
    ap.add_argument(
        "--refine",
        action="store_true",
        help="Run a boundary-preserving gpt-4o wording pass before exporting.",
    )
    ap.add_argument(
        "--refine-audio",
        dest="refine_audio",
        action="store_true",
        help=(
            "Implies --refine, then re-translate each segment from the original "
            "Telugu audio with gpt-audio-1.5 and a diff-gate (highest accuracy)."
        ),
    )
    ap.add_argument(
        "--force-refine",
        action="store_true",
        help="Re-run the gpt-4o refine pass even if a cached version exists.",
    )
    ap.add_argument(
        "--no-sloka-normalize",
        action="store_true",
        help=(
            "Skip correcting recited Bhagavad Gi:tha verse lines to the "
            "authoritative srikaryam text (only relevant with --refine)."
        ),
    )
    args = ap.parse_args()

    url = args.url
    if "/" not in url and "=" not in url and len(url) == 11:
        url = f"https://youtu.be/{url}"
    video_id = extract_video_id(url)

    transcript = get_transcript(url, progress_cb=lambda m: print(m, flush=True))
    segments = transcript.get("segments") or []

    do_refine = args.refine or args.refine_audio
    suffix = ".en.srt"
    if do_refine:
        refined_path = TRANSCRIPT_DIR / f"{video_id}.en.refined.json"
        if refined_path.exists() and not args.force_refine:
            print(f"Using cached refined transcript: {refined_path}")
            segments = json.loads(refined_path.read_text(encoding="utf-8"))["segments"]
        else:
            segments = refine_segments(
                segments, progress_cb=lambda m: print(m, flush=True)
            )
            refined_path.write_text(
                json.dumps(
                    {"video_id": video_id, "segments": segments},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"Cached refined transcript -> {refined_path}")
        suffix = ".en.refined.srt"

        if args.refine_audio:
            audio_path = TRANSCRIPT_DIR / f"{video_id}.en.audio-refined.json"
            if audio_path.exists() and not args.force_refine:
                print(f"Using cached audio-refined transcript: {audio_path}")
                segments = json.loads(audio_path.read_text(encoding="utf-8"))["segments"]
            else:
                from audio_refine import audio_refine_segments

                segments, info = audio_refine_segments(
                    segments, video_id, progress_cb=lambda m: print(m, flush=True)
                )
                audio_path.write_text(
                    json.dumps(
                        {"video_id": video_id, "segments": segments},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                print(f"Cached audio-refined transcript -> {audio_path}")
                _write_audio_sidecars(TRANSCRIPT_DIR, video_id, info)
            suffix = ".en.audio-refined.srt"

        if not args.no_sloka_normalize:
            segments = normalize_recited_slokas(
                segments, progress_cb=lambda m: print(m, flush=True)
            )

    cues = build_cues(segments)
    if not cues:
        print("No subtitle cues produced (empty transcript?).")
        return 1

    out_path = args.out or (TRANSCRIPT_DIR / f"{video_id}{suffix}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(cues_to_srt(cues), encoding="utf-8")

    total = transcript.get("duration") or (cues[-1]["end"] if cues else 0)
    print(
        f"\nWrote {len(cues)} subtitle cues "
        f"({_fmt_ts(total)} of video) -> {out_path}"
    )
    print(f"Title: {transcript.get('title', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
