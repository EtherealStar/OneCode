"""M5.2 entrypoint routing tests: TTY -> TUI, non-TTY -> batch, redirect error."""

from __future__ import annotations

from pathlib import Path

from ui.cli import app as cli_app


class FakeTty:
    def __init__(self, is_tty: bool) -> None:
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        return None


def test_tty_stdin_and_stdout_run_tui(tmp_path: Path, monkeypatch) -> None:
    calls: list[Path] = []

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_app.sys, "stdin", FakeTty(True))
    monkeypatch.setattr(cli_app.sys, "stdout", FakeTty(True))
    monkeypatch.setattr(
        "ui.tui.app.run_tui", lambda workspace: calls.append(workspace) or 0
    )

    assert cli_app.main([]) == 0
    assert calls == [tmp_path]


def test_non_tty_stdin_uses_batch(tmp_path: Path, monkeypatch) -> None:
    calls: list[Path] = []

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_app.sys, "stdin", FakeTty(False))
    monkeypatch.setattr(cli_app.sys, "stdout", FakeTty(True))
    monkeypatch.setattr(
        "ui.cli.batch.run_batch", lambda workspace: calls.append(workspace) or 7
    )

    assert cli_app.main([]) == 7
    assert calls == [tmp_path]


def test_tty_stdin_redirected_stdout_is_an_error(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_app.sys, "stdin", FakeTty(True))
    monkeypatch.setattr(cli_app.sys, "stdout", FakeTty(False))

    assert cli_app.main([]) == 1
    err = capsys.readouterr().err
    assert "stdout is not a TTY" in err


def test_tui_startup_error_is_reported(tmp_path: Path, monkeypatch, capsys) -> None:
    def boom(workspace: Path) -> int:
        raise RuntimeError("tui failed")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_app.sys, "stdin", FakeTty(True))
    monkeypatch.setattr(cli_app.sys, "stdout", FakeTty(True))
    monkeypatch.setattr("ui.tui.app.run_tui", boom)

    assert cli_app.main([]) == 1
    assert "tui failed" in capsys.readouterr().err
