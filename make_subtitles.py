"""Self-contained entry point for generating VT Seva English subtitles.

This is a thin convenience wrapper around the canonical pipeline in
``ingest/transcript_to_srt.py`` that:
  * always runs the guideline-compliant gpt-4o ``--refine`` pass,
  * by default also runs the gpt-audio-1.5 ``--refine-audio`` pass
    (audio-grounded re-translation + diff-gate); pass ``--no-refine-audio``
    for a faster/cheaper text-only run,
  * auto-corrects recited Bhagavad Gi:tha verse lines to the authoritative
    srikaryam text (via ``sloka_normalize.py`` in this folder), and
  * writes the finished ``.srt`` into this ``subtitles/`` folder.

The audio-dependent review triage (flagging non-Gi:tha Sanskrit, garbled or
mistranslated cues) is done by a coding agent following ``SKILL.md``, which
adds inline ``[[REVIEW: ...]]`` tags directly in the ``.srt`` so a reviewer
can share just that one file, grep the tags, confirm, and delete them. Pass
``--review-notes`` if you also want the legacy ``<id>.review-notes.md``
audit stub.

Everything needed to (re)produce a subtitle lives in this folder:
  * ``make_subtitles.py``        -- this entry point
  * ``sloka_normalize.py``       -- authoritative Gi:tha verse corrector
  * ``slokas_authoritative.json``-- the 700 Gi:tha verses (srikaryam.com)
  * ``requirements.txt``         -- Python deps
  * ``README.md``                -- full how-to
The heavy lifting (Whisper transcription, gpt-4o refine) is shared infra in
``ingest/``; this wrapper just calls it so editors have one command to run.

Usage (from repo root, venv active, OPENAI_API_KEY in .env.local):
    python subtitles/make_subtitles.py https://youtu.be/UqggcPidPUU
    python subtitles/make_subtitles.py UqggcPidPUU --no-refine-audio  # text-only
    python subtitles/make_subtitles.py UqggcPidPUU --force-refine

The default ``--refine-audio`` pass re-translates each cue from the original
Telugu audio with ``gpt-audio-1.5`` and a diff-gate (catches confident
whisper mistranslations the text-only refine cannot see); see
``ingest/README.md`` for details.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
PIPELINE = HERE / "ingest" / "transcript_to_srt.py"

_SRT_CUE = re.compile(r"\A(\d+)\n([\d:,]+) --> ([\d:,]+)\n(.*)\Z", re.DOTALL)


def _detect_gita_cues(srt_path: Path) -> list[dict]:
    """Identify which finished cues are recited Bhagavad Gi:tha verse lines.

    Runs the same authoritative matcher the pipeline uses, but in *detect* mode:
    it reports every cue whose (already-canonical) text confidently matches a
    known verse, so they can be listed in the review-notes "Already fixed"
    table. Returns ``[{"cue", "start", "verse", "text"}, ...]``. Best-effort:
    returns ``[]`` if the sloka kit can't be imported.
    """
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    try:
        from sloka_normalize import (  # type: ignore
            COVERAGE_THRESHOLD,
            DENSITY_THRESHOLD,
            MIN_FINGERPRINT_LEN,
            MIN_TOKENS,
            _best_verse_match,
            _fingerprint,
            load_sloka_index,
        )
    except Exception:
        return []

    try:
        index, kgram_map = load_sloka_index()
    except Exception:
        return []

    content = srt_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    found: list[dict] = []
    for block in content.strip("\n").split("\n\n"):
        m = _SRT_CUE.match(block)
        if not m:
            continue
        num, start, _end, body = m.group(1), m.group(2), m.group(3), m.group(4)
        if len(body.split()) < MIN_TOKENS:
            continue
        cue_fp = _fingerprint(body)
        if len(cue_fp) < MIN_FINGERPRINT_LEN:
            continue
        coverage, density, key, _repl = _best_verse_match(cue_fp, index, kgram_map)
        if key is not None and coverage >= COVERAGE_THRESHOLD and density >= DENSITY_THRESHOLD:
            found.append(
                {"cue": num, "start": start, "verse": key, "text": body.replace("\n", " ")}
            )
    return found


def _write_review_notes_stub(srt_path: Path, video_id: str, force: bool) -> Path | None:
    """Write a starter ``<id>.review-notes.md`` next to the .srt.

    Auto-fills the "Already fixed (recited Bhagavad Gi:tha verses)" table from
    the verses the matcher recognized, and scaffolds an empty
    "Needs human review" section for a human to complete against the audio.
    Skips (does not overwrite) an existing file unless ``force`` is set.
    """
    notes_path = srt_path.with_name(f"{video_id}.review-notes.md")
    if notes_path.exists() and not force:
        print(
            f"Review-notes already exist, leaving as-is: {notes_path.name} "
            f"(use --force-review-notes to regenerate the stub)."
        )
        return notes_path

    gita = _detect_gita_cues(srt_path)
    if gita:
        rows = "\n".join(
            f"| {g['cue']} | `{g['start'][:8]}` | Gi:tha {g['verse']} | `{g['text']}` |"
            for g in gita
        )
        fixed_section = (
            "Corrected to the authoritative srikaryam text in "
            "[`slokas_authoritative.json`](slokas_authoritative.json):\n\n"
            "| Cue | Time | Verse | Now reads |\n"
            "|-----|------|-------|-----------|\n"
            f"{rows}\n"
        )
    else:
        fixed_section = (
            "_No recited Bhagavad Gi:tha verse lines were detected in this "
            "episode._\n"
        )

    body = f"""# Subtitle review notes — `{video_id}`

These are spots in [`{srt_path.name}`]({srt_path.name}) that an automated
pass **cannot safely correct**. They need a reviewer with the Telugu audio
and/or a qualified Sri:vaishnava scholar (per the subtitle guideline:
non-Gi:tha Sanskrit verses must be verified by Vamsi Swami in the a:sram at
Hyderabad or an equivalently qualified person). **Do not "fix" these from
memory** — verify against the audio.

> This file was scaffolded automatically by `make_subtitles.py`. The
> "Already fixed" table is auto-detected; the "Needs human review" section
> below must be filled in by a human/agent following `SKILL.md` (read the
> SRT, flag non-Gi:tha Sanskrit, mistranslations, repeated/garbled lines,
> and suspect transliterations).

## Already fixed (recited Bhagavad Gi:tha verses)

{fixed_section}
## Needs human review (NOT changed)

_Fill in below. For each item give the cue number(s), timestamp(s), what is
wrong, and the suspected correct reference if known — but leave the SRT text
as-is until verified against the audio._

1. _e.g._ Non-Gi:tha Sanskrit verse — cue NN (`hh:mm:ss`): suspected
   `<Upanishad / source>`, verify wording + translation with audio + scholar.
"""
    notes_path.write_text(body, encoding="utf-8")
    print(
        f"Wrote review-notes stub: {notes_path.name} "
        f"({len(gita)} Gi:tha verse line(s) auto-listed)."
    )
    return notes_path



def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url", help="YouTube URL or 11-char video ID")
    ap.add_argument(
        "--no-refine-audio",
        dest="refine_audio",
        action="store_false",
        help=(
            "Skip the gpt-audio-1.5 audio-grounded re-translation pass "
            "(faster/cheaper text-only run)."
        ),
    )
    ap.add_argument(
        "--refine-audio",
        dest="refine_audio",
        action="store_true",
        help=argparse.SUPPRESS,  # default-on; accepted for back-compat
    )
    ap.set_defaults(refine_audio=True)
    ap.add_argument(
        "--force-refine",
        action="store_true",
        help="Re-run the gpt-4o refine pass even if a cached version exists.",
    )
    ap.add_argument(
        "--no-sloka-normalize",
        action="store_true",
        help="Skip authoritative Gi:tha verse correction.",
    )
    ap.add_argument(
        "--review-notes",
        action="store_true",
        help=(
            "Also scaffold a <id>.review-notes.md audit stub (legacy; the "
            "default is inline [[REVIEW: ...]] tags in the .srt only)."
        ),
    )
    ap.add_argument(
        "--force-review-notes",
        action="store_true",
        help="With --review-notes, overwrite an existing stub.",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output .srt path (default: subtitles/<video_id>.en.srt).",
    )
    args = ap.parse_args()

    url = args.url
    if "/" not in url and "=" not in url and len(url) == 11:
        url = f"https://youtu.be/{url}"
    # Derive the 11-char id for the default output name.
    video_id = url.rstrip("/").split("/")[-1].split("=")[-1][:11]
    out_path = args.out or (HERE / f"{video_id}.en.srt")

    cmd = [
        sys.executable,
        str(PIPELINE),
        url,
        "--refine",
        "--out",
        str(out_path),
    ]
    if args.refine_audio:
        cmd.append("--refine-audio")
    if args.force_refine:
        cmd.append("--force-refine")
    if args.no_sloka_normalize:
        cmd.append("--no-sloka-normalize")

    print(f"Running: {' '.join(cmd)}\n")
    rc = subprocess.call(cmd, cwd=str(REPO_ROOT))
    if rc != 0:
        return rc

    if args.review_notes:
        try:
            _write_review_notes_stub(out_path, video_id, force=args.force_review_notes)
        except Exception as exc:  # never fail the run over the optional stub
            print(f"(Skipped review-notes stub: {exc})")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
