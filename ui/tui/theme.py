"""OneCode TUI visual tokens and theme registration.

The palette is the single code mapping for the full-screen TUI. Renderers
only reference named styles from :data:`RICH_STYLES`; they never hard-code
colours. Textual layout tokens live on :data:`ONECODE_THEME` so widget CSS can
use ``$primary``/``$surface``/``$text-muted`` and friends.
"""

from __future__ import annotations

from rich.theme import Theme as RichTheme
from textual.app import App
from textual.theme import Theme

# --- palette ---------------------------------------------------------------

BG = "#1A1B20"
SURFACE = "#23252D"
SURFACE_2 = "#2A2D37"
TEXT = "#E6E8EE"
MUTED = "#989CA8"
ACCENT = "#7AA2F7"
ACCENT_2 = "#E0A35E"
TOOL = "#6FC3CF"
SUCCESS = "#98C379"
ERROR = "#E06C75"

ONECODE_THEME = Theme(
    name="onecode",
    primary=ACCENT,
    secondary=TOOL,
    accent=ACCENT_2,
    warning=ACCENT_2,
    error=ERROR,
    success=SUCCESS,
    foreground=TEXT,
    background=BG,
    surface=SURFACE,
    panel=SURFACE_2,
    dark=True,
    variables={
        # Override Textual's derived alpha values so ``$text-muted`` hits the
        # token exactly.
        "text-muted": MUTED,
        "tool": TOOL,
        "surface-2": SURFACE_2,
    },
)

#: Named Rich styles shared by the renderers and Rich Markdown.
RICH_STYLES: dict[str, str] = {
    "ui.label.user": MUTED,
    "ui.label.agent": f"bold {ACCENT}",
    "ui.reasoning": f"italic {MUTED}",
    "ui.tool": TOOL,
    "ui.tool.summary": MUTED,
    "ui.success": SUCCESS,
    "ui.error": ERROR,
    "ui.queued": f"italic {MUTED}",
    "ui.queued.tag": ACCENT_2,
    "ui.meta": MUTED,
    "ui.attachment": TOOL,
    "ui.status.model": ACCENT,
    "ui.todo": MUTED,
    "ui.todo.pending": MUTED,
    "ui.todo.active": TOOL,
    "ui.todo.active.text": TEXT,
    "ui.todo.done": MUTED,
    "ui.todo.done.glyph": SUCCESS,
    # Rich's Markdown renderer only honours ``markdown.*`` named styles.
    "markdown.h1": f"bold {TEXT}",
    "markdown.h2": f"bold {TEXT}",
    "markdown.h3": f"bold {MUTED}",
    "markdown.h4": f"bold {MUTED}",
    "markdown.h5": f"bold {MUTED}",
    "markdown.h6": f"bold {MUTED}",
    "markdown.code": f"{ACCENT_2} on {SURFACE}",
    "markdown.code_block": f"{TEXT} on {SURFACE}",
    "markdown.link": f"underline {ACCENT}",
    "markdown.block_quote": f"italic {MUTED}",
    "markdown.hr": MUTED,
}


def _detect_code_theme() -> str:
    try:
        from pygments.styles import get_style_by_name

        get_style_by_name("one-dark")
        return "one-dark"
    except Exception:  # pragma: no cover - depends on installed pygments
        return "monokai"


MARKDOWN_CODE_THEME = _detect_code_theme()


def apply_theme(app: App[object]) -> None:
    """Register and select the OneCode theme, injecting named Rich styles."""

    app.register_theme(ONECODE_THEME)
    app.theme = ONECODE_THEME.name
    app.console.push_theme(RichTheme(RICH_STYLES))


__all__ = [
    "ACCENT",
    "ACCENT_2",
    "BG",
    "ERROR",
    "MARKDOWN_CODE_THEME",
    "MUTED",
    "ONECODE_THEME",
    "RICH_STYLES",
    "SUCCESS",
    "SURFACE",
    "SURFACE_2",
    "TEXT",
    "TOOL",
    "apply_theme",
]
