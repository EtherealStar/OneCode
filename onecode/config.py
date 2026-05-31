from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[assignment]


@dataclass(frozen=True)
class ModelProfile:
    name: str
    context_window: int = 200_000
    default_max_output_tokens: int = 8_000
    escalated_max_output_tokens: int = 64_000
    reserved_output_tokens: int = 8_000


@dataclass(frozen=True)
class AgentConfig:
    cwd: Path
    model: str
    api_key: str | None = None
    base_url: str | None = None
    model_profile: ModelProfile | None = None
    max_turns: int = 100
    max_rate_limit_retries: int = 10
    max_output_recovery_retries: int = 3
    auto_compact_ratio: float = 0.80
    post_compact_target_ratio: float = 0.55
    state_dir_name: str = ".onecode"

    @property
    def state_dir(self) -> Path:
        return self.cwd / self.state_dir_name


def load_config(cwd: Path | None = None) -> AgentConfig:
    workdir = (cwd or Path.cwd()).resolve()
    # Load .env file (won't override existing env vars)
    if load_dotenv is not None:
        env_file = workdir / ".env"
        if env_file.exists():
            load_dotenv(env_file, override=False)
    model = _env_text("ONECODE_MODEL") or _env_text("MODEL_ID")
    if not model:
        raise ValueError("ONECODE_MODEL is required.")
    base_url = _env_text("ONECODE_BASE_URL")
    if not base_url:
        raise ValueError(
            "ONECODE_BASE_URL is required and is currently empty. "
            f"Set it in {workdir / '.env'} or in the process environment."
        )
    context_window = int(os.getenv("ONECODE_CONTEXT_WINDOW", "200000"))
    default_output = int(os.getenv("ONECODE_MAX_OUTPUT_TOKENS", "8000"))
    escalated_output = int(os.getenv("ONECODE_ESCALATED_MAX_OUTPUT_TOKENS", "64000"))
    profile = ModelProfile(
        name=model,
        context_window=context_window,
        default_max_output_tokens=default_output,
        escalated_max_output_tokens=escalated_output,
        reserved_output_tokens=default_output,
    )
    return AgentConfig(
        cwd=workdir,
        model=model,
        api_key=_env_text("ONECODE_API_KEY"),
        base_url=base_url,
        model_profile=profile,
    )


def _env_text(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None
