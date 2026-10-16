import json
from pathlib import Path

import pytest

from baton import pack, render
from baton.brief import build
from baton.model import Session, SessionRef, ToolCall, Turn
from baton.workspace import Workspace


def sample(tmp_path):
    session = Session(
        ref=SessionRef(harness="claude", id="s1", directory=str(tmp_path), title="Fix the parser"),
        model="opus",
        turns=[
            Turn(role="user", text="fix the parser", meta={"synthetic": False}),
            Turn(role="assistant", text="done, tests pass",
                 tools=[ToolCall(kind="edit", name="Edit", path="src/p.py"),
                        ToolCall(kind="run", name="Bash", command="pytest -q")]),
        ],
    )
    return build(session, Workspace(directory=str(tmp_path)))


def test_the_document_carries_what_the_next_agent_needs(tmp_path):
    text = render.markdown(sample(tmp_path), transcript_path="/x/t.json")
    for heading in ("# Handoff", "## What was asked", "## Files this session changed",
                    "## Commands it ran", "## Next step", "## The full transcript"):
        assert heading in text
    assert "fix the parser" in text
    assert "`src/p.py`" in text
    assert "No model wrote this" in text


def test_the_next_step_points_at_the_verification_the_old_agent_used(tmp_path):
    assert "pytest -q" in render.markdown(sample(tmp_path))


def test_writing_a_handoff_leaves_a_document_a_transcript_and_a_latest_pointer(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    paths = pack.write(sample(tmp_path), str(tmp_path))
    document = pack.default_out_dir(str(tmp_path))
    assert (document / "latest.md").exists()
    assert paths["document"].endswith(".md")

    payload = json.loads(Path(paths["transcript"]).read_text())
    assert payload["harness"] == "claude"
    assert payload["turns"][0]["human"] is True
    assert payload["turns"][1]["tools"][0]["path"] == "src/p.py"


def test_resume_commands_carry_the_handoff_into_each_harness(tmp_path, monkeypatch):
    monkeypatch.setattr(pack.shutil, "which", lambda name: f"/usr/bin/{name}")
    for harness in ("claude", "opencode", "codex", "hermes"):
        command = pack.resume_command(harness, "/x/handoff.md", str(tmp_path))
        assert command[0] == harness
        assert any("/x/handoff.md" in part for part in command)


def test_an_unknown_or_missing_harness_is_refused_with_a_readable_error(tmp_path, monkeypatch):
    with pytest.raises(pack.BatonError, match="unknown harness"):
        pack.resume_command("nope", "/x.md", None)
    monkeypatch.setattr(pack.shutil, "which", lambda name: None)
    with pytest.raises(pack.BatonError, match="not on PATH"):
        pack.resume_command("claude", "/x.md", None)


def test_picking_a_session_that_does_not_exist_is_refused(tmp_path):
    with pytest.raises(pack.BatonError):
        pack.pick(str(tmp_path), session_id="nope")


def _ref(harness, sid, messages, updated):
    return SessionRef(harness=harness, id=sid, directory="/w", updated=updated, messages=messages)


def test_picking_skips_a_one_turn_stub_and_takes_the_last_real_session(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pack,
        "sessions_for",
        lambda directory, harnesses=None: [
            _ref("opencode", "stub", 1, "2026-09-16T20:00:00"),
            _ref("opencode", "real", 40, "2026-09-16T19:00:00"),
        ],
    )
    assert pack.pick(str(tmp_path)).id == "real"


def test_picking_falls_back_to_the_newest_when_every_session_is_a_stub(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pack,
        "sessions_for",
        lambda directory, harnesses=None: [
            _ref("opencode", "newest", 1, "2026-09-16T20:00:00"),
            _ref("opencode", "older", 2, "2026-09-16T19:00:00"),
        ],
    )
    assert pack.pick(str(tmp_path)).id == "newest"


def test_install_links_the_skill_only_into_harnesses_that_exist(tmp_path):
    from baton import install as install_mod

    present = tmp_path / ".claude" / "skills"
    present.parent.mkdir(parents=True)
    absent = tmp_path / ".nothing" / "skills"

    results = {
        name: outcome
        for name, outcome, _ in install_mod.install({"claude": present, "ghost": absent})
    }
    assert results == {"claude": "linked", "ghost": "skipped"}
    assert (present / "session-handoff" / "SKILL.md").is_file()
    assert not absent.exists()


def test_install_is_idempotent_and_never_clobbers_a_real_directory(tmp_path):
    from baton import install as install_mod

    skills = tmp_path / ".claude" / "skills"
    skills.parent.mkdir(parents=True)
    assert install_mod.install({"claude": skills})[0][1] == "linked"
    assert install_mod.install({"claude": skills})[0][1] == "already linked"

    other = tmp_path / ".codex" / "skills"
    (other / "session-handoff").mkdir(parents=True)
    assert install_mod.install({"codex": other})[0][1] == "left alone"


def test_private_handoffs_stay_outside_project_and_redact_credentials(tmp_path, monkeypatch):
    import os
    import stat
    from baton.model import Turn
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    brief = sample(project)
    credential = "ghp_" + "a" * 36
    brief.session.turns.append(Turn(role="user", text=f"Authorization: Bearer {credential} password=private-example"))
    brief = build(brief.session, brief.workspace)
    old_umask = os.umask(0)
    try:
        paths = pack.write(brief, str(project), minimal=True)
    finally:
        os.umask(old_umask)
    assert not (project / ".baton").exists()
    for name in ("document", "transcript"):
        path = Path(paths[name])
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        text = path.read_text()
        assert credential not in text
        assert "private-example" not in text
        assert "REDACTED" in text


def test_explicit_export_does_not_follow_preexisting_file_symlinks(tmp_path):
    brief = sample(tmp_path)
    output = tmp_path / "exports"
    output.mkdir()
    target = tmp_path / "unrelated"
    target.write_text("keep")
    (output / "latest.md").symlink_to(target)
    paths = pack.write(brief, str(tmp_path), str(output))
    assert target.read_text() == "keep"
    assert Path(paths["latest"]).resolve() == Path(paths["document"])
