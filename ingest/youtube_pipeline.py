"""YouTube → English-translated transcript pipeline.

Ported from the ytchat prototype (pipeline.py + retrieval.chunk_transcript)
so that video ingest is self-contained in this repo. Used by
`python -m ingest.add_video`.

Per video (cached by YouTube video ID under cache/):
    URL -> yt-dlp audio -> ffmpeg chunks (15 min, 16 kHz mono mp3)
        -> OpenAI Whisper translations endpoint (any language -> English)
        -> stitched transcript JSON on disk.

We use the translations endpoint (not transcriptions) because:
  * OpenAI's transcription endpoint rejects Telugu (`language="te"`) and
    auto-detect mis-routes Telugu audio into Hindi/Gujarati scripts producing
    unusable phonetic mangling.
  * The translations endpoint accepts the same audio and outputs English
    directly, which is what we want for the chat UX anyway.
  * Shloka fidelity is the documented tradeoff.

The transcript JSON shape (cached in cache/transcripts/<video_id>.json):
    {
        "video_id": str,
        "url": str,
        "title": str,
        "duration": float,
        "language": "en",
        "segments": [{"start": float, "end": float, "text": str}, ...],
    }
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from openai import OpenAI

from common import CACHE_DIR, TRANSCRIPT_DIR

# NOTE: yt_dlp is imported lazily inside _download_audio so simply importing
# this module doesn't require yt-dlp on the import path (e.g. when only the
# chunk_transcript helper is used in tests).

AUDIO_DIR = CACHE_DIR / "audio"
CHUNK_SECONDS = 900  # 15 minutes per chunk -> ~3.6 MB at 32 kbps mono mp3

# Transcript chunking knobs (must match what produced the existing 604 chunks
# in data/chunks.jsonl, otherwise re-embedding would drift IDs).
TARGET_CHUNK_TOKENS = 250
CHUNK_OVERLAP_SEGMENTS = 3

_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})")

ProgressCB = Optional[Callable[[str], None]]


# --- video URL helpers -------------------------------------------------------


def extract_video_id(url: str) -> str:
    """Return the 11-char YouTube video ID from any standard YouTube URL."""
    m = _VIDEO_ID_RE.search(url)
    if not m:
        raise ValueError(f"Could not extract YouTube video ID from URL: {url}")
    return m.group(1)


# --- transcript pipeline -----------------------------------------------------


def get_transcript(
    url: str,
    *,
    progress_cb: ProgressCB = None,
) -> dict:
    """Return the English-translated transcript dict for the given YouTube URL.

    Audio is fetched + chunked once and cached on disk. Translation is also
    cached once written, so subsequent calls are instant.
    """
    _ensure_dirs()
    video_id = extract_video_id(url)
    tpath = _transcript_path(video_id)
    if tpath.exists():
        return json.loads(tpath.read_text(encoding="utf-8"))

    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg not found on PATH. Install ffmpeg "
            "(Windows: `winget install Gyan.FFmpeg`) and try again."
        )

    audio_path, info = _download_audio(url, video_id, progress_cb)
    chunks = _chunk_audio(audio_path, video_id, progress_cb)
    client = OpenAI()
    segments = _translate_all(client, chunks, progress_cb)

    transcript = {
        "video_id": video_id,
        "url": url,
        "title": info.get("title", ""),
        "duration": float(info.get("duration") or 0.0),
        "language": "en",
        "segments": segments,
    }
    tpath.write_text(
        json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return transcript


# --- transcript chunking (mirrors ytchat/retrieval.chunk_transcript) ---------


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def chunk_transcript(
    transcript: dict,
    *,
    target_tokens: int = TARGET_CHUNK_TOKENS,
    overlap_segments: int = CHUNK_OVERLAP_SEGMENTS,
) -> list[dict]:
    """Group adjacent Whisper segments into ~target_tokens windows with overlap.

    Returns chunk dicts without `chunk_id` -- the caller assigns those during
    persistence so they stay stable across the corpus.
    """
    video_id = transcript["video_id"]
    segments = transcript.get("segments") or []
    out: list[dict] = []
    cur: list[dict] = []
    cur_tokens = 0
    for seg in segments:
        t = _approx_tokens(seg["text"])
        if cur and cur_tokens + t > target_tokens:
            out.append(_finalize(video_id, cur))
            cur = cur[-overlap_segments:] if overlap_segments > 0 else []
            cur_tokens = sum(_approx_tokens(s["text"]) for s in cur)
        cur.append(seg)
        cur_tokens += t
    if cur:
        out.append(_finalize(video_id, cur))
    return out


def _finalize(video_id: str, segs: list[dict]) -> dict:
    return {
        "video_id": video_id,
        "start": float(segs[0]["start"]),
        "end": float(segs[-1]["end"]),
        "text": " ".join(s["text"].strip() for s in segs if s.get("text")).strip(),
    }


# --- internals ---------------------------------------------------------------


def _ensure_dirs() -> None:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)


def _transcript_path(video_id: str) -> Path:
    return TRANSCRIPT_DIR / f"{video_id}.json"


def _chunk_dir(video_id: str) -> Path:
    return AUDIO_DIR / video_id


def _ydl_cloud_opts() -> dict:
    """yt-dlp options for authenticated YouTube access.

    Priority order:
      1. YT_COOKIES env var (CI / server deployments) — written to a temp
         file and passed as cookiefile.
      2. Local browser cookies (dev machines) — yt-dlp reads Chrome's live
         cookie store directly; always fresh, never stale.
    Only the stable `web` player client is used — ios and tv_embedded both
    require PO Tokens or are outright blocked, causing "format not available".
    """
    opts: dict = {
    "extractor_args": {
        "youtube": {"player_client": ["mweb", "android"]},
    },
}
    cookies_blob = os.environ.get("YT_COOKIES")
    if cookies_blob:
        # yt-dlp wants a filesystem path; write the blob to a temp file.
        tf = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        tf.write(cookies_blob)
        tf.close()
        opts["cookiefile"] = tf.name
    else:
        local_cookiefile = os.environ.get("YT_COOKIEFILE")
        if local_cookiefile:
            opts["cookiefile"] = local_cookiefile
        else:
            local_cookie_path = Path(__file__).with_name("youtube_cookies.txt")
            if local_cookie_path.exists():
                opts["cookiefile"] = str(local_cookie_path)
    return opts


def _download_audio(
    url: str, video_id: str, progress_cb: ProgressCB
) -> tuple[Path, dict]:
    """Download bestaudio for the video. Returns (audio_path, yt-dlp info dict)."""
    # Skip yt-dlp partial-download artifacts (`*.part`, `*.ytdl`) — treating
    # them as a valid cache made the next run feed a 318-byte stub into
    # ffmpeg and Whisper, which silently produced an undecodable mp3.
    _IGNORED_SUFFIXES = {".json", ".part", ".ytdl", ".tmp"}
    existing = [
        p
        for p in AUDIO_DIR.glob(f"{video_id}.*")
        if p.is_file() and p.suffix.lower() not in _IGNORED_SUFFIXES
    ]
    if existing:
        import yt_dlp  # lazy

        with yt_dlp.YoutubeDL(
            {
                "quiet": True,
                "skip_download": True,
                "no_warnings": True,
                **_ydl_cloud_opts(),
            }
        ) as ydl:
            info = ydl.extract_info(url, download=False)
        return existing[0], info

    if progress_cb:
        progress_cb("Downloading audio from YouTube...")

    # Sweep stale partials from a previous failed/aborted run so the next
    # attempt resumes/restarts cleanly instead of seeing a 318-byte stub.
    for stale in AUDIO_DIR.glob(f"{video_id}.*"):
        if stale.is_file() and stale.suffix.lower() in _IGNORED_SUFFIXES - {".json"}:
            try:
                stale.unlink()
            except OSError:
                pass

    # import yt_dlp  # lazy: only the ingest CLI ever reaches this path

    # ydl_opts = {
    #     # Prefer audio-only m4a (smallest, ffmpeg can stream-copy the audio
    #     # track). For videos where YouTube only serves muxed progressive
    #     # formats to this player client, fall back to `best` -- ffmpeg's -vn
    #     # in _chunk_audio drops the video stream cheaply.
    #     "format": "bestaudio[ext=m4a]/bestaudio/best",
    #     "outtmpl": str(AUDIO_DIR / f"{video_id}.%(ext)s"),
    #     "quiet": True,
    #     "no_warnings": True,
    #     **_ydl_cloud_opts(),
    # }
    # with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    #     info = ydl.extract_info(url, download=True)
        
    import yt_dlp

# ... (Assuming AUDIO_DIR, video_id, and _ydl_cloud_opts are defined)

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(AUDIO_DIR / f"{video_id}.%(ext)s"),
        "quiet": True,
        "no_warnings": False,
        # COMMENTED OUT: YouTube recently broke these specific clients
        # "extractor_args": {"youtube": {"player_client": ["ios", "mweb"]}},
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "m4a",
        }],
        **_ydl_cloud_opts(),
    }


    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        # Wrapping in a try-except block handles future extraction failures gracefully
        try:
            info = ydl.extract_info(url, download=True) 
        except yt_dlp.utils.DownloadError as e:
            print(f"Failed to download {url}: {e}")

        paths = [
            p
            for p in AUDIO_DIR.glob(f"{video_id}.*")
            if p.is_file() and p.suffix.lower() not in _IGNORED_SUFFIXES
        ]
        if not paths:
            raise RuntimeError(f"yt-dlp did not produce an audio file for {video_id}")
        return paths[0], info


def _chunk_audio(
    audio_path: Path, video_id: str, progress_cb: ProgressCB
) -> list[Path]:
    """Split + downmix audio to 16 kHz mono 32 kbps mp3 chunks of CHUNK_SECONDS each."""
    chunk_dir = _chunk_dir(video_id)
    chunk_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(chunk_dir.glob("chunk_*.mp3"))
    if existing:
        return existing

    if progress_cb:
        progress_cb("Splitting audio into 15-minute chunks...")

    cmd = [
        "ffmpeg", "-y", "-i", str(audio_path),
        # -vn drops any video stream BEFORE decode. Without this, a muxed
        # mp4 input (video+audio) makes ffmpeg decode all video frames just
        # to throw them away -- turning a ~10s chunking job into a 15+ min
        # CPU-pegged one.
        "-vn",
        "-ac", "1", "-ar", "16000", "-b:a", "32k",
        "-f", "segment", "-segment_time", str(CHUNK_SECONDS),
        "-reset_timestamps", "1",
        str(chunk_dir / "chunk_%03d.mp3"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{proc.stderr[-1000:]}")

    chunks = sorted(chunk_dir.glob("chunk_*.mp3"))
    if not chunks:
        raise RuntimeError("ffmpeg produced no audio chunks")
    return chunks


def _translate_chunk(client: OpenAI, chunk_path: Path) -> list[dict]:
    with open(chunk_path, "rb") as f:
        resp = client.audio.translations.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
        )
    segments = getattr(resp, "segments", None) or []
    out: list[dict] = []
    for s in segments:
        out.append(
            {
                "start": float(s.start),
                "end": float(s.end),
                "text": (s.text or "").strip(),
            }
        )
    return out


def _translate_all(
    client: OpenAI,
    chunks: list[Path],
    progress_cb: ProgressCB,
) -> list[dict]:
    all_segments: list[dict] = []
    for i, chunk in enumerate(chunks):
        if progress_cb:
            progress_cb(f"Translating chunk {i + 1}/{len(chunks)} to English...")
        offset = i * CHUNK_SECONDS
        for seg in _translate_chunk(client, chunk):
            seg["start"] += offset
            seg["end"] += offset
            all_segments.append(seg)
    return all_segments


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Fetch + translate a YouTube video to English (cache only)."
    )
    parser.add_argument("url", help="YouTube video URL")
    args = parser.parse_args()

    def _print(msg: str) -> None:
        print(msg, flush=True)

    t = get_transcript(args.url, progress_cb=_print)
    print(f"\nDone. {len(t['segments'])} segments. Title: {t['title']}")
    print(f"Cached at: {_transcript_path(t['video_id'])}")
