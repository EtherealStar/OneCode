"""Prompt text for long-term memory behavior."""

from __future__ import annotations

from hashlib import sha256

from services.memory.auto_store import LongTermMemoryStore

PROMPT_VERSION = "long-term-memory-v1"


class LongTermMemoryPromptProvider:
    def __init__(self, store: LongTermMemoryStore) -> None:
        self.store = store

    def prompt_text(self) -> str:
        index_text, truncated = self.store.truncated_entrypoint()
        lines = [
            f"Long-term memory directory: {self.store.memory_dir}",
            "Memory types: user, feedback, project, reference.",
            "Use normal file tools for explicit user requests to remember or forget information.",
            "Save durable preferences, feedback, project context, and external references that will matter in future sessions.",
            "Do not save short-term todos, current implementation plans, secrets, or facts that are directly available from source files or git history.",
            "Prefer updating or deleting stale memories over creating duplicates.",
            "Keep MEMORY.md as a compact index. Put detailed content in topic Markdown files with frontmatter.",
            "Relevant topic files may be attached separately as memory attachments; use them as current-context guidance.",
        ]
        if index_text.strip():
            lines.extend(["", "Current MEMORY.md index:", index_text.strip()])
        elif truncated:
            lines.append("MEMORY.md index is truncated but currently empty after truncation.")
        return "\n".join(lines)

    def fingerprint(self) -> str:
        index_text, _ = self.store.truncated_entrypoint()
        payload = "\0".join([PROMPT_VERSION, str(self.store.entrypoint_path), index_text])
        return sha256(payload.encode("utf-8")).hexdigest()
