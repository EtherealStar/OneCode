"""Shared text decoding helpers for tools and local process output."""

from __future__ import annotations

from pathlib import Path


DEFAULT_TEXT_ENCODING = "utf-8"


def decode_text(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode(DEFAULT_TEXT_ENCODING, errors="replace")


def read_text_file(path: Path) -> str:
    return path.read_text(encoding=DEFAULT_TEXT_ENCODING, errors="replace")


def write_text_file(path: Path, content: str) -> None:
    path.write_text(content, encoding=DEFAULT_TEXT_ENCODING)
