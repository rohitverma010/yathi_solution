---
name: subtitle-discourse
description: >-
  Generate editor-ready English .srt subtitles for HH Sri Chinna Jeeyar
  Swamiji's Telugu discourses in the ask-jeeyar repo, then do the
  agent-assisted human-review triage that the automated pipeline cannot
  do alone. USE THIS SKILL when the user asks to "make subtitles",
  \"generate an SRT\", \"subtitle this discourse / video\", \"review/verify
  the subtitles\", or \"fix slokas in the subtitles\". Works with ANY
  capable coding agent + an OpenAI API key — no specific model required.
  The transcription/wording runs on OpenAI (whisper-1 timing + gpt-4o
  refine + gpt-audio-1.5 audio-grounded re-translation, on by default);
  the agent's job is orchestration + the audio-dependent review pass,
  which marks suspect cues with inline [[REVIEW: ...]] tags in the .srt.
  Do NOT use for non-subtitle ingest (chunking/embedding) or for editing
  the website.
---

# Subtitle a Telugu discourse (ask-jeeyar)

This skill produces a timestamp-accurate English `.srt` for one of HH Sri
Chinna Jeeyar Swamiji's Telugu discourses, then captures the spots a
machine cannot safely fix as inline `[[REVIEW: ...]]` tags **in the `.srt`
itself** so a reviewer can be handed that one file, confirm each tag
against the audio/a scholar, and delete it.

**The mechanical pipeline needs no agent and no Claude/Opus** — it runs on
OpenAI (`whisper-1` for timing, `gpt-4o` for wording, and by default
`gpt-audio-1.5` for an audio-grounded re-translation pass) plus stdlib-only
verse correction. The agent's value is in **step 4 (review triage)**:
listening to the original Telugu audio to decide which non-Gi:tha Sanskrit
lines, garbled cues, or suspect proper nouns to tag for a human. Everything
else is `python subtitles/make_subtitles.py`.

## Prerequisites

- Python 3.11+ venv at the repo root (`.venv`), activated.
- `ffmpeg` on `PATH`.
- `OPENAI_API_KEY` in `.env.local` at the repo root (git-ignored).
- `pip install -r subtitles/requirements.txt` already done.

If any are missing, follow [subtitles/README.md](README.md) → "How
someone else reproduces this", then continue.

## Procedure

### 1. Generate the SRT

```powershell
python subtitles/make_subtitles.py <youtube-id-or-url>
# -> subtitles/<id>.en.srt
```

This is the **standard workflow**. By default it runs three passes:
`whisper-1` timing -> `gpt-4o` text refine -> **`gpt-audio-1.5`
audio-grounded re-translation** (`--refine-audio`, on by default). The audio
pass listens to the original Telugu in ~45 s windows and, behind a diff-gate,
corrects confident whisper mistranslations the text-only refine cannot see
(e.g. a cue heard as an unrelated English phrase). Whisper cue boundaries are
held fixed throughout — only words change, never timing.

The raw + refined transcripts cache under `cache/transcripts/<id>*.json`,
so re-runs are free unless you pass `--force-refine`. Authoritative
Bhagavad Gi:tha verse spelling is corrected automatically by
`sloka_normalize.py` (skip with `--no-sloka-normalize`).

Useful flags:

- `--no-refine-audio` — skip the `gpt-audio-1.5` pass for a faster/cheaper
  text-only run (use only when audio cost is a concern; quality is lower).
- `--review-notes` — also scaffold the legacy `<id>.review-notes.md` audit
  stub. **Not the default** — the standard review output is inline
  `[[REVIEW: ...]]` tags (steps 4–5).

### 2. Sanity-check the output

- Confirm cue count > 0 and the file ends with a complete cue.
- Spot-check a few cues for sequential, non-overlapping timestamps.
- Confirm *Prajna* colon transliteration is present (e.g. `Ve:da`,
  `Na:ra:yana`) and there are no stray trailing-`m` forms
  (`Ve:dam`/`sa:sthram`).

### 3. Confirm the automatic Gi:tha corrections

Run the normalizer in dry-run against the final SRT and record what it
recognized:

```powershell
python subtitles/sloka_normalize.py subtitles/<id>.en.srt
```

Any line it rewrote is a recited Bhagavad Gi:tha fragment matched to
`subtitles/slokas_authoritative.json`. List these under
"Already fixed" in the review notes. **Never hand-edit a Gi:tha verse
from memory** — only the authoritative srikaryam text is allowed.

### 4. Agent-assisted review triage (the part that needs you)

Read through the SRT and find every line the pipeline could **not** safely
fix. These are audio-dependent: **verify by listening to the original Telugu
audio — do NOT eyeball or guess** (the project's absolute rule). For each
suspect cue, slice the audio at that cue's timestamps and transcribe/
translate it with `gpt-audio-1.5` to confirm what the Swami actually says:

- Treat the **audio as the source of truth**. Phrase the prompt as a
  "faithful translator, the audio is the source of truth" — avoid the word
  "verbatim," which tends to trigger a boilerplate refusal.
- `gpt-audio-1.5` intermittently returns a refusal ("Sorry, I can't … without
  hearing the audio") even when it did hear it. **Retry (4–6 attempts) and
  require 2+ stable passes** that agree before trusting a reading.
- If the audio confirms the cue is already faithful, **leave it untouched**.
- If a cue is genuinely garbled and the audio gives a clear reading, fix the
  text **and** add a `[[REVIEW]]` tag noting the audio basis.
- If even the audio is ambiguous, or the line is non-Gi:tha Sanskrit, leave
  the best-effort text and tag it for a human/scholar — never assert.

Categories to look for:

- **Non-Gi:tha Sanskrit verses** (Upanishad mantras, do:sha / food-purity
  frameworks, sto:tra lines). These are not in the authoritative source,
  so the normalizer leaves them untouched, and AI cannot be the authority —
  tag for a qualified Sri:vaishnava scholar (Vamsi Swami or equivalent).
- **Heavily-drifted Gi:tha fragments** the normalizer deliberately
  skipped (failed its coverage/density gates). Note the suspected verse
  but leave the text as captured.
- **Repeated/identical cues** that read as machine artifacts
  (e.g. "Something fell." ×3).
- **Likely mistranslations / non-sequiturs** (a line that doesn't follow
  in context).
- **Suspect transliterations / proper nouns** (a name or term that isn't
  recognizable, or a vocative like `Bha:rata` mis-read as the country).

### 5. Tag the SRT inline (the deliverable)

The review output is **inline `[[REVIEW: ...]]` tags written directly into
the `.srt`** — so the reviewer can be handed that single file, `grep`
the tags, confirm each against the audio, and delete it before final use.
There is **no separate notes file** in the standard workflow.

Append the tag to the affected cue's text (it renders on-screen in players,
which is intentional — the reviewer can't miss it):

```
318
00:16:52,000 --> 00:16:55,000
Ja:thi, a:sraya, nimitta, adushta, annat,
ka:ya suddhi— [[REVIEW: non-Gi:tha do:sha terms run together; confirm
wording + translation with a qualified scholar]]
```

Guidelines for the tags:

- One tag per issue, on the cue it applies to. State **what is suspect**,
  the **audio basis** (what the audio actually says, if you verified it),
  and **who must confirm** (audio reviewer vs. Sri:vaishnava scholar).
- A reviewer finds them all with `grep '\[\[REVIEW' subtitles/<id>.en.srt`.
- **Anchor by timestamp, not cue number.** The audio-refine pass may
  re-segment cues, so cue numbers can differ from any earlier `.en.srt`;
  always reference the `hh:mm:ss` timestamp.

(If someone explicitly wants the legacy audit file too, regenerate the SRT
with `--review-notes`; it is not part of the default workflow.)

### 6. Update the Files table

Add a row to the Files table in [README.md](README.md) linking the new
`<id>.en.srt`, its source video, the episode title, and its review notes.

## Hard rules

- **Never fabricate sloka text.** Gi:tha corrections come only from
  `slokas_authoritative.json`; everything else gets flagged, not guessed.
- **Conservative is correct.** If the normalizer skipped a verse, do not
  lower thresholds or hand-rewrite it — that's a `[[REVIEW]]` tag.
- **Timestamps never move.** The refine and audio-refine passes are
  boundary-preserving; do not merge/split cues to "improve" wording.
- **Repo hygiene** (per `.github/copilot-instructions.md`): commit author
  `vtsseattle`; `gh auth switch --user vtsseattle` before any push; do
  NOT stage `scratch/`, `cache/`, `docs/ask-test-run/`, or
  `ask-jeeyar.code-workspace`; update the README with every change.
