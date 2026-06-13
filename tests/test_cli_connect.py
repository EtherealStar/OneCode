from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.runtime_state import RuntimeState
from infrastructure.config.env import load_provider_config
from services.context.message_store import MessageStore
from services.tools.executor import ToolExecutionUpdate
from services.tools.registry import ToolRegistry
from ui.cli.connect import ProviderEnvUpdate, write_provider_env
from ui.cli.types import CliRuntime


class FakeModelClient:
    def __init__(self, *, display_name: str, model: str) -> None:
        self.config = SimpleNamespace(display_name=display_name, model=model)

    async def stream(self, snapshot: object):
        raise AssertionError("model should not be called by connect tests")
        yield


class FakeToolExecutor:
    async def execute(self, tool_calls: tuple, state: object):
        if False:
            yield ToolExecutionUpdate(type="result")


class FakeLoop:
    async def stream(self, prompt: str):
        raise AssertionError("loop should not be called by connect tests")
        yield


def make_runtime(tmp_path: Path) -> CliRuntime:
    state = RuntimeState(session_id="session-connect")
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        cwd=tmp_path,
        flush_interval_seconds=60,
    )
    old_client = FakeModelClient(display_name="Old", model="old-model")
    return CliRuntime(
        workspace=tmp_path,
        state=state,
        message_store=message_store,
        registry=ToolRegistry(),
        loop=FakeLoop(),  # type: ignore[arg-type]
        provider_label="Old",
        model="old-model",
        model_client=old_client,
        tool_executor=FakeToolExecutor(),  # type: ignore[arg-type]
    )


def test_write_provider_env_preserves_unrelated_lines_and_removes_old_base_url(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "# keep this",
                "OTHER_SETTING=yes",
                "ONECODE_PROVIDER_ID=custom",
                "ONECODE_MODEL=old",
                "ONECODE_API_KEY=old-secret",
                "ONECODE_BASE_URL=https://old.example/v1",
            ]
        ),
        encoding="utf-8",
    )

    write_provider_env(
        env_path,
        ProviderEnvUpdate(
            provider_id="deepseek",
            model="deepseek-chat",
            api_key="secret with spaces",
        ),
    )

    text = env_path.read_text(encoding="utf-8")
    config = load_provider_config(env_path)
    assert "# keep this" in text
    assert "OTHER_SETTING=yes" in text
    assert "ONECODE_BASE_URL" not in text
    assert config.provider_id == "deepseek"
    assert config.model == "deepseek-chat"
    assert config.api_key == "secret with spaces"


def test_write_provider_env_writes_required_base_url(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"

    write_provider_env(
        env_path,
        ProviderEnvUpdate(
            provider_id="custom",
            model="custom-model",
            api_key="secret",
            base_url="https://example.test/v1",
        ),
    )

    config = load_provider_config(env_path)
    assert config.provider_id == "custom"
    assert config.base_url == "https://example.test/v1"


def test_runtime_with_model_config_rebinds_model_client(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = make_runtime(tmp_path)
    new_client = FakeModelClient(display_name="DeepSeek", model="deepseek-chat")
    monkeypatch.setattr(
        "ui.cli.types.create_model_client",
        lambda env_path: new_client,
    )

    rebound = runtime.with_model_config()

    assert rebound.state is runtime.state
    assert rebound.message_store is runtime.message_store
    assert rebound.provider_label == "DeepSeek"
    assert rebound.model == "deepseek-chat"
    assert rebound.model_client is new_client
    assert rebound.loop.model_client is new_client
    assert rebound.memory_selector is not runtime.memory_selector

