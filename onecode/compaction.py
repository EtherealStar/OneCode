from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

from .hooks import HookRegistry
from .model_client import ModelClient
from .state import AgentState
from .transcript import write_transcript


@dataclass(frozen=True)
class CompactionConfig:
    state_dir: Path
    keep_recent_tool_results: int = 3
    tool_result_total_budget_chars: int = 200_000
    tool_result_preview_chars: int = 2_000
    tool_result_persist_threshold_chars: int = 30_000
    sliding_window_max_messages: int = 60
    auto_compact_ratio: float = 0.80
    max_consecutive_compact_failures: int = 3

    @property
    def tool_results_dir(self) -> Path:
        return self.state_dir / "tool-results"

    @property
    def transcript_dir(self) -> Path:
        return self.state_dir / "transcripts"


class Compactor:
    def __init__(self, config: CompactionConfig):
        self.config = config

    def prepare_before_model_call(
        self,
        state: AgentState,
        *,
        model_client: ModelClient,
        hooks: HookRegistry,
    ) -> None:
        state.messages = self.apply_tool_result_budget(state.messages)
        state.messages = self.cleanup_old_tool_results(state.messages)
        state.messages = self.apply_sliding_window(state.messages)
        if state.usage.occupied_ratio >= self.config.auto_compact_ratio:
            self.full_compact(
                state,
                model_client=model_client,
                hooks=hooks,
                reason=f"usage {state.usage.occupied_ratio:.0%} reached auto compact threshold",
            )

    def apply_tool_result_budget(self, messages: list[dict]) -> list[dict]:
        messages = copy.deepcopy(messages)
        last = messages[-1] if messages else None
        if not last or last.get("role") != "user" or not isinstance(last.get("content"), list):
            return messages
        blocks = [
            block
            for block in last["content"]
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
        total = sum(len(str(block.get("content", ""))) for block in blocks)
        if total <= self.config.tool_result_total_budget_chars:
            return messages
        ranked = sorted(blocks, key=lambda block: len(str(block.get("content", ""))), reverse=True)
        for block in ranked:
            if total <= self.config.tool_result_total_budget_chars:
                break
            content = str(block.get("content", ""))
            if len(content) < self.config.tool_result_persist_threshold_chars:
                continue
            block["content"] = self._persist_large_tool_result(
                str(block.get("tool_use_id", "unknown")),
                content,
            )
            total = sum(len(str(candidate.get("content", ""))) for candidate in blocks)
        return messages

    def cleanup_old_tool_results(self, messages: list[dict]) -> list[dict]:
        messages = copy.deepcopy(messages)
        tool_results: list[dict] = []
        for message in messages:
            if message.get("role") != "user" or not isinstance(message.get("content"), list):
                continue
            for block in message["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_results.append(block)
        if len(tool_results) <= self.config.keep_recent_tool_results:
            return messages
        for block in tool_results[: -self.config.keep_recent_tool_results]:
            content = str(block.get("content", ""))
            if len(content) > 120:
                block["content"] = "[Earlier tool result compacted. Re-run the tool if needed.]"
        return messages

    def apply_sliding_window(self, messages: list[dict]) -> list[dict]:
        if len(messages) <= self.config.sliding_window_max_messages:
            return messages
        keep_head = 2
        keep_tail = self.config.sliding_window_max_messages - keep_head - 1
        omitted = len(messages) - keep_head - keep_tail
        placeholder = {
            "role": "user",
            "content": f"[sliding window omitted {omitted} older messages]",
        }
        return messages[:keep_head] + [placeholder] + messages[-keep_tail:]

    def full_compact(
        self,
        state: AgentState,
        *,
        model_client: ModelClient,
        hooks: HookRegistry,
        reason: str,
        keep_tail: int = 3,
    ) -> None:
        if state.consecutive_compact_failures >= self.config.max_consecutive_compact_failures:
            return
        hooks.emit("PreCompact", state=state, reason=reason)
        transcript_path = write_transcript(state.messages, self.config.transcript_dir)
        try:
            summary = model_client.summarize(messages=state.messages, focus=reason)
        except Exception:
            state.consecutive_compact_failures += 1
            raise
        summary_message = {
            "role": "user",
            "content": (
                "[Compacted conversation]\n"
                f"Transcript: {transcript_path}\n\n"
                f"{summary}"
            ),
        }
        tail = state.messages[-keep_tail:] if keep_tail > 0 else []
        state.messages = [summary_message, *tail]
        state.has_reactive_compacted = False
        state.consecutive_compact_failures = 0
        hooks.emit("PostCompact", state=state, reason=reason, transcript_path=transcript_path)

    def reactive_compact(
        self,
        state: AgentState,
        *,
        model_client: ModelClient,
        hooks: HookRegistry,
    ) -> bool:
        if state.has_reactive_compacted:
            return False
        self.full_compact(
            state,
            model_client=model_client,
            hooks=hooks,
            reason="reactive compact after context limit error",
            keep_tail=5,
        )
        state.has_reactive_compacted = True
        return True

    def _persist_large_tool_result(self, tool_use_id: str, content: str) -> str:
        self.config.tool_results_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.tool_results_dir / f"{tool_use_id}.txt"
        if not path.exists():
            path.write_text(content, encoding="utf-8")
        preview = content[: self.config.tool_result_preview_chars]
        return f"<persisted-output path=\"{path}\">\n{preview}\n</persisted-output>"
