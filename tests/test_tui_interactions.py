"""Tests for TUI interaction panels (permission, question, plan, trust, session)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

from textual.app import App

from services.permissions import PermissionOption, PermissionResponse
from services.questions.types import QuestionOption, QuestionRequest
from ui.tui.modals import (
    CommandOutputModal,
    McpTrustModal,
    PermissionModal,
    PlanApprovalModal,
    QuestionModal,
    SessionPickerModal,
)


class ModalHost(App[None]):
    """Pushes one modal and records its result."""

    def __init__(self, modal) -> None:
        super().__init__()
        self._modal = modal
        self.result: object = "unset"

    async def on_mount(self) -> None:
        from ui.tui.theme import apply_theme

        apply_theme(self)
        self.push_screen(self._modal, self._record)

    def _record(self, result: object) -> None:
        self.result = result


def _run(coro):
    return asyncio.run(coro)


def _permission_request():
    return SimpleNamespace(
        descriptor=SimpleNamespace(name="bash"),
        decision=SimpleNamespace(reason="needs approval", guard_policies=()),
        classification=SimpleNamespace(read_only=True),
        tool_input={"command": "ls"},
        options=(
            PermissionOption(id="allow_once", label="allow once", action="allow", scope="once"),
            PermissionOption(
                id="allow_session_directory",
                label="allow this directory for this session",
                action="allow",
                scope="session",
            ),
            PermissionOption(id="deny", label="deny", action="deny", scope="once"),
        ),
    )


def test_permission_modal_allow_once() -> None:
    async def scenario() -> None:
        app = ModalHost(PermissionModal(_permission_request()))
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen.query_one("#permission-option-allow_once").press()
            await pilot.pause()
            assert app.result == PermissionResponse(action="allow", scope="once")

    _run(scenario())


def test_permission_modal_escape_denies() -> None:
    async def scenario() -> None:
        app = ModalHost(PermissionModal(_permission_request()))
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.result, PermissionResponse)
            assert app.result.action == "deny"

    _run(scenario())


def test_question_modal_single_select() -> None:
    request = QuestionRequest(
        question="Which?",
        header="H",
        options=(QuestionOption(label="A"), QuestionOption(label="B")),
    )

    async def scenario() -> None:
        app = ModalHost(QuestionModal(request))
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app.result == ("A",)

    _run(scenario())


def test_question_modal_multi_select() -> None:
    request = QuestionRequest(
        question="Which?",
        header="H",
        options=(QuestionOption(label="A"), QuestionOption(label="B")),
        multi_select=True,
    )

    async def scenario() -> None:
        app = ModalHost(QuestionModal(request))
        async with app.run_test() as pilot:
            await pilot.pause()
            selection = app.screen.query_one("#question-select")
            selection.select("A")
            selection.select("B")
            await pilot.pause()
            app.screen.query_one("#question-submit").press()
            await pilot.pause()
            assert app.result == ("A", "B")

    _run(scenario())


def test_question_modal_cancel_declines() -> None:
    request = QuestionRequest(
        question="Which?",
        header="H",
        options=(QuestionOption(label="A"),),
    )

    async def scenario() -> None:
        app = ModalHost(QuestionModal(request))
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen.query_one("#question-cancel").press()
            await pilot.pause()
            assert app.result is None

    _run(scenario())


def test_plan_approval_modal_returns_choice() -> None:
    async def scenario() -> None:
        app = ModalHost(
            PlanApprovalModal(path="/tmp/plan.md", content="# Plan", summary="s")
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen.query_one("#plan-approve").press()
            await pilot.pause()
            assert app.result == "approve"

    _run(scenario())


def test_mcp_trust_modal_returns_choice() -> None:
    request = SimpleNamespace(
        server_name="srv",
        command="cmd",
        args="--x",
        cwd="/tmp",
        explicit_env_keys="(none)",
        base_env_keys="(none)",
    )

    async def scenario() -> None:
        app = ModalHost(McpTrustModal(request))
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen.query_one("#trust-accept").press()
            await pilot.pause()
            assert app.result == "trust"

    _run(scenario())


def test_session_picker_returns_session_id() -> None:
    summary = SimpleNamespace(
        session_id="abc",
        title="hello",
        message_count=3,
        updated_at=datetime(2026, 1, 1, 12, 0),
    )

    async def scenario() -> None:
        app = ModalHost(SessionPickerModal((summary,)))
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app.result == "abc"

    _run(scenario())


def test_command_output_modal_closes_on_escape() -> None:
    from rich.text import Text

    async def scenario() -> None:
        app = ModalHost(CommandOutputModal("状态", Text("ok")))
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert app.result is None

    _run(scenario())


def test_provider_picker_selects_first() -> None:
    from infrastructure.providers.connection import ConnectOption
    from ui.tui.modals import ProviderPickerModal

    options = (
        ConnectOption(provider_id="openai", display_name="OpenAI"),
        ConnectOption(provider_id="custom", display_name="Custom", requires_base_url=True),
    )

    async def scenario() -> None:
        app = ModalHost(ProviderPickerModal(options))
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app.result is not None
            assert app.result.provider_id == "openai"

    _run(scenario())


def test_credential_modal_collects_base_url_and_key() -> None:
    from infrastructure.providers.catalog import get_provider_definition
    from ui.tui.modals import CredentialModal

    async def scenario() -> None:
        provider = get_provider_definition("custom")
        app = ModalHost(CredentialModal(provider))
        async with app.run_test() as pilot:
            await pilot.pause()
            base = app.screen.query_one("#credential-base-url")
            base.focus()
            base.value = "https://example.com/v1"
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            key = app.screen.query_one("#credential-api-key")
            key.value = "sk-test"
            await pilot.press("enter")
            await pilot.pause()
            assert app.result is not None
            assert app.result.base_url == "https://example.com/v1"
            assert app.result.api_key == "sk-test"

    _run(scenario())


def test_credential_modal_ollama_skips_key() -> None:
    from infrastructure.providers.catalog import get_provider_definition
    from ui.tui.modals import CredentialModal

    async def scenario() -> None:
        provider = get_provider_definition("ollama")
        app = ModalHost(CredentialModal(provider))
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen.query_one("#credential-submit").press()
            await pilot.pause()
            assert app.result is not None
            assert app.result.api_key == ""
            assert app.result.base_url == "http://localhost:11434"

    _run(scenario())
