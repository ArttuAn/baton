from types import SimpleNamespace

import pytest

from baton import cli, pack
from baton.model import SessionRef
from baton.pack import BatonError

REF = SessionRef("claude", "abc123", "/tmp/project", "A prompt", "2026-01-01", 3)


def test_sessions_rejects_non_positive_limit(monkeypatch):
    monkeypatch.setattr(pack, "sessions_for", lambda _dir: [REF])
    for limit in (0, -1):
        with pytest.raises(BatonError, match="--limit"):
            cli.cmd_sessions(SimpleNamespace(dir="/tmp/project", limit=limit))


def test_sessions_lists_a_real_ref(monkeypatch, capsys):
    monkeypatch.setattr(pack, "sessions_for", lambda _dir: [REF])
    assert cli.cmd_sessions(SimpleNamespace(dir="/tmp/project", limit=1)) == 0
    assert "abc123" in capsys.readouterr().out


def test_pick_rejects_non_positive_timeout(monkeypatch):
    monkeypatch.setattr(cli.picker, "pick", lambda *_: pytest.fail("should not click"))
    with pytest.raises(BatonError, match="--timeout"):
        cli.cmd_pick(SimpleNamespace(timeout=0))


def test_setup_errors_exit_two(monkeypatch, capsys):
    monkeypatch.setattr(pack, "sessions_for", lambda _dir: [REF])
    assert cli.main(["sessions", "--limit", "0"]) == 2
    assert "--limit" in capsys.readouterr().err


def test_help_lists_every_command(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    for command in ("doctor", "sessions", "pick", "pack", "resume", "handoff", "install", "continue"):
        assert command in help_text
