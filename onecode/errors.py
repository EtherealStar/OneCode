from __future__ import annotations


class AgentError(Exception):
    """Base class for recoverable agent runtime errors."""


class RateLimitError(AgentError):
    def __init__(self, message: str = "rate limited", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ContextLimitError(AgentError):
    """Raised when the provider rejects the prompt because context is too large."""


class ModelOverloadedError(RateLimitError):
    """Provider overloaded. Same retry path as rate limits for MVP."""


class OutputInterruptedError(AgentError):
    """Streaming stopped before a complete assistant response was available."""


def classify_provider_error(exc: Exception) -> AgentError | None:
    text = str(exc).lower()
    if "429" in text or "rate limit" in text or "rate_limit" in text:
        return RateLimitError(str(exc))
    if "529" in text or "overloaded" in text:
        return ModelOverloadedError(str(exc))
    if (
        "prompt_too_long" in text
        or "context_length_exceeded" in text
        or "too many tokens" in text
        or "413" in text
    ):
        return ContextLimitError(str(exc))
    return None
