# Subtitle review notes — Chapter 05 EP01 (`UqggcPidPUU`)

These are spots in [`UqggcPidPUU.en.srt`](UqggcPidPUU.en.srt) that an
AI pass **cannot safely correct**. They need a reviewer with the Telugu
audio and/or a qualified Sri:vaishnava scholar (per the subtitle
guideline: non-Gi:tha Sanskrit verses must be verified by Vamsi Swami or
an equivalently qualified person). **Do not "fix" these from memory** —
verify against the audio.

## Already fixed (recited Bhagavad Gi:tha verses)

Corrected to the authoritative srikaryam text in
[`data/slokas_authoritative.json`](../data/slokas_authoritative.json):

| Cue | Verse | Now reads |
|-----|-------|-----------|
| 107 | Gi:tha 6.17 | `yuktha:ha:ra viha:rasya,` |
| 109 | Gi:tha 6.17 | `yuktha svapna:vabo:dha:sya,` |
| 397 | Gi:tha 4.19 | `yasya sarve: sama:rambha:ha,` |
| 398 | Gi:tha 4.19 | `ka:ma sankalpa varjitha:ha.` |

## Audio-verification log (2026-06-08)

Each item below was re-checked against the original Telugu audio
(`gpt-audio-1.5`, two stable passes per spot). Note: the audio-refined
SRT (`UqggcPidPUU.en.audio-refined.srt`) was re-segmented, so cue
NUMBERS differ from the original `.en.srt` — anchor by TIMESTAMP.

| # | Spot (timestamp) | Audio verdict | Action |
|---|---|---|---|
| 1 | Pu:rva se:sham (`00:11:12`) | ✅ faithful — `pu:rva se:sham` + "remainder of what was said earlier" | none |
| 4 | a:sraya do:sha (`00:18:11`) | ✅ faithful — "by joining such people, this thing is not spoiled"; old "Justice/Yudhishthira" garble gone | none |
| 5 | nimitta do:sha (`00:18:35`) | ✅ faithful — "an insect/ant/hair fell in"; old "Something fell ×3" gone | none |
| 6 | "head" (`00:20:11`) | ✅ faithful — Swami literally says `tala ko:rukunte: tala` ("desire a head, get a head") | none |
| 8 | Gi:tha 4.42 vocative (`00:26:38`) | ✅ fixed — `…utthishtta Bha:rata!` now reads as the vocative to Arjuna | none |
| 7 | `Ta:mar Shah Krishna` (`00:22:42`) | 🔴 confirmed garbled — mishearing of `tama:sha: Krishnudu`; AI gave 2 differing readings | replaced + tagged `[[REVIEW]]` |
| 2 | Chandogya a:ha:ra suddhi (`00:14:22`+) | ⚠️ non-Gi:tha Sanskrit — AI cannot be the authority | tagged `[[REVIEW]]` |
| 3 | ja:thi/a:sraya/nimitta do:sha (`00:16:52`) | ⚠️ non-Gi:tha terms — AI cannot be the authority | tagged `[[REVIEW]]` |

**Inline review tags:** the SRT now carries `[[REVIEW: …]]` markers at the
three open spots (#2, #3, #7). A reviewer can `grep '\[\[REVIEW'` to find
them, confirm against audio/scholar, then delete the tags before use.
Everything else is audio-verified and untagged.

## Original flags (for reference — see log above for current status)

### 1. Three-part naming garble — cues 180–183 (`00:11:12`–`00:11:19`)
`Purva Sesham.` repeated, then `Migulu.` left untranslated. The
three-part term Swami is naming was not captured cleanly. Confirm the
actual terms from audio.

### 2. Chandogya Upanishad "a:ha:ra suddhi" verse — cues 278–296 (`00:14:24`–`00:15:00`)
`Sattva Suddhi` / `Sattva Suddho` recited repetitively; the gloss
("Sattva means mind") and surrounding lines are semantically muddled.
This is the Chandogya 7.26.2 passage
(*a:ha:ra suddhau sattva suddhihi…*) — **not** a Gi:tha verse, so it is
not in the authoritative source. Verify wording + translation with the
audio and a qualified scholar.

### 3. ja:thi / a:sraya / nimitta do:sha passage — cue 321 (`00:16:52`) and 356–361
`Jati, asriya, nimitta, adushta, annat, kayasuddhihi` run together in
one cue; the later breakdown (cues ~356–361) is partly garbled. This is
the food-purity (do:sha) framework, not a Gi:tha verse — verify.

### 4. "Justice … Yudhishthira" non-sequitur — cues 342–343 (`00:18:11`–`00:18:17`)
`Justice should be given as per the example of Sri: Yudhishthira.`
repeated twice and reads as a non-sequitur in context. Likely a
mistranslation of the Telugu — confirm intended meaning.

### 5. "Something fell." ×3 — cues 350–352 (`00:18:35`–`00:18:39`)
Three identical cues on genuinely repeated audio timestamps, but the
English is almost certainly a mistranslation. Confirm what Swami says.

### 6. "If you desire a head, you will receive a head." — cue 373 (`00:20:08`)
Almost certainly a mistranslation. Confirm from audio.

### 7. `Ta:mar Shah Krishna,` — cue 412 (`00:22:42`)
Transliteration looks wrong / not a recognizable term. Confirm.

### 8. "the ultimate is Bharata." — cue 478 (`00:26:38`)
`Bha:rata` here is the vocative address to Arjuna, not the country/proper
noun. Reword once the audio meaning is confirmed.
