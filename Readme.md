# Subtitles

Editor-ready **English subtitles** (`.srt`) for HH Sri Chinna Jeeyar
Swamiji's Telugu discourses, plus everything needed to (re)produce them.
This folder is self-contained: the generation wrapper, the authoritative
Bhagavad Gi:tha verses, the verse-correction tool, the dependency list,
and the finished output all live here.

## What's in this folder

| File | Purpose |
| --- | --- |
| [`make_subtitles.py`](make_subtitles.py) | One-command entry point: transcribe → refine → **audio-refine** → correct verses → write `.srt`. |
| [`sloka_normalize.py`](sloka_normalize.py) | Corrects recited Gi:tha verse lines to the authoritative text (stdlib only). |
| [`slokas_authoritative.json`](slokas_authoritative.json) | The 700 Bhagavad Gi:tha verses (srikaryam.com), Prajna colon transliteration. |
| [`requirements.txt`](requirements.txt) | Python deps for the workflow. |
| [`SKILL.md`](SKILL.md) | Agent recipe: generate the SRT *and* do the audio-dependent review triage (inline `[[REVIEW]]` tags). |
| [`translate-timed-telugu/SKILL.md`](translate-timed-telugu/SKILL.md) | Agent recipe for the **other** input shape: translate an already-timed Telugu transcript (`.sbv`/`.srt`) to English via the proven **prose-first** method. |
| [`translate-timed-telugu/sbv_to_srt.py`](translate-timed-telugu/sbv_to_srt.py) | Mechanical timing helper for that skill: extract source timings, stitch English blocks onto them, and validate cue count/timing/layout (no translation). |
| `*.en.srt` | Finished subtitle files (output). Suspect cues carry inline `[[REVIEW: …]]` tags. |
| `*.review-notes.md` | Legacy per-video audit list (opt-in via `--review-notes`; superseded by inline tags). |

### Two subtitle skills, two starting points

- **`subtitle-discourse`** ([`SKILL.md`](SKILL.md)) — start from **audio/YouTube**;
  transcribe → refine → audio-refine → verse-correct.
- **`translate-timed-telugu`** ([`translate-timed-telugu/SKILL.md`](translate-timed-telugu/SKILL.md)) —
  start from an **already-timed Telugu transcript** plus a spec; translate to
  English using prose-first (translate the whole discourse as prose, then
  re-segment onto the existing timestamps). In blind benchmarking this beat
  cue-by-cue translation on every metric and reached ~97% document-level
  similarity to a human-approved reference. Encodes the **Acharya discourse
  translation layer** from spec v1.1: translate teaching *intent* (not just
  sentences), keep Sanskrit only when it is a technical term / scriptural
  quote / term being taught (italicized), preserve examples and rhetorical
  repetition, and pass the reviewer test ("sounds like Swamiji speaking in
  English, not an AI translating Telugu").

The heavy lifting (YouTube download, Whisper transcription, the gpt-4o
refine pass, and the gpt-audio-1.5 audio-refine pass) is shared
infrastructure in
[`ingest/transcript_to_srt.py`](../ingest/transcript_to_srt.py);
`make_subtitles.py` just calls it and drops the result here.

## How someone else reproduces this

### 1. Prerequisites

* **Python 3.11+** (developed on 3.14).
* **ffmpeg** on your `PATH` (yt-dlp uses it to chunk audio):
  * Windows: `winget install Gyan.FFmpeg`
  * macOS: `brew install ffmpeg`
  * Linux: `apt-get install ffmpeg`
* An **OpenAI API key** with access to `whisper-1`, `gpt-4o`, and
  `gpt-audio-1.5` (the default audio-refine pass; not needed if you run
  with `--no-refine-audio`).

### 2. Set up the environment (from the repo root)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1            # Windows PowerShell
# source .venv/bin/activate           # macOS / Linux
pip install -r subtitles/requirements.txt
```

### 3. Provide the API key

Create `.env.local` in the repo root (it is git-ignored):

```
OPENAI_API_KEY=sk-...
```

The pipeline auto-loads `.env.local` (then `.env`) via `python-dotenv`.

### 4. Generate a subtitle

```powershell
python subtitles/make_subtitles.py https://youtu.be/UqggcPidPUU
# or just the 11-char id:
python subtitles/make_subtitles.py UqggcPidPUU
```

This writes `subtitles/<video_id>.en.srt`. By **default** it runs three
passes — `whisper-1` timing → `gpt-4o` text refine → **`gpt-audio-1.5`
audio-grounded re-translation** (the `--refine-audio` pass, on by default).
The audio pass listens to the original Telugu in ~45 s windows and, behind a
diff-gate, corrects confident whisper mistranslations the text-only refine
cannot see; cue boundaries stay fixed (only words change). The raw
transcript is cached under `cache/transcripts/<id>.json` and the refined
transcript under `cache/transcripts/<id>.en.refined.json`, so re-runs cost
nothing unless you pass `--force-refine`.

Spots the pipeline cannot safely fix are marked **inline** in the `.srt`
with `[[REVIEW: …]]` tags during the agent review pass (see
[Human review pass](#human-review-pass)) — a reviewer can be handed that one
file, `grep` the tags, confirm them against the audio, and delete them.

Useful flags:

| Flag | Effect |
| --- | --- |
| `--no-refine-audio` | Skip the `gpt-audio-1.5` audio pass (faster/cheaper, text-only, lower accuracy). |
| `--force-refine` | Re-run the gpt-4o refine pass even if a cached version exists. |
| `--no-sloka-normalize` | Skip the authoritative Gi:tha verse correction. |
| `--review-notes` | Also scaffold a legacy `<id>.review-notes.md` audit stub (default is inline tags only). |
| `--force-review-notes` | With `--review-notes`, overwrite an existing stub. |
| `--out PATH` | Write the `.srt` somewhere other than `subtitles/<id>.en.srt`. |

You can also call the underlying pipeline directly:

```powershell
python ingest/transcript_to_srt.py UqggcPidPUU --refine --refine-audio \
    --out subtitles/UqggcPidPUU.en.srt
```

## How it works

1. **Timing** — Whisper *translations* (`whisper-1`,
   `response_format="verbose_json"`) give real per-segment timestamps.
2. **Wording (`--refine`)** — the literal English is sent to `gpt-4o`
   for a *boundary-preserving* cleanup. It may fix grammar, proper
   nouns, and transliteration, but must return exactly one polished
   string per input segment, so timestamps never move. Output is cached
   as `cache/transcripts/<id>.en.refined.json`.
3. **Audio re-translation (`--refine-audio`, default on)** — each cue is
   re-checked against the **original Telugu audio** with `gpt-audio-1.5`
   in ~45 s windows. Behind a diff-gate (only sufficiently different,
   confident rewrites are accepted) it corrects whisper mistranslations
   the text-only pass cannot detect. Cue boundaries are held fixed — only
   the words change. Skip with `--no-refine-audio`.
4. **Verse correction** — `sloka_normalize.py` rewrites any cue that is
   confidently a recited Bhagavad Gi:tha verse fragment to the
   authoritative srikaryam spelling (see below).
5. **Cue hygiene** — segments are re-packed into broadcast-style cues
   (≤42 chars/line, ≤2 lines, 1–7 s, no overlaps).

### Guideline encoding (the refine prompt)

The `--refine` pass encodes the VT Seva subtitling standards from
[`docs/Subtitles Guidelines and Process.docx`](../docs/Subtitles%20Guidelines%20and%20Process.docx)
and [`docs/Translation_Reference.docx`](../docs/Translation_Reference.docx):
natural English (not verbatim), *Prajna* colon transliteration
(`Ve:da`, `slo:ka`, `Na:ra:yana`), no trailing `m` on `Ve:da`/`sa:sthra`,
plain-`s` plurals (`Pa:ndavas`, `Ve:das`), recited slo:kas left
untranslated, and the Telugu→English glossary for common terms. Italics
are omitted because `.srt` cannot render them; brackets `[ ]`, quotes,
and single quotes for naming are preserved as plain text.

It also optimizes for **natural flow between cues** — viewers reading
without audio should see one connected speaker, not a list of
disconnected lines. Continuation cues start lowercase, run-on cues end
with a comma (full stops only where a thought ends), light connectives
bridge cause-and-effect, pronouns thread recurring referents, and
adjacent near-duplicate machine lines are rephrased. Each request also
sees the previous few finalized cues and next few raw cues as read-only
context so flow carries across batch boundaries. A batch that returns a
mismatched segment count is split and retried (down to a single segment)
before any cue falls back to raw machine text, so timing stays aligned.

### Authoritative Gi:tha verse correction

Whisper + gpt-4o get *timing* right but drift on the spelling of recited
Sanskrit slo:kas (e.g. `Yukta Aha:ra Viha:rasya` instead of the
authoritative `yuktha:ha:ra viha:rasya`). `sloka_normalize.py` fixes
**only** recited Bhagavad Gi:tha fragments, against the 700 verses in
[`slokas_authoritative.json`](slokas_authoritative.json) (the `slokas`
map, keyed `"chapter.verse"`, sourced from srikaryam.com — the Gi:tha
English Mu:lam blessed by HH Thridandi Chinna Sri:manna:ra:yana
Ra:ma:nuja Ji:yar Swa:miji).

It is **conservative by design** (the project rule is *never guess*):

* A cue is rewritten only when its letters map to a **compact,
  contiguous run** inside a single verse — i.e. it really is a recited
  fragment. Two gates must both pass: *coverage* (the cue is almost
  fully explained by the verse) and *density* (the matched region of
  the verse is tightly packed, not smeared across it). English prose
  fails the density gate and is never touched.
* This is a **spelling** correction of an already-recognized verse, not
  a translation step and not a guess.
* Heavily mis-transcribed verse fragments may fall below the threshold
  and are deliberately left for human review rather than risk a wrong
  rewrite.
* Non-Gi:tha Sanskrit (Upanishads, do:sha frameworks, etc.) is **not**
  in this source and is never altered — those go in the review notes.

Run it standalone (dry-run report by default):

```powershell
# Report what would change (no writes):
python subtitles/sloka_normalize.py subtitles/UqggcPidPUU.en.srt

# Apply in place:
python subtitles/sloka_normalize.py cache/transcripts/UqggcPidPUU.en.refined.json --apply
```

## Human review pass

Every subtitle still needs a human pass for Sanskrit terms, proper
nouns, and any non-Gi:tha verse. The spots an automated pass cannot
safely fix (they need the Telugu audio, or a qualified Sri:vaishnava
scholar for non-Gi:tha Sanskrit) are marked **inline in the `.srt`** with
`[[REVIEW: …]]` tags during the agent review pass. A reviewer can be handed
that single file, find every open item with
`grep '\[\[REVIEW' subtitles/<id>.en.srt`, confirm each against the audio,
and delete the tag before final use. (Anchor by **timestamp**, not cue
number — the audio-refine pass may re-segment cues.)

The generation steps need **no specific AI model** — anyone with the repo
and an OpenAI key just runs `make_subtitles.py`. A coding agent only adds
value for the review triage: listening to the audio (slice + `gpt-audio-1.5`)
to decide which lines to tag, and writing the `[[REVIEW]]` tags. That
agent-assisted procedure is captured in [`SKILL.md`](SKILL.md) so any
capable agent (not just the one that built this) can repeat it. The legacy
`*.review-notes.md` audit file is still available on demand via
`--review-notes`, but inline tags are the default deliverable.

## Files

| File | Source video | Title | Review notes |
| --- | --- | --- | --- |
| [UqggcPidPUU.en.srt](UqggcPidPUU.en.srt) | https://youtu.be/UqggcPidPUU | Chapter 05 EP01 | [notes](UqggcPidPUU.review-notes.md) |
| [gita-purpose-structure.en.srt](gita-purpose-structure.en.srt) | local timed Telugu captions (`captions Telugu.sbv.txt`, spec v1.1) | Bhagavad Gi:tha — purpose & three-Shatkam structure (ch. 1–4 lead-in) | [prose](gita-purpose-structure.prose.txt) |
