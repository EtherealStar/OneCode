"""Provider connection helpers for the CLI."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from infrastructure.config.env import provider_env_prefix
from infrastructure.providers.connection import ConnectOption, ProviderConnectionService

ACTIVE_PROVIDER_KEY = "ONECODE_PROVIDER_ID"


@dataclass(frozen=True)
class ProviderEnvUpdate:
    provider_id: str
    model: str
    api_key: str
    base_url: str | None = None


def list_connect_options() -> tuple[ConnectOption, ...]:
    return tuple(ProviderConnectionService().list_connect_options())


def write_provider_env(env_path: Path, update: ProviderEnvUpdate) -> None:
    prefix = provider_env_prefix(update.provider_id)
    provider_keys = {
        f"{prefix}_BASE_URL",
        f"{prefix}_MODEL",
        f"{prefix}_API_KEY",
    }
    provider_assignments = {
        f"{prefix}_MODEL": update.model,
    }
    if update.base_url:
        provider_assignments[f"{prefix}_BASE_URL"] = update.base_url
    if update.api_key:
        provider_assignments[f"{prefix}_API_KEY"] = update.api_key

    lines = _read_env_lines(env_path)
    output: list[str] = []
    active_seen = False
    provider_comment = f"#{update.provider_id}"
    provider_block_seen = False
    provider_comment_index: int | None = None
    for line in lines:
        key = _line_key(line)
        if key == ACTIVE_PROVIDER_KEY:
            output.append(f"{ACTIVE_PROVIDER_KEY}={_format_env_value(update.provider_id)}")
            active_seen = True
            continue
        if key in provider_keys:
            continue
        output.append(line)
        if line.strip() == provider_comment:
            provider_block_seen = True
            provider_comment_index = len(output) - 1

    if not active_seen:
        output.insert(0, f"{ACTIVE_PROVIDER_KEY}={_format_env_value(update.provider_id)}")
        if provider_comment_index is not None:
            provider_comment_index += 1

    if not provider_block_seen:
        if output and output[-1].strip():
            output.append("")
        output.append(provider_comment)
        provider_comment_index = len(output) - 1

    provider_lines = _provider_assignment_lines(provider_assignments)
    assert provider_comment_index is not None
    output[provider_comment_index + 1:provider_comment_index + 1] = provider_lines

    env_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = env_path.with_name(f".{env_path.name}.tmp")
    tmp_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    tmp_path.replace(env_path)


def _read_env_lines(env_path: Path) -> list[str]:
    try:
        return env_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def _line_key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key = stripped.split("=", 1)[0].strip()
    return key


def _format_env_value(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:@+-]+", value):
        return value
    return json.dumps(value, ensure_ascii=False)


def _provider_assignment_lines(assignments: dict[str, str]) -> list[str]:
    return [
        f"{key}={_format_env_value(value)}"
        for key, value in assignments.items()
    ]


def read_existing_env(env_path: Path) -> dict[str, str | None]:
    """Read all assignments from a ``.env`` file without interpolation."""

    result: dict[str, str | None] = {}
    for line in _read_env_lines(env_path):
        key = _line_key(line)
        if key is not None:
            raw_value = line.strip().split("=", 1)[1].strip()
            # Strip surrounding quotes if present.
            if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in {'"', "'"}:
                raw_value = raw_value[1:-1]
            result[key] = raw_value or None
    return result


def has_provider_config(env_path: Path) -> bool:
    """Return ``True`` when ``.env`` has the active provider block configured."""

    existing = read_existing_env(env_path)
    provider_id = existing.get(ACTIVE_PROVIDER_KEY)
    if not provider_id:
        return False
    prefix = provider_env_prefix(provider_id)
    api_key = existing.get(f"{prefix}_API_KEY")
    model = existing.get(f"{prefix}_MODEL")
    if not provider_id or not model:
        return False
    # Ollama doesn't require an API key.
    from infrastructure.providers.catalog import BUILTIN_PROVIDERS

    provider = BUILTIN_PROVIDERS.get(provider_id)
    if provider is not None and not provider.api_key_required:
        return True
    return bool(api_key)


def existing_key_for_provider(env_path: Path, provider_id: str) -> str | None:
    """Return the existing provider-specific API key from ``.env``."""

    existing = read_existing_env(env_path)
    return existing.get(f"{provider_env_prefix(provider_id)}_API_KEY")
