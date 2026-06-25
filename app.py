"""Streamlit app — Telugu SRT → English SRT converter.

Left column : SRT upload
Right column: translated SRT output + download button
"""
from __future__ import annotations

import io
import logging
import os
import re
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

load_dotenv(dotenv_path=Path(__file__).parent / ".env.local")
load_dotenv(dotenv_path=Path(__file__).parent / ".env")
log.info("App started — env loaded")

SUPPORTED_TYPES = ["srt"]


# ── helpers ───────────────────────────────────────────────────────────────────


def _parse_srt_blocks(content: str) -> list[dict]:
    blocks = re.split(r"\n\s*\n", content.strip())
    log.info(f"_parse_srt_blocks | raw blocks found: {len(blocks)}")
    segments = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0].strip())
            times = lines[1].strip()
            text = "\n".join(lines[2:]).strip()
            segments.append({"index": idx, "times": times, "text": text})
        except (ValueError, IndexError):
            log.warning(f"_parse_srt_blocks | skipped malformed block: {block[:60]!r}")
    log.info(f"_parse_srt_blocks | parsed segments: {len(segments)}")
    return segments


def translate_srt(srt_content: str, status_fn) -> str:
    """Translate SRT text from Telugu to English using GPT, preserving timestamps."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY not set. Add it to .env.local or your shell environment."
        )

    client = OpenAI(api_key=api_key)
    segments = _parse_srt_blocks(srt_content)
    if not segments:
        raise RuntimeError("Could not parse any subtitle blocks — check the SRT format.")

    BATCH = 50
    total = len(segments)
    log.info(f"translate_srt | total segments: {total}, batch size: {BATCH}")
    translated: list[str] = []

    for start in range(0, total, BATCH):
        batch = segments[start : start + BATCH]
        batch_end = min(start + BATCH, total)
        log.info(f"translate_srt | batch {start + 1}–{batch_end} / {total}")
        status_fn(f"Translating subtitles {start + 1}–{batch_end} of {total} …")

        source = "\n---\n".join(s["text"] for s in batch)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Translate the following Telugu subtitle texts to English. "
                        "Return ONLY the translations separated by '---', one per entry, "
                        "preserving order and count exactly. No extra commentary."
                    ),
                },
                {"role": "user", "content": source},
            ],
        )

        raw = resp.choices[0].message.content.strip()
        parts = [p.strip() for p in raw.split("---")]

        if len(parts) != len(batch):
            log.warning(
                f"translate_srt | GPT returned {len(parts)} parts for {len(batch)} "
                f"segments in batch starting at {start + 1}"
            )
        while len(parts) < len(batch):
            parts.append("")
        translated.extend(parts[: len(batch)])

    log.info(f"translate_srt | done — {total} segments translated")

    lines: list[str] = []
    for i, seg in enumerate(segments):
        lines.append(f"{seg['index']}\n{seg['times']}\n{translated[i]}\n")
    return "\n".join(lines)


def srt_to_docx(srt_content: str) -> bytes:
    """Convert SRT text to a .docx file; returns raw bytes."""
    from docx import Document
    from docx.shared import Pt, RGBColor

    doc = Document()
    doc.add_heading("Translated Subtitles", level=1)

    for block in re.split(r"\n\s*\n", srt_content.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        timecode = lines[1].strip()
        text = " ".join(lines[2:]).strip()

        p = doc.add_paragraph()
        run_time = p.add_run(f"{timecode}\n")
        run_time.font.size = Pt(8)
        run_time.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
        run_text = p.add_run(text)
        run_text.font.size = Pt(11)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ── Streamlit UI ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Telugu → English Subtitle Converter",
    layout="wide",
)

st.title("Telugu → English Subtitle Converter")
st.caption(
    "Upload a Telugu `.srt` file. Subtitles are translated to English "
    "and available for download as `.srt` or `.docx`."
)

# Session state defaults
st.session_state.setdefault("srt_content", "")
st.session_state.setdefault("srt_filename", "output.srt")

left, right = st.columns(2)

# ── LEFT: upload ──────────────────────────────────────────────────────────────
with left:
    st.subheader("Input")

    uploaded = st.file_uploader(
        "Choose an SRT file",
        type=SUPPORTED_TYPES,
        help="Supported format: .srt",
    )

    if uploaded:
        st.info(
            f"**File:** {uploaded.name}  \n"
            f"**Size:** {uploaded.size / 1_000:.1f} KB"
        )

    convert_btn = st.button(
        "Convert to English SRT",
        type="primary",
        use_container_width=True,
        disabled=not uploaded,
    )

# ── RIGHT: output ─────────────────────────────────────────────────────────────
with right:
    st.subheader("English SRT Output")

    if convert_btn and uploaded:
        status_box = st.empty()

        with st.spinner("Processing — this may take a few minutes …"):
            try:
                srt_raw = uploaded.getvalue().decode("utf-8", errors="replace")
                srt_text = translate_srt(srt_raw, lambda msg: status_box.info(msg))

                stem = Path(uploaded.name).stem
                st.session_state["srt_filename"] = f"{stem}.en.srt"
                st.session_state["srt_content"] = srt_text
                status_box.success("Translation complete!")

            except Exception as exc:
                log.exception("Translation failed")
                status_box.error(f"Error: {exc}")

    if st.session_state["srt_content"]:
        st.text_area(
            "SRT content",
            value=st.session_state["srt_content"],
            height=480,
            label_visibility="collapsed",
        )

        dl_left, dl_right = st.columns(2)
        srt_filename = st.session_state["srt_filename"]
        docx_filename = f"{Path(srt_filename).stem}.docx"

        with dl_left:
            st.download_button(
                label="⬇ Download .srt",
                data=st.session_state["srt_content"].encode("utf-8"),
                file_name=srt_filename,
                mime="text/plain",
                use_container_width=True,
            )
        with dl_right:
            st.download_button(
                label="⬇ Download .docx",
                data=srt_to_docx(st.session_state["srt_content"]),
                file_name=docx_filename,
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )
    else:
        st.info("Upload an SRT file on the left, then click **Convert**.")