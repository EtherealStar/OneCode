"""Built-in OpenAI-compatible provider catalog."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderDefinition:
    id: str
    display_name: str
    base_url: str
    models_path: str = "/models"
    chat_completions_path: str = "/chat/completions"
    default_headers: dict[str, str] = field(default_factory=dict)
    notes: str | None = None
    requires_base_url: bool = False


BUILTIN_PROVIDERS: dict[str, ProviderDefinition] = {
    "openai": ProviderDefinition(
        id="openai",
        display_name="OpenAI",
        base_url="https://api.openai.com/v1",
    ),
    "deepseek": ProviderDefinition(
        id="deepseek",
        display_name="DeepSeek",
        base_url="https://api.deepseek.com",
    ),
    "glm": ProviderDefinition(
        id="glm",
        display_name="GLM",
        base_url="https://open.bigmodel.cn/api/paas/v4",
    ),
    "minimax": ProviderDefinition(
        id="minimax",
        display_name="MiniMax",
        base_url="https://api.minimax.chat/v1",
    ),
    "siliconflow": ProviderDefinition(
        id="siliconflow",
        display_name="SiliconFlow",
        base_url="https://api.siliconflow.cn/v1",
    ),
    "gemini": ProviderDefinition(
        id="gemini",
        display_name="Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    ),
    "claude-openai-compatible": ProviderDefinition(
        id="claude-openai-compatible",
        display_name="Claude OpenAI-compatible",
        base_url="",
        notes=(
            "Claude native API is not OpenAI Chat Completions compatible; "
            "configure an OpenAI-compatible gateway base URL."
        ),
        requires_base_url=True,
    ),
    "custom": ProviderDefinition(
        id="custom",
        display_name="Custom",
        base_url="",
        requires_base_url=True,
    ),
}

CONNECT_PROVIDER_ORDER = (
    "openai",
    "deepseek",
    "glm",
    "minimax",
    "siliconflow",
    "gemini",
    "claude-openai-compatible",
    "custom",
)


def get_provider_definition(provider_id: str) -> ProviderDefinition:
    try:
        return BUILTIN_PROVIDERS[provider_id]
    except KeyError as exc:
        raise KeyError(f"Unknown provider id: {provider_id}") from exc


def list_provider_definitions() -> tuple[ProviderDefinition, ...]:
    return tuple(BUILTIN_PROVIDERS[provider_id] for provider_id in CONNECT_PROVIDER_ORDER)
