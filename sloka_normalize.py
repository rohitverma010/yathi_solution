"""Normalize recited Bhagavad Gi:tha verse lines in subtitles to authoritative text.

The Whisper-translation + gpt-4o refine pipeline produces good *timing* but
its spelling of recited Sanskrit slo:kas drifts (e.g. ``Yukta Aha:ra
Viha:rasya`` instead of the authoritative ``yuktha:ha:ra viha:rasya``). This
module fixes ONLY those recited Gi:tha verse fragments, by matching each cue
against the authoritative verse text and, when a cue is *confidently* the same
fragment, replacing its wording with the canonical srikaryam transliteration.

Source of truth: ``slokas_authoritative.json`` (the ``slokas`` map keyed
``"chapter.verse"``), sourced from srikaryam.com -- the Sri:mad Bhagavadgi:tha
English Mu:lam blessed by HH Thridandi Chinna Sri:manna:ra:yana Ra:ma:nuja
Ji:yar Swa:miji. This is NOT a translation step and NOT a guess: a cue is only
rewritten when its letters already match a contiguous span of a known verse
above a high coverage threshold; the rewrite just corrects spelling/spacing to
the canonical form. Verses NOT in the Gi:tha (Upanishads, do:sha frameworks,
etc.) are not in this source and are deliberately left untouched.

Library use:
    from sloka_normalize import load_sloka_index, normalize_segments
    index = load_sloka_index()
    segments, changes = normalize_segments(segments, index)

CLI (dry-run report by default; ``--apply`` writes the file in place):
    python subtitles/sloka_normalize.py subtitles/UqggcPidPUU.en.srt
    python subtitles/sloka_normalize.py cache/transcripts/UqggcPidPUU.en.refined.json --apply
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path
from typing import Iterable

# --- matching knobs ----------------------------------------------------------
# A cue is rewritten only when its letters map to a COMPACT, CONTIGUOUS run
# inside a single authoritative verse -- i.e. the cue really is a recited
# fragment of that verse, not prose that happens to share scattered letters.
# Two gates must BOTH pass (see _best_verse_match):
#   coverage = matched_letters / cue_letters        (the cue is almost fully
#                                                     explained by the verse)
#   density  = matched_letters / verse_span_letters  (the matched region of the
#                                                     verse is tightly packed,
#                                                     not smeared across it)
# The density gate is what rejects English prose: its letters only match a
# verse as a sparse subsequence, so density stays low.
COVERAGE_THRESHOLD = 0.90
DENSITY_THRESHOLD = 0.85
MIN_FINGERPRINT_LEN = 12   # skip very short cues (single Sanskrit words, etc.)
MIN_TOKENS = 2             # skip one-word cues -- too ambiguous to match safely

_NON_LETTER = re.compile(r"[^a-z]")
_TRAILING_PUNCT = re.compile(r"([.,;:!?'\"\)\]\u2019\u201d]+)\s*$")
_VERSE_SEP = {"|", "||", "।", "॥"}

# k-gram size used to pre-filter candidate verses before the (costly) per-verse
# SequenceMatcher pass. A genuine recited fragment shares several contiguous
# k-letter runs with its verse even after minor spelling drift; prose shares
# essentially none, so this prunes ~all 700 verses for ordinary cues.
KGRAM = 6


def _fingerprint(text: str) -> str:
    """Lowercase and strip everything except a-z (colons, spaces, punctuation)."""
    return _NON_LETTER.sub("", text.lower())


def _kgrams(fp: str, k: int = KGRAM) -> set[str]:
    """Return the set of length-``k`` substrings of a fingerprint."""
    if len(fp) < k:
        return {fp} if fp else set()
    return {fp[i:i + k] for i in range(len(fp) - k + 1)}


def load_sloka_index(
    path: str | Path | None = None,
) -> tuple[dict[str, tuple[str, list[tuple[str, int, int]]]], dict[str, set[str]]]:
    """Load authoritative verses into a fingerprint index plus a k-gram map.

    Returns ``(index, kgram_map)`` where ``index`` is
    ``{key: (verse_fingerprint, [(word, start, end), ...])}`` and ``kgram_map``
    is ``{key: {kgram, ...}}`` used to cheaply pre-filter candidate verses.
    Each ``(word, start, end)`` ties an authoritative word's original spelling
    to its character span inside the fingerprint, so a matched span can be
    rendered back as canonical words.

    ``path`` defaults to ``slokas_authoritative.json`` next to this file, then
    falls back to the repo's ``data/slokas_authoritative.json``.
    """
    candidates: list[Path] = []
    if path is not None:
        candidates.append(Path(path))
    here = Path(__file__).resolve().parent
    candidates.append(here / "slokas_authoritative.json")
    candidates.append(here.parent / "data" / "slokas_authoritative.json")

    src = next((p for p in candidates if p.exists()), None)
    if src is None:
        raise FileNotFoundError(
            "slokas_authoritative.json not found in: "
            + ", ".join(str(c) for c in candidates)
        )

    raw = json.loads(src.read_text(encoding="utf-8"))
    slokas = raw.get("slokas", {})
    index: dict[str, tuple[str, list[tuple[str, int, int]]]] = {}
    kgram_map: dict[str, set[str]] = {}
    for key, verse in slokas.items():
        if not isinstance(verse, str):
            continue
        fp = ""
        spans: list[tuple[str, int, int]] = []
        for word in verse.split():
            if word in _VERSE_SEP:
                continue
            wf = _fingerprint(word)
            if not wf:
                continue
            start = len(fp)
            fp += wf
            spans.append((word, start, start + len(wf)))
        if fp:
            index[key] = (fp, spans)
            kgram_map[key] = _kgrams(fp)
    return index, kgram_map


def _best_verse_match(
    cue_fp: str, index, kgram_map, min_hits: int = 2
) -> tuple[float, float, str | None, str | None]:
    """Return ``(coverage, density, verse_key, replacement_words)`` for the best match.

    ``coverage`` = matched letters / cue letters. ``density`` = matched letters /
    the length of the verse region those matches span. A genuine recited
    fragment scores high on BOTH; English prose scores high coverage but LOW
    density (its letters are a sparse subsequence smeared across the verse).
    The best match is chosen by the smaller of the two scores so a candidate
    only wins by being strong on both axes.

    Only verses sharing at least ``min_hits`` k-grams with the cue are scored
    with SequenceMatcher; the rest are pruned cheaply.
    """
    cue_grams = _kgrams(cue_fp)
    if not cue_grams:
        return 0.0, 0.0, None, None
    best_score = 0.0
    best_cov = 0.0
    best_den = 0.0
    best_key: str | None = None
    best_repl: str | None = None
    sm = difflib.SequenceMatcher(autojunk=False)
    sm.set_seq1(cue_fp)
    cue_len = len(cue_fp)
    for key, (vfp, spans) in index.items():
        if len(cue_grams & kgram_map[key]) < min_hits:
            continue
        sm.set_seq2(vfp)
        blocks = [b for b in sm.get_matching_blocks() if b.size > 0]
        if not blocks:
            continue
        matched = sum(b.size for b in blocks)
        b_start = blocks[0].b
        b_end = blocks[-1].b + blocks[-1].size
        verse_span = max(1, b_end - b_start)
        coverage = matched / cue_len
        density = matched / verse_span
        score = min(coverage, density)
        if score <= best_score:
            continue
        words = [w for (w, s, e) in spans if s < b_end and e > b_start]
        if not words:
            continue
        best_score = score
        best_cov = coverage
        best_den = density
        best_key = key
        best_repl = " ".join(words)
    return best_cov, best_den, best_key, best_repl


def normalize_text(
    text: str,
    index,
    kgram_map,
    threshold: float = COVERAGE_THRESHOLD,
) -> tuple[str, str | None, float]:
    """Normalize a single cue string.

    Returns ``(new_text, verse_key_or_None, coverage)``. ``new_text`` equals the
    input when no confident verse match is found.
    """
    stripped = text.strip()
    if len(stripped.split()) < MIN_TOKENS:
        return text, None, 0.0
    cue_fp = _fingerprint(stripped)
    if len(cue_fp) < MIN_FINGERPRINT_LEN:
        return text, None, 0.0

    coverage, density, key, replacement = _best_verse_match(cue_fp, index, kgram_map)
    if (
        key is None
        or replacement is None
        or coverage < threshold
        or density < DENSITY_THRESHOLD
    ):
        return text, None, coverage

    trailing_match = _TRAILING_PUNCT.search(stripped)
    trailing = trailing_match.group(1) if trailing_match else ""
    new_text = replacement + trailing
    if _fingerprint(new_text) == _fingerprint(text) and new_text == stripped:
        # Already canonical -- report no change.
        return text, None, coverage
    return new_text, key, coverage


def normalize_segments(
    segments: Iterable[dict],
    index,
    kgram_map,
    threshold: float = COVERAGE_THRESHOLD,
) -> tuple[list[dict], list[dict]]:
    """Normalize the ``text`` of each segment dict; timestamps are untouched.

    Returns ``(new_segments, changes)`` where ``changes`` is a list of
    ``{"index", "verse", "coverage", "before", "after"}`` for audit/logging.
    """
    out: list[dict] = []
    changes: list[dict] = []
    for i, seg in enumerate(segments):
        text = str(seg.get("text", ""))
        new_text, key, coverage = normalize_text(text, index, kgram_map, threshold)
        new_seg = dict(seg)
        if key is not None and new_text != text:
            new_seg["text"] = new_text
            changes.append(
                {
                    "index": i,
                    "verse": key,
                    "coverage": round(coverage, 3),
                    "before": text,
                    "after": new_text,
                }
            )
        out.append(new_seg)
    return out, changes


# --- standalone CLI (operate on .srt or .refined.json files) -----------------

_SRT_BLOCK = re.compile(r"\A(\d+)\n([\d:,]+ --> [\d:,]+)\n(.*)\Z", re.DOTALL)


def _normalize_srt(content: str, index, kgram_map, threshold: float) -> tuple[str, list[dict]]:
    blocks = content.replace("\r\n", "\n").strip("\n").split("\n\n")
    out_blocks: list[str] = []
    changes: list[dict] = []
    for bi, block in enumerate(blocks):
        m = _SRT_BLOCK.match(block)
        if not m:
            out_blocks.append(block)
            continue
        num, ts, body = m.group(1), m.group(2), m.group(3)
        new_body, key, coverage = normalize_text(body, index, kgram_map, threshold)
        if key is not None and new_body != body:
            changes.append(
                {
                    "cue": num,
                    "verse": key,
                    "coverage": round(coverage, 3),
                    "before": body,
                    "after": new_body,
                }
            )
        out_blocks.append(f"{num}\n{ts}\n{new_body}")
    return "\n\n".join(out_blocks) + "\n", changes


def _normalize_refined_json(content: str, index, kgram_map, threshold: float) -> tuple[str, list[dict]]:
    data = json.loads(content)
    segments = data.get("segments", [])
    new_segments, changes = normalize_segments(segments, index, kgram_map, threshold)
    data["segments"] = new_segments
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n", changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", type=Path, help=".srt or .refined.json file to normalize")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Write the corrected file in place (default: dry-run report only).",
    )
    ap.add_argument(
        "--slokas",
        type=Path,
        default=None,
        help="Path to slokas_authoritative.json (default: alongside this script).",
    )
    ap.add_argument(
        "--threshold",
        type=float,
        default=COVERAGE_THRESHOLD,
        help=f"Match coverage threshold 0-1 (default: {COVERAGE_THRESHOLD}).",
    )
    args = ap.parse_args()

    index, kgram_map = load_sloka_index(args.slokas)
    content = args.file.read_text(encoding="utf-8")
    if args.file.suffix == ".srt":
        new_content, changes = _normalize_srt(content, index, kgram_map, args.threshold)
    elif args.file.suffix == ".json":
        new_content, changes = _normalize_refined_json(content, index, kgram_map, args.threshold)
    else:
        print(f"Unsupported file type: {args.file.suffix} (expected .srt or .json)")
        return 1

    if not changes:
        print(f"No recited Gi:tha verse lines needed normalization in {args.file}.")
        return 0

    print(f"{len(changes)} verse line(s) {'corrected' if args.apply else 'would be corrected'}:\n")
    for c in changes:
        loc = c.get("cue") or c.get("index")
        print(f"  [{loc}] Gi:tha {c['verse']} (coverage {c['coverage']})")
        print(f"      - {c['before']!r}")
        print(f"      + {c['after']!r}")

    if args.apply:
        args.file.write_text(new_content, encoding="utf-8")
        print(f"\nWrote {args.file}.")
    else:
        print("\n(dry run -- re-run with --apply to write these changes.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
