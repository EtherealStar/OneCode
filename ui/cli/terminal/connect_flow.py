"""``/connect`` wizard — multi-step provider configuration.

The wizard lives entirely on the alternate screen so the user can
type their API key without polluting the inline scrollback. It is
deliberately minimal per execplan §M5: pick a provider, type model +
API key (+ base URL when required), persist via
:func:`ui.cli.connect.write_provider_env`, and reload the model client
via :meth:`ui.cli.types.CliRuntime.with_model_config`.
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
from prompt_toolkit.layout.controls import FormattedTextControl
from rich.text import Text

from ui.cli.connect import write_provider_env
from ui.cli.terminal.transient import can_enter_alternate_screen
from ui.cli.types import CliRuntime


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
    """Select a provider, collect credentials, then reload the runtime.

    A "simple" wizard per execplan §M5: pick a provider from the
    catalog, type model + API key (+ base URL when the provider needs
    one), persist them via :func:`write_provider_env`, and rebuild the
    model client with :meth:`CliRuntime.with_model_config`.
    """

    from ui.cli.connect import ProviderEnvUpdate, list_connect_options
    from ui.cli.terminal.selector import SelectorItem, TransientSelector

    out = stdout if stdout is not None else sys.stdout
    if not can_enter_alternate_screen(out):
        # Non-TTY hosts can't render the wizard. Ask the user to edit
        # ``.env`` directly rather than failing silently.
        return ConnectFlowResult(
            cancelled=True,
            renderable=Text(
                "Run OneCode in a real terminal to configure provider "
                "credentials, or edit .env directly.",
                style="onecode.warning",
            ),
        )

    options = list_connect_options()
    if not options:
        return ConnectFlowResult(
            cancelled=True,
            renderable=Text("No providers available to connect.", style="onecode.warning"),
        )

    # Step 1: pick a provider.
    selector: TransientSelector = TransientSelector(
        "Connect a provider",
        tuple(
            SelectorItem(label=option.display_name, value=option, detail=option.provider_id)
            for option in options
        ),
    )
    chosen = await selector.run()
    if chosen is None:
        return ConnectFlowResult(cancelled=True)
    option = chosen.value

    # Step 2: model name.
    model = await _prompt_text(f"Model for {option.display_name}", out=out)
    if not model:
        return ConnectFlowResult(cancelled=True)
    # Step 3: API key (masked).
    api_key = await _prompt_text("API key", out=out, secret=True)
    if not api_key:
        return ConnectFlowResult(cancelled=True)
    # Step 4: base URL when required.
    base_url: str | None = None
    if option.requires_base_url:
        base_url = await _prompt_text("Base URL", out=out)
        if not base_url:
            return ConnectFlowResult(cancelled=True)

    write_provider_env(
        runtime.workspace / ".env",
        ProviderEnvUpdate(
            provider_id=option.provider_id,
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
            f"Connected to {new_runtime.provider_label} ({new_runtime.model}).",
            style="onecode.success",
        ),
    )


async def _prompt_text(
    prompt: str,
    *,
    out: TextIO,
    secret: bool = False,
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

    def get_text():  # type: ignore[no-untyped-def]
        masked = "*" * len(buffer.text) if secret else buffer.text
        return FormattedText(
            [
                ("class:prompt", f"{prompt}\n"),
                ("class:input", f"> {masked}\n"),
                ("class:footer", "\nEnter to confirm · Esc to cancel"),
            ]
        )

    # ``full_screen`` manages the alternate screen (DEC 1049) itself,
    # so the credential entry never leaks into the static scrollback.
    window = Window(content=FormattedTextControl(get_text))
    app: Application[None] = Application(
        layout=Layout(HSplit([window])),
        full_screen=True,
        mouse_support=False,
        key_bindings=bindings,
    )
    await app.run_async()
    return result[0]