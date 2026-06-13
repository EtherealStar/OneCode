"""Provider connection helpers for the CLI."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from infrastructure.providers.connection import ConnectOption, ProviderConnectionService

PROVIDER_ENV_KEYS = {
    "ONECODE_PROVIDER_ID",
    "ONECODE_MODEL",
    "ONECODE_API_KEY",
    "ONECODE_BASE_URL",
}


@dataclass(frozen=True)
class ProviderEnvUpdate:
    provider_id: str
    model: str
    api_key: str
    base_url: str | None = None


def list_connect_options() -> tuple[ConnectOption, ...]:
    return tuple(ProviderConnectionService().list_connect_options())


def write_provider_env(env_path: Path, update: ProviderEnvUpdate) -> None:
    assignments = {
        "ONECODE_PROVIDER_ID": update.provider_id,
        "ONECODE_MODEL": update.model,
        "ONECODE_API_KEY": update.api_key,
    }
    remove_keys = set()
    if update.base_url:
        assignments["ONECODE_BASE_URL"] = update.base_url
    else:
        remove_keys.add("ONECODE_BASE_URL")

    lines = _read_env_lines(env_path)
    output: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = _line_key(line)
        if key in assignments:
            output.append(f"{key}={_format_env_value(assignments[key])}")
            seen.add(key)
            continue
        if key in remove_keys:
            continue
        output.append(line)

    for key, value in assignments.items():
        if key not in seen:
            output.append(f"{key}={_format_env_value(value)}")

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
    return key if key in PROVIDER_ENV_KEYS else None


def _format_env_value(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:@+-]+", value):
        return value
    return json.dumps(value, ensure_ascii=False)
