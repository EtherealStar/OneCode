from __future__ import annotations

from onecode.config import load_config


def test_load_config_rejects_empty_base_url(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "ONECODE_MODEL=test-model\nONECODE_BASE_URL=\nONECODE_API_KEY=test-key\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("ONECODE_MODEL", raising=False)
    monkeypatch.delenv("ONECODE_BASE_URL", raising=False)
    monkeypatch.delenv("ONECODE_API_KEY", raising=False)

    try:
        load_config(tmp_path)
    except ValueError as exc:
        assert "ONECODE_BASE_URL is required and is currently empty" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_load_config_reads_env_file_base_url(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "ONECODE_MODEL=test-model\n"
        "ONECODE_BASE_URL=https://server.example/v1\n"
        "ONECODE_API_KEY=test-key\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("ONECODE_MODEL", raising=False)
    monkeypatch.delenv("ONECODE_BASE_URL", raising=False)
    monkeypatch.delenv("ONECODE_API_KEY", raising=False)

    config = load_config(tmp_path)

    assert config.model == "test-model"
    assert config.base_url == "https://server.example/v1"
    assert config.api_key == "test-key"
