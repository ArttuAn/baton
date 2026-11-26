"""Third-pass regressions: untrusted text and hostile local metadata."""

import json
import os
import shlex
import subprocess
from types import SimpleNamespace

import pytest

from baton import brief, cli, install, pack, picker, render, workspace
from baton.brief import build
from baton.model import Session, SessionRef, ToolCall, Turn
from baton.privacy import redact_payload, redact_text, safe_text
from baton.readers import claude, codex
from baton.workspace import Workspace


def test_cli_style_and_block_secrets_are_redacted():
    assert redact_text("--password hunter2") == "--password [REDACTED]"
    assert "hunter2" not in redact_text("machine example.com login bob password hunter2")
    assert "hunter2" not in redact_text("password: |\n  hunter2\n  second\n")
    assert "hunter2" not in redact_text('{"password": ["hunter2", "second"]}')
    assert "hunter2" not in redact_text(r'{"cfg":"{\"password\":\"hunter2\"}"}')
    assert redact_payload({"tool_output": r'{"cfg":"{\"password\":\"hunter2\"}"}'})["tool_output"].find("hunter2") == -1


def test_common_credential_formats_are_redacted():
    assert "dXNlcjpwYXNzd29yZA==" not in redact_text("auth: Basic dXNlcjpwYXNzd29yZA==")
    # Built at runtime: a literal provider-shaped token trips secret scanners.
    stripe = "sk_live_" + "abcdefghijklmnopqrstuvwxyz"
    gitlab = "glpat-" + "abcdefghijklmnopqrstuvwxyz"
    assert stripe not in redact_text(f"key: {stripe}")
    assert gitlab not in redact_text(f"key: {gitlab}")


def test_encoded_secret_keys_are_decoded_before_matching():
    assert redact_text(r'{"pa\U00000073sword": "hunter2"}') == '{"password": "[REDACTED]"}'
    assert "hunter2" not in redact_text("password%3A%20hunter2")
    assert "unter2" not in redact_text("password: &#104;unter2")


def test_error_messages_cannot_inject_terminal_escapes(monkeypatch, capsys):
    def explode(args):
        raise pack.BatonError("ambiguous session 's\x1b]52;c;c2VjcmV0\x07'")

    monkeypatch.setattr(cli, "cmd_sessions", lambda args: explode(args))
    assert cli.main(["sessions"]) == 2
    assert "\x1b" not in capsys.readouterr().err


def test_surrogate_escapes_stay_encoded(tmp_path):
    text = redact_text("emoji \\ud800and\\udc00")
    assert "\\ud800" in text and "\ud800" not in text
    assert safe_text(chr(0x202E) + "\x1b[31m") == r"\u202e\u001b[31m"


def test_cli_output_keeps_terminal_controls_inert(monkeypatch, capsys):
    class Thin:
        def is_thin(self):
            return False

    monkeypatch.setattr(
        cli, "_do_pack", lambda args: ({"document": "/x/\x1b]52;c;c2VjcmV0\x07"}, Thin())
    )
    assert cli.cmd_pack(SimpleNamespace()) == 0
    assert "\x1b" not in capsys.readouterr().out


def test_claude_tool_argument_types_cannot_break_a_brief():
    call = claude._tool_call(
        {"name": {"bad": "name"}, "input": {"file_path": ["a.py"], "command": {"x": 1}}}
    )
    assert call.path is None and call.command is None
    session = Session(ref=SessionRef("claude", "s", "/p"), turns=[Turn(role="assistant", tools=[call])])
    assert build(session, Workspace(directory="/p")).edited == []


def test_codex_tool_failures_surface_and_meta_skips_bad_lines(tmp_path):
    path = tmp_path / "rollout-x.jsonl"
    lines = [
        "",
        json.dumps({"type": "session_meta", "payload": {"cwd": str(tmp_path), "model": "gpt"}}),
        json.dumps(
            {
                "type": "response_item",
                "payload": {"type": "function_call", "name": "shell", "call_id": "c1", "arguments": "{}"},
            }
        ),
        json.dumps(
            {
                "type": "response_item",
                "payload": {"type": "function_call_output", "call_id": "c1", "output": "FAILED: 3 tests"},
            }
        ),
    ]
    path.write_text("\n".join(lines) + "\n")
    ref = SessionRef("codex", "x", str(tmp_path), source=str(path))
    assert codex._meta(path)["model"] == "gpt"
    session = codex.load(ref)
    call = session.tool_calls()[0]
    assert call.ok is False
    assert build(session, Workspace(directory=str(tmp_path))).failures


def test_repository_fsmonitor_is_not_executed(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    marker = tmp_path / "fsmonitor-ran"
    script = tmp_path / "fsmonitor.sh"
    script.write_text("#!/bin/sh\ntouch " + shlex.quote(str(marker)) + "\n")
    script.chmod(0o755)
    subprocess.run(["git", "-C", str(tmp_path), "config", "core.fsmonitor", str(script)], check=True)
    workspace.inspect(str(tmp_path))
    assert not marker.exists()


def test_heredoc_bodies_do_not_move_cwd_or_mark_files_edited(tmp_path):
    (tmp_path / "config.py").write_text("")
    (tmp_path / "main.py").write_text("")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "main.py").write_text("")
    calls = [
        ToolCall(
            kind="run",
            name="bash",
            command="cat > config.py <<'EOF'\ncd pkg\nvalue = 8 >> 2\nEOF",
        ),
        ToolCall(kind="run", name="bash", command="cat main.py"),
    ]
    written, read = brief._mined_paths(calls, Workspace(directory=str(tmp_path)))
    assert "config.py" in written
    assert "main.py" in read
    assert "pkg/main.py" not in read


def test_git_quoted_dirty_paths_are_decoded():
    assert brief._unquote_git_path('"a\\012b.txt"') == "a\nb.txt"
    assert brief._unquote_git_path('"x\\"y.txt"') == 'x"y.txt'


def test_unusable_export_paths_raise_baton_error(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    session = Session(
        ref=SessionRef("claude", "s1", str(tmp_path)),
        turns=[Turn(role="user", text="fix it", meta={"synthetic": False})],
    )
    handoff = build(session, Workspace(directory=str(tmp_path)))
    with pytest.raises(pack.BatonError, match="not a directory"):
        pack.write(handoff, str(tmp_path), str(blocker / "out"))


def test_handoff_refuses_an_unknown_target_before_writing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        pack,
        "resume_command",
        lambda *a, **k: (_ for _ in ()).throw(pack.BatonError("unknown harness")) if not calls else [],
    )
    monkeypatch.setattr(cli, "_do_pack", lambda args: calls.append("packed") or ({}, None))
    with pytest.raises(pack.BatonError, match="unknown harness"):
        cli.cmd_handoff(SimpleNamespace(target="nope", dir=str(tmp_path)))
    assert calls == []


def test_install_does_not_clobber_an_unrelated_symlink(tmp_path):
    target = tmp_path / "harness" / "skills"
    target.parent.mkdir()
    target.mkdir()
    own_skill = tmp_path / "my-skill"
    own_skill.mkdir()
    (own_skill / "SKILL.md").write_text("mine")
    link = target / "session-handoff"
    link.symlink_to(own_skill, target_is_directory=True)
    results = install.install({"mine": target})
    assert results[0][1] == "left alone"
    assert link.resolve() == own_skill


def test_inline_code_values_cannot_inject_a_heading(tmp_path):
    hostile = "p`\n## INJECTED HEADING\n`q"
    assert "\n## INJECTED" not in render._inline(hostile)
    session = Session(
        ref=SessionRef("claude", "s1", str(tmp_path)),
        turns=[Turn(role="user", text="fix it", meta={"synthetic": False})],
    )
    handoff = build(session, Workspace(directory=str(tmp_path)))
    handoff.edited = [hostile]
    assert "\n## INJECTED HEADING" not in render.markdown(handoff)


def test_picker_ignores_malformed_pid_records(monkeypatch, tmp_path):
    pid = os.getpid()
    (tmp_path / f"{pid}.json").write_text(json.dumps({"sessionId": {"bad": True}, "cwd": ["x"]}))
    monkeypatch.setattr(picker, "_claude_sessions", lambda: tmp_path)
    monkeypatch.setattr(picker, "_by_title", lambda *a, **k: None)
    picked = picker._resolve(pid, "claude", "title")
    assert picked.session_id is None
    assert picked.directory == os.readlink(f"/proc/{pid}/cwd")
