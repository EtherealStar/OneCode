"""Project-local trust policy for MCP servers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from services.mcp.types import McpServerConfig


BASE_STDIO_ENV_ALLOWLIST: tuple[str, ...] = (
    "APPDATA",
    "COMSPEC",
    "HOME",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
)


def fingerprint_mcp_server(config: McpServerConfig, workspace: Path) -> str:
    """Return a stable fingerprint for execution-relevant MCP config fields."""

    payload = {
        "name": config.name,
        "transport": config.transport,
        "command": config.command or "",
        "args": list(config.args),
        "cwd": str(workspace.resolve()),
        "env": {key: config.env[key] for key in sorted(config.env)},
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_stdio_child_env(parent_env: dict[str, str], config: McpServerConfig) -> dict[str, str]:
    """Build the sanitized environment passed to a stdio MCP child process."""

    allowed = {key.upper() for key in BASE_STDIO_ENV_ALLOWLIST}
    env = {
        key: value
        for key, value in parent_env.items()
        if key.upper() in allowed
    }
    env.update(config.env)
    return env


@dataclass(frozen=True)
class McpTrustPolicy:
    store: "McpTrustStore | None" = None
    session_trusted: frozenset[str] = frozenset()
    trust_all: bool = False

    def __init__(
        self,
        store: "McpTrustStore | None" = None,
        *,
        session_trusted: Iterable[str] = (),
        trust_all: bool = False,
    ) -> None:
        object.__setattr__(self, "store", store)
        object.__setattr__(self, "session_trusted", frozenset(session_trusted))
        object.__setattr__(self, "trust_all", trust_all)

    @classmethod
    def trust_all_servers(cls) -> "McpTrustPolicy":
        return cls(trust_all=True)

    def is_trusted(self, config: McpServerConfig, workspace: Path) -> bool:
        if config.transport != "stdio":
            return True
        if self.trust_all or config.name in self.session_trusted:
            return True
        if self.store is None:
            return False
        return self.store.is_trusted(config.name, fingerprint_mcp_server(config, workspace))


class McpTrustStore:
    """Stores local MCP trust decisions in .onecode/settings.json."""

    def __init__(self, settings_path: Path) -> None:
        self.settings_path = settings_path

    def is_trusted(self, server_name: str, fingerprint: str) -> bool:
        entry = self._trusted_servers(self._read_settings(), create=False).get(server_name)
        return isinstance(entry, dict) and entry.get("fingerprint") == fingerprint

    def trust_server(
        self,
        server_name: str,
        fingerprint: str,
        *,
        transport: str = "stdio",
    ) -> None:
        settings = self._read_settings()
        trusted = self._trusted_servers(settings, create=True)
        trusted[server_name] = {
            "fingerprint": fingerprint,
            "transport": transport,
        }
        self._write_settings(settings)

    def _read_settings(self) -> dict[str, Any]:
        if not self.settings_path.exists():
            return {}
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in project settings: {self.settings_path}: {exc.msg}"
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(
                f"Project settings must contain a JSON object: {self.settings_path}"
            )
        return data

    def _write_settings(self, settings: dict[str, Any]) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _trusted_servers(
        settings: dict[str, Any],
        *,
        create: bool,
    ) -> dict[str, Any]:
        mcp = settings.get("mcp")
        if mcp is None:
            if not create:
                return {}
            mcp = {}
            settings["mcp"] = mcp
        if not isinstance(mcp, dict):
            raise ValueError("Project settings field 'mcp' must be an object.")
        trusted = mcp.get("trustedServers")
        if trusted is None:
            if not create:
                return {}
            trusted = {}
            mcp["trustedServers"] = trusted
        if not isinstance(trusted, dict):
            raise ValueError(
                "Project settings field 'mcp.trustedServers' must be an object."
            )
        return trusted
