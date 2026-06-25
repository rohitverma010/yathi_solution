---
name: translate-timed-telugu
description: >-
  Translate an ALREADY-TIMED Telugu transcript (an .sbv or .srt whose
  cues/timestamps already exist) into editor-ready English subtitles for
  HH Sri Chinna Jeeyar Swamiji's discourses, using the proven two-pass
  "prose-first" method (translate the whole discourse as flowing English
  prose, THEN re-segment onto the existing timestamps). USE THIS SKILL
  when the user gives you a Telugu caption file plus a translation spec
  and asks to "create English subtitles", "translate these Telugu
  captions/SBV to English SRT", "make an English SRT from this transcript",
  or "match the gold/reference SRT as closely as possible". Do NOT use this
  to transcribe audio/YouTube from scratch (that is the sibling
  `subtitle-discourse` skill) or for chunking/embedding ingest.
  Works with any capable agent + an OpenAI API key; GPT-class models
  scored best in head-to-head benchmarking.
---

# Translate a timed Telugu transcript to English SRT (prose-first)

You are given a **timed Telugu transcript** (`.sbv` or `.srt`, cues and
timestamps already fixed) and a **translation specification**. Produce a
timestamp-identical English `.srt`.

## Why "prose-first" (this is the whole point)

Each Telugu cue is usually a **mid-sentence fragment**. Translating
cue-by-cue forces choppy, isolated English that reads unnaturally and drifts
from how a skilled human renders the passage. In a blind benchmark on a
human-approved reference, **prose-first beat cue-by-cue on every metric in a
single pass** (per-cue semantic 70.2 -> 71.8, word overlap 40.4 -> 42.6,
cues at "close" meaning 46 -> 54), and reached **~97% document-level
semantic similarity** to the gold file. It also matches how the reference
itself was authored. **Always translate the whole discourse as prose first,
then slice it back onto the timestamps.**

## Golden rule: timing is mechanical, never hand-typed

The English timestamps MUST be the source timestamps, unchanged. Never
re-type or re-time them. Use the helper:

```powershell
# from repo root, venv active
python subtitles/translate-timed-telugu/sbv_to_srt.py timings <source.sbv>
```

SBV->SRT timestamp rule (what the helper applies): `0:00:00.640,0:00:03.640`
becomes `00:00:00,640 --> 00:00:03,640` (pad hour to 2 digits, replace the
dot-before-milliseconds with a comma, join with ` --> `).

## Acharya discourse translation layer (MANDATORY)

HH Sri Chinna Jeeyar Swamiji's discourses are not ordinary lectures — they are
structured spiritual teachings. A common (but **not** fixed) progression is:

```
Question
  ↓
Practical example
  ↓
Scriptural principle
  ↓
Clarification
  ↓
Conclusion
```

Do not assume a fixed discourse structure, but **preserve whatever teaching
progression the section actually follows.** Preserve both *what* is being
taught and *how* it is being taught.

### Translate intent, not just sentences

Before translating a section, identify:

1. What question / point is Swamiji addressing?
2. What misunderstanding (if any) is he correcting?
3. What role does each example play?
4. What conclusion is he leading toward?

Then translate to preserve that progression. A translation **may differ from
the literal Telugu wording** when that is required to preserve the intended
teaching in natural English.

### English listener rule

The target audience is an educated English-speaking viewer. The goal is to help
them understand what Swamiji is teaching — **not** to demonstrate knowledge of
Telugu. When two renderings are equally faithful, always prefer natural English
over literal, Telugu-shaped English.

- Bad: `Dear Bhagavad-bandhus`
- Preferred: `Dear devotees`
  (unless Swamiji is specifically explaining the meaning of *Bhagavad-bandhu*).

### Technical Sanskrit vs ordinary Sanskrit

Preserve Sanskrit **only** when it functions as one of these:

- **A technical concept** — e.g. *A:thma, Parama:thma, Jna:na, Bhakthi, Karma,
  Yajna, Vive:ka, Pra:rabdha, A:ga:mi*.
- **A scriptural quotation** — e.g. *Yukta:a:ha:ra-viha:rasya*,
  *Brahma:rpanam Brahma Havih*.
- **A term being explicitly taught** — e.g. *Vive:ka* → "the ability to
  distinguish clearly".

Do **not** preserve Sanskrit merely because the Telugu speaker used a
Sanskrit-derived word. Translate those naturally:

- `grantham` → scripture
- `bandhu` → devotee
- `tattvam` → principle / reality (unless being taught as a technical term)

All preserved Sanskrit words, phrases, quotations, verses, and technical terms
are written in *italics* (spec v1.1).

### Technical-term introduction rule

The **first** time a technical Sanskrit term appears, introduce its meaning
(Sanskrit term italicized on line 1, Swamiji's English explanation on line 2):

```
Vive:ka
The ability to distinguish clearly
```

After it has been introduced, the bare term may be used on its own:

```
Vive:ka
```

Avoid re-explaining the same technical term every time it recurs.

### Example-preservation rule

Examples are never filler — every example serves a teaching purpose. When
translating:

1. Preserve the example.
2. Preserve the conclusion the example supports.
3. Preserve the rhetorical repetition that reinforces the lesson.

Do not compress an example merely because its meaning appears obvious.

### Conversational-filler removal rule

Remove Telugu conversational fillers that sound unnatural in English **unless**
they carry doctrinal meaning. Preserve the emotional force; drop the mechanical
Telugu structure.

- Avoid: `Poor man, he may do it too.`
- Preferred: `He may even do it.`

### Discourse-cohesion rule

A subtitle file is judged on three **equally important** axes:

1. Fidelity to the Telugu.
2. Fidelity to Swamiji's intended teaching.
3. Natural English flow.

A subtitle that is word-accurate but obscures the teaching is **inferior** to
one that conveys the teaching naturally and faithfully.

### Subtitle reviewer test

Before finalizing a section, ask:

> Would an English-speaking viewer with no Telugu knowledge understand what
> Swamiji is teaching?

If the answer is no, revise the subtitle — **not** the teaching. The subtitle.
The finished result should sound like Swamiji speaking in English, not like an
AI translating Telugu.

## Prerequisites

- Python 3.11+ venv at repo root (`.venv`), activated.
- `OPENAI_API_KEY` in `.env.local` (only needed if you run the optional
  embedding-based self-check; translation itself is done by you, the agent).
- The two inputs: the timed Telugu file and the spec (often a `.docx` — read
  it with `python-docx`).

## Procedure

### 1. Read the spec and the Telugu source

Read the full spec. The non-negotiable rules distilled from it:

- **Meaning-faithful, not word-literal.** Render what Swamiji *means*.
- **The Telugu is the ONLY source of truth.** Do not import outside Gi:tha
  knowledge or a remembered English translation. If a thought isn't in the
  Telugu, it isn't in the English.
- **Preserve Sanskrit quotations as transliteration** unless Swamiji himself
  explains them; translate the explanation, keep the quote transliterated.
- **Transliteration standard — colon AFTER a long vowel** (`a: e: i: o: u:`),
  per spec v1.1: `A:thma`, `Parama:thma`, `Jna:na`, `Bhakthi`, `Karma`,
  `Yajna`, `Vive:ka`, `Pra:rabdha`, `A:ga:mi`, `Bhagava:n`, `Krushna`,
  `Ra:ma`, `Si:tha`, `Go:vinda`, `Gi:tha`, `Sri:manna:ra:yana`. No
  trailing-`m` forms (`Ve:da` not `Ve:dam`). All Sanskrit terms and
  quotations are rendered in *italics*. (See the **Acharya discourse
  translation layer** section above for when to keep vs. translate Sanskrit.)
- **Preserve every example** (e.g. Prime Minister sweeping roads,
  Agniho:tra, food becoming strength), all **rhetorical repetition**, and
  all **questions** — do not smooth them away.
- **Prohibited:** do NOT add meanings, commentary, personal interpretations,
  or external scriptural explanations that are not present in the discourse.
  Only translate explanations Swamiji himself gives.
- **Layout:** max 2 lines per cue, roughly 32-42 characters per line.

### 2. PASS 1 — translate the whole discourse as prose

Translate the entire transcript into natural, faithful English **prose
paragraphs**, letting each sentence read as one complete thought. Do not
think about cue boundaries yet. Save it (e.g. `prose.txt`) so the
re-segmentation in step 3 has a stable source. Keep the discourse order
exactly as the Telugu.

### 3. PASS 2 — re-segment the prose onto the cues (use the scaffold)

Generate a numbered fill-in template so alignment is **structural** — you
write each cue's English directly beneath its own Telugu line, so you
physically cannot lose count or run ahead:

```powershell
python subtitles/translate-timed-telugu/sbv_to_srt.py scaffold <source.sbv> english_blocks.txt
```

This writes one slot per source cue:

```
@@@CUE 1 | 00:00:00,640 --> 00:00:03,640
@@@TEL ఆపదామపహర్తారం

@@@CUE 2 | 00:00:04,000 --> 00:00:07,960
@@@TEL దాతారం సర్వసంపదాం
```

Fill the blank line under each `@@@TEL` with **that cue's** English (1-2
lines, ~42 chars), taken from your Pass-1 prose. Leave the `@@@CUE`/`@@@TEL`
markers untouched. The English may carry natural sentence flow across cue
boundaries (a cue need not be a self-contained sentence) — but the text you
put under cue _i_ must convey the meaning of Telugu cue _i_, so a viewer
reads it while that Telugu is spoken.

**The #1 failure mode is DRIFT — guard against it.** If you let an early cue
swallow two cues' worth of content, every later cue silently shifts and the
whole file goes out of alignment (a fresh run of this skill once scored 32%
instead of 72% from exactly this mistake). The scaffold prevents most of it;
also:

- Fill **every** slot; never merge two cues' English into one slot or leave a
  slot empty (the helper rejects both).
- Cue _i_ covers the **same span of meaning** the Telugu cue _i_ covers —
  do **not** pull a later cue's content forward, do **not** run ahead.
- If a Telugu cue is a short fragment, its English is a short fragment too.
  Matching the source's pacing matters more than a tidy sentence.
- Sanity-anchor as you go: at cues 1, 64, 128, 192, 256 confirm the English
  in that slot matches the Telugu on its `@@@TEL` line.

Then stitch the filled scaffold onto the exact timings:

```powershell
python subtitles/translate-timed-telugu/sbv_to_srt.py build <source.sbv> english_blocks.txt out.srt
```

(`build` also accepts a plain blank-line-separated file of N blocks if you
prefer, but the scaffold is strongly recommended — it makes the 1:1 mapping
impossible to get wrong.)

### 4. Validate layout + timing (must PASS)

```powershell
python subtitles/translate-timed-telugu/sbv_to_srt.py validate <source.sbv> out.srt
```

This checks: cue count equals the source, every timing matches the source in
order, no cue exceeds 2 lines, and flags any line over 42 chars. Fix any
FAIL and re-run. Do not deliver an SRT that does not PASS.

### 5. Alignment self-check — REQUIRED (catches drift, no reference needed)

`validate` only proves the timings are intact; it cannot see whether the
English *content* drifted off its cue. Always run the reference-free drift
detector (Telugu<->English via multilingual embeddings):

```powershell
python subtitles/translate-timed-telugu/sbv_to_srt.py aligncheck <source.sbv> out.srt
```

- A healthy file prints `VERDICT: alignment looks healthy.` (mean cross-
  lingual alignment ~22%+ on this material; cross-lingual cosine is low in
  absolute terms — judge by the VERDICT, not the raw %).
- `VERDICT: SIGNIFICANT DRIFT` means an early cue swallowed content and the
  file shifted. The listed cues show which way it slipped (`offset -1/-2`).
  **Redo Pass 2** keeping strict 1:1 alignment, then re-check. Do not deliver
  a file that reports DRIFT.

### 6. (Optional) Self-check against a gold reference

If — and only if — a human-approved reference SRT exists AND the user wants a
closeness score, you may measure similarity with an embedding scorer
(OpenAI `text-embedding-3-large`, per-cue cosine with a +/-1 drift window).
**The translator must never read the reference; scoring is a separate,
after-the-fact step.** Report BOTH numbers honestly:

- **Per-cue** semantic % (strict; depressed by fragment segmentation).
- **Document-level** semantic % (the true accuracy signal; expect ~95%+ for
  a faithful translation even when per-cue sits near 70%).

A literal 100% match to a specific reference is **not achievable blind and
is not the goal** — many translations are equally correct. Target: a
reviewer rates it as accurate as the reference (document-level ~97%).

### 7. Final quality review (spec v1.1 checklist)

Before declaring the SRT done, confirm every item:

- [ ] No missing examples — every example in the Telugu is present.
- [ ] No invented explanations — nothing added beyond what Swamiji says.
- [ ] Sanskrit consistency — same term spelled the same way throughout
      (per the transliteration standard), and italicized.
- [ ] Terminology consistency — technical terms used consistently;
      introduced once, then used bare.
- [ ] Natural English — reads naturally, not Telugu-shaped.
- [ ] Teaching intent preserved — what *and* how Swamiji teaches is intact.
- [ ] Philosophical distinctions preserved — no flattening of doctrinal
      precision.
- [ ] Example-to-teaching alignment — each example still supports the
      conclusion it was given for.

**Final reviewer test:** the result should sound like Swamiji speaking in
English, not like an AI translating Telugu. If it fails, revise the
subtitle — not the teaching.

## Deliverables

- The English `.srt` (timestamp-identical to the source, validated).
- The intermediate `prose.txt` (useful as a prose transcript and for review).

## Anti-cheat (when benchmarking models)

If you are comparing models against a reference: each model reads ONLY the
spec + Telugu source (and its own prior round), never a sibling model's
output and never the reference. After each round, check pairwise
identical-cue counts across models to detect peeking (independent outputs
share ~0-1/256 identical cues; contamination shows up as dozens).
