from __future__ import annotations

import random
import time
from collections.abc import Callable

from .compaction import Compactor
from .config import AgentConfig
from .errors import ContextLimitError, RateLimitError, classify_provider_error
from .hooks import HookRegistry
from .model_client import LLMResponse, ModelClient, ToolCall
from .prompts import PromptContext, assemble_system_prompt
from .state import AgentState, UsageSnapshot
from .tools import ToolExecutionResult, ToolRegistry, partition_tool_calls

CONTINUATION_PROMPT = (
    "Output token limit hit. Resume directly: no apology, no recap. "
    "Pick up mid-thought if that is where the cut happened. Break remaining work into smaller pieces."
)


class AgentLoop:
    def __init__(
        self,
        *,
        config: AgentConfig,
        model_client: ModelClient,
        tool_registry: ToolRegistry,
        hooks: HookRegistry,
        compactor: Compactor,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ):
        self.config = config
        self.model_client = model_client
        self.tool_registry = tool_registry
        self.hooks = hooks
        self.compactor = compactor
        self.sleep = sleep
        self.rng = rng or random.Random()
        self.state = AgentState()
        profile = self.config.model_profile
        if profile:
            self.state.usage.context_window = profile.context_window

    def run(self, prompt: str) -> str:
        hook_result = self.hooks.emit("UserPromptSubmit", prompt=prompt, state=self.state)
        if hook_result.blocked:
            return hook_result.message or "Prompt blocked."
        content = hook_result.message if hook_result.force_continue and hook_result.message else prompt
        self.state.messages.append({"role": "user", "content": content})
        return self._run_loop()

    def _run_loop(self) -> str:
        profile = self.config.model_profile
        if profile is None:
            raise ValueError("AgentConfig.model_profile is required")
        max_output_tokens = profile.default_max_output_tokens
        final_text = ""

        while True:
            self.state.turn_count += 1
            if self.state.turn_count > self.config.max_turns:
                return "Stopped: maximum turn count reached."

            self.compactor.prepare_before_model_call(
                self.state,
                model_client=self.model_client,
                hooks=self.hooks,
            )
            system_prompt = assemble_system_prompt(
                PromptContext(cwd=self.config.cwd, tool_registry=self.tool_registry)
            )

            try:
                response = self._send_with_rate_limit_retries(
                    system_prompt=system_prompt,
                    max_output_tokens=max_output_tokens,
                )
            except ContextLimitError:
                if self.compactor.reactive_compact(
                    self.state,
                    model_client=self.model_client,
                    hooks=self.hooks,
                ):
                    self.state.last_transition = "reactive_compact_retry"
                    continue
                return "Stopped: context is still too large after reactive compact."

            self._update_usage(response.usage)

            if response.output_interrupted or response.stop_reason == "max_tokens":
                if max_output_tokens < profile.escalated_max_output_tokens:
                    max_output_tokens = profile.escalated_max_output_tokens
                    self.state.last_transition = "max_output_tokens_escalate"
                    continue
                if self.state.max_output_recovery_count < self.config.max_output_recovery_retries:
                    self.state.messages.append(response.assistant_message)
                    self.state.messages.append({"role": "user", "content": CONTINUATION_PROMPT})
                    self.state.max_output_recovery_count += 1
                    self.state.last_transition = "max_output_tokens_recovery"
                    max_output_tokens = profile.default_max_output_tokens
                    continue
                self.state.messages.append(response.assistant_message)
                return response.final_text

            self.state.max_output_recovery_count = 0
            self.state.messages.append(response.assistant_message)
            final_text = response.final_text

            if not response.tool_calls:
                stop_result = self.hooks.emit("Stop", state=self.state)
                if stop_result.force_continue and stop_result.message:
                    self.state.messages.append({"role": "user", "content": stop_result.message})
                    self.state.last_transition = "stop_hook_continue"
                    continue
                return final_text

            result_blocks = self._execute_tool_calls(response.tool_calls)
            self.state.messages.append({"role": "user", "content": result_blocks})
            self.state.last_transition = "tool_use"

    def _send_with_rate_limit_retries(
        self,
        *,
        system_prompt: str,
        max_output_tokens: int,
    ) -> LLMResponse:
        attempts = self.config.max_rate_limit_retries + 1
        for attempt in range(attempts):
            try:
                return self.model_client.send(
                    system=system_prompt,
                    messages=self.state.messages,
                    tools=self.tool_registry.api_schemas(),
                    max_output_tokens=max_output_tokens,
                )
            except Exception as exc:
                classified = exc if isinstance(exc, (RateLimitError, ContextLimitError)) else classify_provider_error(exc)
                if isinstance(classified, ContextLimitError):
                    raise classified from exc
                if isinstance(classified, RateLimitError) and attempt < attempts - 1:
                    self.sleep(self._retry_delay(attempt, classified.retry_after))
                    self.state.last_transition = "rate_limit_retry"
                    continue
                raise
        raise RuntimeError("unreachable")

    def _retry_delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return retry_after
        base = min(0.5 * (2**attempt), 32.0)
        return base + self.rng.uniform(0.0, base * 0.25)

    def _update_usage(self, usage: dict | None) -> None:
        profile = self.config.model_profile
        if profile is None:
            return
        self.state.usage = UsageSnapshot.from_usage(
            usage,
            context_window=profile.context_window,
            reserved_output_tokens=profile.reserved_output_tokens,
        )

    def _execute_tool_calls(self, tool_calls: list[ToolCall]) -> list[dict]:
        blocks: list[dict] = []
        for batch in partition_tool_calls(tool_calls, self.tool_registry):
            for tool_call in batch:
                blocks.append(self._execute_one_tool(tool_call).to_message_block())
        return blocks

    def _execute_one_tool(self, tool_call: ToolCall) -> ToolExecutionResult:
        tool = self.tool_registry.get(tool_call.name)
        if tool is None:
            return ToolExecutionResult(
                tool_use_id=tool_call.id,
                content=f"Unknown tool: {tool_call.name}",
                is_error=True,
            )

        validation_error = self._validate_required(tool_call.input, tool.meta.input_schema)
        if validation_error:
            return ToolExecutionResult(tool_call.id, validation_error, is_error=True)

        hook_result = self.hooks.emit("PreToolUse", tool_call=tool_call, tool=tool, state=self.state)
        if hook_result.updated_input is not None:
            tool_call = ToolCall(id=tool_call.id, name=tool_call.name, input=hook_result.updated_input)
        if hook_result.blocked:
            return ToolExecutionResult(
                tool_use_id=tool_call.id,
                content=hook_result.message or "Tool use blocked.",
                is_error=True,
            )

        try:
            output = str(tool.handler(**tool_call.input))
            if tool.meta.max_result_chars is not None and len(output) > tool.meta.max_result_chars:
                hidden = len(output) - tool.meta.max_result_chars
                output = output[: tool.meta.max_result_chars] + f"\n[truncated {hidden} chars]"
            self.hooks.emit(
                "PostToolUse",
                tool_call=tool_call,
                tool=tool,
                output=output,
                state=self.state,
            )
            return ToolExecutionResult(tool_use_id=tool_call.id, content=output)
        except Exception as exc:
            self.hooks.emit("ToolError", tool_call=tool_call, tool=tool, error=exc, state=self.state)
            return ToolExecutionResult(tool_use_id=tool_call.id, content=f"Error: {exc}", is_error=True)

    @staticmethod
    def _validate_required(input_data: dict, schema: dict) -> str | None:
        required = schema.get("required", [])
        for field in required:
            if field not in input_data:
                return f"Invalid tool input: missing required field '{field}'"
        return None
