from __future__ import annotations

import json
import time
from pathlib import Path


def write_transcript(messages: list[dict], transcript_dir: Path) -> Path:
    transcript_dir.mkdir(parents=True, exist_ok=True)
    path = transcript_dir / f"transcript_{int(time.time() * 1000)}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for message in messages:
            handle.write(json.dumps(message, ensure_ascii=False, default=str))
            handle.write("\n")
    return path
