"""`/connect`` wizard — multi-step provider configuration.

The wizard lives entirely on the alternate screen so the user can
type their API key without polluting the inline scrollback.

Flow::

    1. Pick a provider (TransientSelector)
    2. Custom → input base URL
    3. Check .env for existing key → K/R/C (Keep/Replace/Cancel)
       No key + api_key_required → input API key
       No key + not api_key_required (Ollama) → skip
    4. Fetch model list from provider (endpoint auto-detection)
       Failure → fallback to manual model name + connection test
    5. Model selector (TransientSelector)
    6. write_provider_env → with_model_config → return new runtime
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, TextIO

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import BeforeInput, PasswordProcessor
from prompt_toolkit.styles import Style
from rich.text import Text

from ui.cli.connect import write_provider_env
from ui.cli.terminal.transient import can_enter_alternate_screen
from ui.cli.types import CliRuntime

_CONNECT_STYLE = Style.from_dict(
    {
        "prompt": "ansicyan bold",
        "input": "ansiwhite",
        "footer": "#666666",
    }
)


@dataclass
class ConnectFlowResult:
    cancelled: bool
    runtime: CliRuntime | None = None
    renderable: Any = None


async def run_connect_flow(
    runtime: CliRuntime,
    *,
    stdout: TextIO | None = None,
) -> ConnectFlowResult:
    """Multi-step wizard: provider → key → model list → save."""

    from infrastructure.providers.catalog import ProviderDefinition
    from infrastructure.providers.model_catalog import (
        ProviderModel,
        fetch_models_for_connect,
        test_model_connection,
    )
    from ui.cli.connect import (
        ProviderEnvUpdate,
        existing_key_for_provider,
        list_connect_options,
    )
    from ui.cli.terminal.selector import SelectorItem, TransientSelector

    out = stdout if stdout is not None else sys.stdout
    if not can_enter_alternate_screen(out):
        return ConnectFlowResult(
            cancelled=True,
            renderable=Text(
                "请在真实终端中运行 OneCode 以配置供应商，或直接编辑 .env 文件。",
                style="onecode.warning",
            ),
        )

    options = list_connect_options()
    if not options:
        return ConnectFlowResult(
            cancelled=True,
            renderable=Text("没有可用的供应商。", style="onecode.warning"),
        )

    # ------------------------------------------------------------------
    # Step 1: Pick a provider.
    # ------------------------------------------------------------------
    selector: TransientSelector = TransientSelector(
        "选择供应商",
        tuple(
            SelectorItem(label=option.display_name, value=option)
            for option in options
        ),
    )
    chosen = await selector.run()
    if chosen is None:
        return ConnectFlowResult(cancelled=True)
    option = chosen.value

    # Resolve the ProviderDefinition from the catalog.
    from infrastructure.providers.catalog import get_provider_definition

    provider: ProviderDefinition = get_provider_definition(option.provider_id)

    # ------------------------------------------------------------------
    # Step 2a: Custom provider → input base URL first.
    # ------------------------------------------------------------------
    base_url: str | None = None
    if provider.requires_base_url:
        base_url = await _prompt_text("请输入 Base URL", out=out)
        if not base_url:
            return ConnectFlowResult(cancelled=True)
    else:
        base_url = provider.base_url or None

    # ------------------------------------------------------------------
    # Step 2b: API key handling — detect existing, K/R/C, or input new.
    # ------------------------------------------------------------------
    api_key: str = ""
    env_path = runtime.workspace / ".env"

    if provider.api_key_required:
        existing_key = existing_key_for_provider(env_path, provider.id)

        if existing_key:
            # Show K/R/C options.
            masked = _mask_key(existing_key)
            krc_selector: TransientSelector[str] = TransientSelector(
                f"已检测到 {provider.display_name} 的 API Key",
                (
                    SelectorItem(label=f"保留现有 Key ({masked})", value="keep"),
                    SelectorItem(label="替换为新的 Key", value="replace"),
                    SelectorItem(label="取消", value="cancel"),
                ),
            )
            krc_result = await krc_selector.run()
            if krc_result is None or krc_result.value == "cancel":
                return ConnectFlowResult(cancelled=True)
            if krc_result.value == "keep":
                api_key = existing_key
            else:
                # Replace: ask for new key.
                new_key = await _prompt_text("请输入新的 API Key", out=out, secret=True)
                if not new_key:
                    return ConnectFlowResult(cancelled=True)
                api_key = new_key
        else:
            # No existing key for this provider → ask for one.
            new_key = await _prompt_text(
                f"请输入 {provider.display_name} 的 API Key", out=out, secret=True,
            )
            if not new_key:
                return ConnectFlowResult(cancelled=True)
            api_key = new_key
    # else: Ollama — no key needed, api_key stays "".

    # ------------------------------------------------------------------
    # Step 3 & 4: Fetch model list → model selector (or manual fallback).
    # ------------------------------------------------------------------
    model: str | None = None

    try:
        models: tuple[ProviderModel, ...] = fetch_models_for_connect(
            provider, api_key, base_url,
        )
    except Exception:
        models = ()

    if models:
        model = await _prompt_model_selection(models, provider.display_name)
    
    if model is None:
        # Fallback: manual model name input + connection test.
        model = await _prompt_manual_model(
            provider, api_key, base_url, out=out,
        )
        if not model:
            return ConnectFlowResult(cancelled=True)

    # ------------------------------------------------------------------
    # Step 5: Save to .env and reload runtime.
    # ------------------------------------------------------------------
    write_provider_env(
        env_path,
        ProviderEnvUpdate(
            provider_id=provider.id,
            model=model,
            api_key=api_key,
            base_url=base_url,
        ),
    )
    new_runtime = runtime.with_model_config()
    return ConnectFlowResult(
        cancelled=False,
        runtime=new_runtime,
        renderable=Text(
            f"已连接到 {new_runtime.provider_label} ({new_runtime.model})。",
            style="onecode.success",
        ),
    )


async def _prompt_model_selection(
    models: tuple,
    provider_name: str,
) -> str | None:
    """Show an interactive model picker on the alternate screen."""

    from ui.cli.terminal.selector import SelectorItem, TransientSelector

    items = tuple(
        SelectorItem(
            label=m.id,
            value=m.id,
            detail=m.owned_by or "",
        )
        for m in models
    )
    selector: TransientSelector[str] = TransientSelector(
        f"选择 {provider_name} 模型",
        items,
    )
    result = await selector.run()
    return result.value if result is not None else None


async def _prompt_manual_model(
    provider: Any,
    api_key: str,
    base_url: str | None,
    *,
    out: TextIO,
) -> str | None:
    """Fallback: type a model name, then verify the connection."""

    from infrastructure.providers.model_catalog import test_model_connection

    model = await _prompt_text(
        "无法获取模型列表，请手动输入模型名称", out=out,
    )
    if not model:
        return None

    # Test connection with the manually entered model.
    error = test_model_connection(provider, api_key, model, base_url)
    if error is not None:
        # Show error and ask to retry or cancel.
        retry = await _prompt_confirm(
            f"连接测试失败: {error}\n是否重新输入模型名称？",
            out=out,
        )
        if retry:
            return await _prompt_manual_model(provider, api_key, base_url, out=out)
        return None
    return model


def _mask_key(key: str) -> str:
    """Return a masked version showing only the last 4 characters."""
    if len(key) <= 4:
        return "****"
    return f"****{key[-4:]}"


async def _prompt_confirm(
    prompt: str,
    *,
    out: TextIO,
) -> bool:
    """Simple yes/no confirmation on the alternate screen."""

    result: list[bool] = [False]
    bindings = KeyBindings()

    @bindings.add("y", eager=True)
    @bindings.add("Y", eager=True)
    @bindings.add(Keys.Enter, eager=True)
    def _on_yes(event) -> None:  # type: ignore[no-untyped-def]
        result[0] = True
        event.app.exit()

    @bindings.add("n", eager=True)
    @bindings.add("N", eager=True)
    @bindings.add(Keys.Escape, eager=True)
    @bindings.add(Keys.ControlC, eager=True)
    def _on_no(event) -> None:  # type: ignore[no-untyped-def]
        result[0] = False
        event.app.exit()

    def get_text():  # type: ignore[no-untyped-def]
        return FormattedText(
            [
                ("class:prompt", f"{prompt}\n\n"),
                ("class:footer", "Y 确认 · N 取消"),
            ]
        )

    window = Window(content=FormattedTextControl(get_text))
    app: Application[None] = Application(
        layout=Layout(HSplit([window])),
        full_screen=True,
        mouse_support=False,
        key_bindings=bindings,
    )
    await app.run_async()
    return result[0]


async def _prompt_text(
    prompt: str,
    *,
    out: TextIO,
    secret: bool = False,
    input=None,  # type: ignore[no-untyped-def]
    output=None,  # type: ignore[no-untyped-def]
) -> str | None:
    buffer = Buffer()
    result: list[str | None] = [None]
    bindings = KeyBindings()

    @bindings.add(Keys.Enter, eager=True)
    def _on_enter(event) -> None:  # type: ignore[no-untyped-def]
        result[0] = buffer.text
        event.app.exit()

    @bindings.add(Keys.Escape, eager=True)
    @bindings.add(Keys.ControlC, eager=True)
    def _on_cancel(event) -> None:  # type: ignore[no-untyped-def]
        result[0] = None
        event.app.exit()

    app = _build_text_prompt_application(
        prompt,
        result,
        secret=secret,
        input=input,
        output=output,
        key_bindings=bindings,
        buffer=buffer,
    )
    await app.run_async()
    return result[0]


def _build_text_prompt_application(
    prompt: str,
    result: list[str | None],
    *,
    secret: bool = False,
    input=None,  # type: ignore[no-untyped-def]
    output=None,  # type: ignore[no-untyped-def]
    key_bindings: KeyBindings | None = None,
    buffer: Buffer | None = None,
) -> Application[None]:
    buffer = buffer or Buffer()
    bindings = key_bindings or KeyBindings()

    def header_text():  # type: ignore[no-untyped-def]
        return FormattedText(
            [
                ("class:prompt", f"{prompt}\n"),
            ]
        )

    def footer_text():  # type: ignore[no-untyped-def]
        return FormattedText([("class:footer", "\nEnter 确认 · Esc 取消")])

    input_processors = []
    if secret:
        input_processors.append(PasswordProcessor())
    input_processors.append(BeforeInput("> ", style="class:prompt"))

    header = Window(
        content=FormattedTextControl(header_text),
        height=Dimension(min=1, max=1),
    )
    input_window = Window(
        content=BufferControl(
            buffer=buffer,
            input_processors=input_processors,
            include_default_input_processors=True,
        ),
        height=Dimension(min=1, max=1),
        wrap_lines=False,
        style="class:input",
    )
    footer = Window(content=FormattedTextControl(footer_text))

    # ``full_screen`` manages the alternate screen (DEC 1049) itself,
    # so the credential entry never leaks into the static scrollback.
    app: Application[None] = Application(
        layout=Layout(HSplit([header, input_window, footer]), focused_element=input_window),
        full_screen=True,
        mouse_support=False,
        key_bindings=bindings,
        input=input,
        output=output,
        style=_CONNECT_STYLE,
    )
    return app
