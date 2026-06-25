# -*- coding: utf-8 -*-
"""Mechanical helpers for the prose-first Telugu->English subtitle skill.

This script does the parts that must be exact and must NEVER be hand-done:
  * reading the timed Telugu source (SBV or SRT),
  * converting each source timestamp to SRT form,
  * stitching a list of 256 English cue texts onto those exact timings,
  * validating a produced SRT against the source (cue count, identical
    timing order, <=2 lines/cue, line length).

It performs NO translation. Translation is the agent's two-pass job
(see SKILL.md). Keeping timing mechanical guarantees the output stays
frame-accurate to the source.

Usage:
  python sbv_to_srt.py timings  <source.sbv|source.srt>
      -> prints "<n>\\n<idx>\\t<SRT-timestamp>" lines (one per cue)

  python sbv_to_srt.py scaffold <source.sbv|source.srt> <out.txt>
      -> writes a numbered fill-in template: one '@@@CUE i | timing' +
         '@@@TEL <telugu>' marker per source cue, with a blank slot under
         each for you to type that cue's English. Filling this in (instead
         of free prose) makes 1:1 alignment structural and drift obvious.
         Feed the filled file straight to `build`.

  python sbv_to_srt.py build     <source.sbv|source.srt> <english.txt> <out.srt>
      -> english.txt is EITHER a filled scaffold (preferred; '@@@CUE'
         markers) OR N blocks separated by blank lines, block i = the
         English (1-2 lines) for cue i. Must resolve to exactly as many
         blocks as the source has cues. Writes out.srt with source timings.

  python sbv_to_srt.py validate  <source.sbv|source.srt> <candidate.srt>
      -> checks cue count, timing-order match, <=2 lines/cue, line length;
         prints PASS/FAIL with details.

  python sbv_to_srt.py aligncheck <source.sbv|source.srt> <candidate.srt>
      -> REFERENCE-FREE drift detector. Embeds Telugu cue i and English
         cue i with a multilingual model (text-embedding-3-large) and
         reports per-cue cosine. Flags cues where a +/-2 shift matches the
         Telugu better than position i (the signature of cumulative drift,
         i.e. English content running ahead of / behind its timestamp).
         Needs OPENAI_. No gold reference required.
"""
import re
import sys
from pathlib import Path

MAX_LINE = 42          # spec: ~32-42 chars/line
MAX_LINES = 2          # spec: max 2 lines per cue


def _ts_sbv_to_srt(ts: str) -> str:
    """'0:00:00.640' -> '00:00:00,640' (pad hour to 2, dot->comma)."""
    ts = ts.strip()
    h, m, rest = ts.split(":")
    return f"{int(h):02d}:{m}:{rest.replace('.', ',')}"


def parse_source(path: Path):
    """Return [(srt_start, srt_end, telugu_text), ...] from SBV or SRT."""
    raw = path.read_text(encoding="utf-8-sig")
    blocks = [b for b in re.split(r"\r?\n\r?\n", raw) if b.strip()]
    out = []
    for b in blocks:
        lines = b.splitlines()
        # SRT blocks start with an integer index line; SBV blocks don't.
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        timing = lines[0].strip()
        text = "\n".join(lines[1:]).strip()
        if "-->" in timing:                       # already SRT timing
            start, end = [t.strip() for t in timing.split("-->")]
        else:                                      # SBV: "start,end"
            start_raw, end_raw = timing.split(",")
            start = _ts_sbv_to_srt(start_raw)
            end = _ts_sbv_to_srt(end_raw)
        out.append((start, end, text))
    return out


def parse_srt(path: Path):
    """Return [(timing_line, [text_lines]), ...] from an SRT."""
    raw = path.read_text(encoding="utf-8-sig")
    blocks = [b for b in re.split(r"\r?\n\r?\n", raw) if b.strip()]
    out = []
    for b in blocks:
        lines = b.splitlines()
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        timing = lines[0].strip()
        out.append((timing, [ln for ln in lines[1:]]))
    return out


def cmd_timings(source):
    cues = parse_source(Path(source))
    print(len(cues))
    for i, (s, e, _) in enumerate(cues, 1):
        print(f"{i}\t{s} --> {e}")


def cmd_scaffold(source, out):
    cues = parse_source(Path(source))
    parts = []
    for i, (s, e, tel) in enumerate(cues, 1):
        tel_one = " / ".join(tel.splitlines())
        parts.append(f"@@@CUE {i} | {s} --> {e}\n@@@TEL {tel_one}\n")
    Path(out).write_text("\n".join(parts), encoding="utf-8")
    print(f"Wrote scaffold {out} with {len(cues)} cue slots. "
          f"Type each cue's English on the line(s) under its @@@TEL marker, "
          f"then run `build`.")


def _parse_scaffold(text, n_cues):
    """Parse a filled '@@@CUE' scaffold into N English blocks, in order."""
    sections = re.split(r"(?m)^@@@CUE\s+(\d+)\b.*$", text)
    # re.split keeps the captured cue number; sections[0] is preamble.
    blocks = {}
    it = iter(sections[1:])
    for num in it:
        body = next(it, "")
        idx = int(num)
        lines = [ln for ln in body.splitlines()
                 if not ln.startswith("@@@TEL")]
        blocks[idx] = "\n".join(
            ln for ln in (l.rstrip() for l in lines) if ln.strip()
        ).strip()
    missing = [i for i in range(1, n_cues + 1) if not blocks.get(i)]
    if missing:
        sys.exit(f"ERROR: scaffold has empty English for cue(s): "
                 f"{missing[:15]}{' ...' if len(missing) > 15 else ''}. "
                 f"Every cue needs English text.")
    extra = [i for i in blocks if i < 1 or i > n_cues]
    if extra:
        sys.exit(f"ERROR: scaffold has cue numbers out of range: {extra[:15]}")
    return [blocks[i] for i in range(1, n_cues + 1)]


def cmd_build(source, english, out):
    cues = parse_source(Path(source))
    raw = Path(english).read_text(encoding="utf-8")
    if "@@@CUE" in raw:
        blocks = _parse_scaffold(raw, len(cues))
    else:
        blocks = [b.strip() for b in re.split(r"\r?\n\r?\n", raw) if b.strip()]
    if len(blocks) != len(cues):
        sys.exit(f"ERROR: {len(blocks)} English blocks but "
                 f"{len(cues)} source cues. They must match 1:1.")
    parts = []
    for i, ((s, e, _), text) in enumerate(zip(cues, blocks), 1):
        parts.append(f"{i}\n{s} --> {e}\n{text}")
    Path(out).write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    print(f"Wrote {out} with {len(parts)} cues.")


def cmd_validate(source, candidate):
    src = parse_source(Path(source))
    cand = parse_srt(Path(candidate))
    ok = True
    if len(src) != len(cand):
        print(f"FAIL cue count: source {len(src)} vs candidate {len(cand)}")
        ok = False
    src_timings = [f"{s} --> {e}" for s, e, _ in src]
    cand_timings = [t for t, _ in cand]
    mism = [i + 1 for i, (a, b) in enumerate(zip(src_timings, cand_timings))
            if a != b]
    if mism:
        print(f"FAIL timing mismatch at cues: {mism[:10]}"
              f"{' ...' if len(mism) > 10 else ''}")
        ok = False
    toomany = [i + 1 for i, (_, lines) in enumerate(cand) if len(lines) > MAX_LINES]
    if toomany:
        print(f"FAIL >2 lines at cues: {toomany[:10]}")
        ok = False
    longl = [(i + 1, len(ln)) for i, (_, lines) in enumerate(cand)
             for ln in lines if len(ln) > MAX_LINE]
    if longl:
        print(f"WARN {len(longl)} lines over {MAX_LINE} chars "
              f"(e.g. cue {longl[0][0]} = {longl[0][1]} chars)")
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


def _srt_text_only(path: Path):
    """Return [text, ...] (joined lines) per cue of an SRT."""
    return [" ".join(t) for _, t in parse_srt(path)]


def cmd_aligncheck(source, candidate):
    """Reference-free drift detector via multilingual embeddings."""
    import os
    try:
        from dotenv import load_dotenv
        from openai import OpenAI
    except ImportError:
        sys.exit("aligncheck needs python-dotenv + openai installed.")
    repo = Path(__file__).resolve().parents[2]
    load_dotenv(repo / ".env.local")
    load_dotenv(repo / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("aligncheck needs OPENAI_API_KEY in .env.local.")
    client = OpenAI()
    model = "text-embedding-3-large"

    tel = [t for _, _, t in parse_source(Path(source))]
    eng = _srt_text_only(Path(candidate))
    n = min(len(tel), len(eng))
    if len(tel) != len(eng):
        print(f"WARN cue count differs: source {len(tel)} vs candidate {len(eng)}")

    def embed(texts):
        vecs = []
        for i in range(0, len(texts), 128):
            r = client.embeddings.create(model=model, input=texts[i:i + 128])
            vecs.extend(d.embedding for d in r.data)
        return vecs

    def cos(a, b):
        s = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return s / (na * nb) if na and nb else 0.0

    te = embed([t if t.strip() else "." for t in tel[:n]])
    ee = embed([e if e.strip() else "." for e in eng[:n]])

    diag = [cos(te[i], ee[i]) for i in range(n)]
    drift = []
    for i in range(n):
        lo, hi = max(0, i - 2), min(n, i + 3)
        best = max(range(lo, hi), key=lambda j: cos(te[i], ee[j]))
        if best != i and cos(te[i], ee[best]) - diag[i] > 0.06:
            drift.append((i + 1, best - i, round(diag[i], 3),
                          round(cos(te[i], ee[best]), 3)))
    print(f"mean Telugu<->English alignment (pos i): "
          f"{round(sum(diag) / n * 100, 1)}%")
    print(f"drift-suspect cues (a shift fits better): {len(drift)}")
    for c, off, here, there in drift[:20]:
        print(f"  cue {c}: pos={here} but offset {off:+d} fits {there}")
    mean_pct = sum(diag) / n * 100
    if len(drift) > n * 0.30 or mean_pct < 18:
        print("VERDICT: SIGNIFICANT DRIFT - re-segmentation lost 1:1 "
              "alignment. Redo Pass 2 keeping one English block per source "
              "cue, in order, without merging or running ahead.")
    else:
        print("VERDICT: alignment looks healthy.")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "timings":
        cmd_timings(sys.argv[2])
    elif cmd == "scaffold":
        cmd_scaffold(sys.argv[2], sys.argv[3])
    elif cmd == "build":
        cmd_build(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "validate":
        cmd_validate(sys.argv[2], sys.argv[3])
    elif cmd == "aligncheck":
        cmd_aligncheck(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
