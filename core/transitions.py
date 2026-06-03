"""Runtime transition reasons used by the agent loop."""

from enum import StrEnum


class TransitionReason(StrEnum):
    TOOL_USE = "tool_use"
    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
    RATE_LIMIT_RETRY = "rate_limit_retry"
    REACTIVE_COMPACT_RETRY = "reactive_compact_retry"
    MAX_OUTPUT_TOKENS_ESCALATE = "max_output_tokens_escalate"
    MAX_OUTPUT_TOKENS_RECOVERY = "max_output_tokens_recovery"
    STOP_HOOK_CONTINUE = "stop_hook_continue"
