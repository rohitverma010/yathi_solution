"""Shared helpers for ingest scripts."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
CORPUS_DIR = DATA_DIR / "corpus"
CACHE_DIR = REPO_ROOT / "cache"
TRANSCRIPT_DIR = CACHE_DIR / "transcripts"


@dataclass(frozen=True)
class TextRecord:
    """One normalized text record (FAQ / Sampradayic / Divine Dose)."""

    source_type: str
    source_id: str
    title: str
    url: str | None
    date: str | None
    text: str

    @property
    def language(self) -> str:
        return "en"


@dataclass
class Chunk:
    """One chunk ready to upsert into Supabase `chunks` table."""

    id: str
    source_type: str
    source_id: str
    source_title: str | None
    source_url: str | None
    source_date: str | None
    language: str
    text: str
    chunk_index: int
    metadata: dict


def iter_jsonl(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def write_jsonl(path: Path, records: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


_WS = re.compile(r"\s+")


def clean(text: str) -> str:
    return _WS.sub(" ", text).strip()


def require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise SystemExit(
            f"Missing required environment variable: {name}. "
            "Set it in your shell or in .env.local before running."
        )
    return val
