"""Malformed session-store data must not hide sessions or break a handoff."""

import json
import os

import pytest

from baton import brief, pack, workspace
from baton.brief import build
from baton.model import Session, SessionRef, ToolCall, Turn
from baton.readers import claude, codex, hermes, opencode
from baton.workspace import Workspace


def test_claude_malformed_lines_do_not_hide_other_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(claude, "root", lambda: tmp_path / "store")
    folder = claude.root() / claude.slug(str(tmp_path))
    folder.mkdir(parents=True)
    lines = [
        '{"type":"user","cwd":["bad"],"message":{"role":"user"}}',
        "123",
        json.dumps(
            {
                "type": "user",
                "cwd": str(tmp_path),
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"role": "user", "content": "hello"},
            }
        ),
    ]
    (folder / "s.jsonl").write_text("\n".join(lines) + "\n")

    refs = claude.list_sessions(str(tmp_path))
    assert [ref.id for ref in refs] == ["s"]
    assert claude.load(refs[0]).human_prompts()[0].text == "hello"


def test_opencode_bad_values_are_ignored():
    assert opencode._stamp("not-a-number") is None
    assert opencode._stamp(10**30) is None
    assert opencode._json("[1,2]") is None
    call = opencode._tool_call({"tool": "edit", "state": {"input": {"filePath": ["bad"], "command": 42}}})
    assert call.path is None
    assert call.command is None


def test_codex_non_object_tool_arguments_are_ignored():
    call = codex._tool_call({"type": "function_call", "arguments": "[1,2]"})
    assert call.path is None
    assert call.command is None


def test_hermes_non_object_tool_rows_are_ignored():
    raw = json.dumps([{"function": {"name": "patch", "arguments": [1, 2]}}, 42])
    calls = hermes._tool_calls(raw, "patch")
    assert len(calls) == 1
    assert calls[0].name == "patch"
    assert calls[0].path is None


def test_git_output_with_invalid_utf8_does_not_crash(tmp_path, monkeypatch):
    fake_git = tmp_path / "git"
    fake_git.write_text('#!/bin/sh\nprintf "\\377 bad subject\\n"\n')
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    assert workspace.inspect(str(tmp_path)) is not None


def test_closing_heredoc_delimiter_with_a_comment_keeps_later_commands():
    lines, hidden = brief._split_shell(
        ["cat > f <<'EOF' # note", "abc", "EOF # done", "pytest -q"]
    )
    assert lines == ["cat > f <<'EOF' # note", "pytest -q"]
    assert hidden == 1


def test_todo_fields_are_strings_before_rendering():
    call = ToolCall(kind="todo", name="TodoWrite", extra={"todos": [{"content": "step", "status": ["running"]}]})
    todos = brief._todos([call])
    assert all(isinstance(value, str) for item in todos for value in item.values())


def test_latest_md_directory_is_refused_before_writing(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    (out / "latest.md").mkdir()
    session = Session(
        ref=SessionRef("claude", "s1", str(tmp_path)),
        turns=[Turn(role="user", text="fix it", meta={"synthetic": False})],
    )
    handoff = build(session, Workspace(directory=str(tmp_path)))
    with pytest.raises(pack.BatonError, match="directory"):
        pack.write(handoff, str(tmp_path), str(out))
