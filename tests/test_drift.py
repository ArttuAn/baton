"""Regressions for vendor-format drift and privacy edge cases."""

import json
import os
import stat

import pytest

from baton import brief, pack, picker, privacy
from baton.brief import build
from baton.model import Session, SessionRef, ToolCall, Turn
from baton.readers import claude, codex, hermes, opencode
from baton.workspace import Workspace


def _brief(tmp_path):
    session = Session(
        ref=SessionRef(harness="claude", id="s1", directory=str(tmp_path), title="Fix the parser"),
        model="opus",
        turns=[Turn(role="user", text="fix the parser", meta={"synthetic": False})],
    )
    return build(session, Workspace(directory=str(tmp_path)))


def test_opencode_apply_patch_and_websearch_are_recognised():
    part = {
        "tool": "apply_patch",
        "state": {
            "input": {
                "patchText": "*** Begin Patch\n*** Update File: src/p.py\n@@\n-old\n+new\n*** End Patch"
            }
        },
    }
    call = opencode._tool_call(part)
    assert call.kind == "edit"
    assert call.path == "src/p.py"
    assert opencode.TOOL_KINDS["websearch"] == "search"


def test_hermes_current_tool_names_are_recognised():
    arguments = json.dumps(
        [
            {"function": {"name": "patch", "arguments": {"path": "src/p.py"}}},
            {"function": {"name": "terminal", "arguments": {"command": "pytest -q"}}},
            {"function": {"name": "search_files", "arguments": {"query": "needle"}}},
        ]
    )
    calls = hermes._tool_calls(arguments, None)
    assert [call.kind for call in calls] == ["edit", "run", "search"]
    assert calls[0].path == "src/p.py"


def test_payload_redaction_uses_the_same_secret_vocabulary_as_text():
    payload = {
        "headers": {"X-API-Key": "xapikey-very-secret", "X-Auth-Token": "authtoken-very-secret"},
        "env": {"Passwd": "pw-very-secret", "AccessKey": "ak-very-secret"},
    }
    redacted = privacy.redact_payload(payload)
    assert all(value == "[REDACTED]" for group in redacted.values() for value in group.values())


def test_heredoc_body_with_a_trailing_comment_stays_hidden():
    command = (
        "cat > config.env <<'EOF'  # write the config\n"
        "export MY_VAR=supersecretvalue\n"
        "another_private_value\n"
        "EOF\n"
        "pytest -q"
    )
    headline = brief._headline(command)
    assert "supersecretvalue" not in headline
    assert "another_private_value" not in headline
    assert "pytest -q" in headline
    assert "[+2 lines of inline script]" in headline


def test_cd_on_its_own_line_changes_the_path_base(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "main.py").write_text("x = 1\n")
    calls = [ToolCall(kind="run", name="Bash", command="pwd\ncd pkg\nsed -i s/a/b/ main.py")]
    written, _ = brief._mined_paths(calls, Workspace(directory=str(tmp_path)))
    assert written == ["pkg/main.py"]


def test_parent_directories_of_private_exports_are_private(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    previous = os.umask(0)
    try:
        pack.write(_brief(tmp_path), str(tmp_path))
    finally:
        os.umask(previous)
    base = pack.default_out_dir(str(tmp_path))
    for path in (state, state / "baton", base):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700, path


def test_codex_home_is_honoured(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    assert codex.root() == home / "sessions"


def test_picker_uses_claude_config_dir_and_survives_bad_records(tmp_path, monkeypatch):
    config = tmp_path / "claude"
    records = config / "sessions"
    records.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setattr(picker.os, "readlink", lambda _: "/project")

    (records / "4242.json").write_text(json.dumps({"sessionId": "real-session-id", "cwd": "/project"}))
    assert picker._resolve(4242, "claude", "some title").session_id == "real-session-id"

    (records / "4242.json").write_text("{")
    assert picker._resolve(4242, "claude", "some title").session_id is None


def test_exact_id_beats_a_longer_prefix(monkeypatch, tmp_path):
    refs = [
        SessionRef("claude", "ses_abcd", str(tmp_path)),
        SessionRef("claude", "ses_abc", str(tmp_path)),
    ]
    monkeypatch.setattr(pack, "sessions_for", lambda *args, **kwargs: refs)
    assert pack.pick(str(tmp_path), session_id="ses_abc").id == "ses_abc"


def test_ambiguous_prefix_is_refused(monkeypatch, tmp_path):
    refs = [
        SessionRef("claude", "ses_abcd", str(tmp_path)),
        SessionRef("claude", "ses_abcx", str(tmp_path)),
    ]
    monkeypatch.setattr(pack, "sessions_for", lambda *args, **kwargs: refs)
    with pytest.raises(pack.BatonError, match="ambiguous"):
        pack.pick(str(tmp_path), session_id="ses_abc")


def test_claude_task_tools_build_a_live_plan():
    session = Session(
        ref=SessionRef("claude", "s1", "/p"),
        turns=[
            Turn(
                role="assistant",
                tools=[
                    claude._tool_call({"name": "TaskCreate", "input": {"taskId": "1", "subject": "Do A"}}),
                    claude._tool_call(
                        {"name": "TaskUpdate", "input": {"taskId": "2", "subject": "Do B", "status": "in_progress"}}
                    ),
                    claude._tool_call({"name": "TaskUpdate", "input": {"taskId": "1", "status": "completed"}}),
                ],
            )
        ],
    )
    assert build(session, Workspace(directory="/p")).todos == [
        {"content": "Do A", "status": "completed"},
        {"content": "Do B", "status": "in_progress"},
    ]
