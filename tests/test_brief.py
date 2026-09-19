from baton import brief as brief_mod
from baton.model import Session, SessionRef, ToolCall, Turn
from baton.workspace import Workspace


def session_with(*turns: Turn) -> Session:
    return Session(ref=SessionRef(harness="claude", id="s1", directory="/tmp"), turns=list(turns))


def space(tmp_path) -> Workspace:
    return Workspace(directory=str(tmp_path))


def test_asks_are_verbatim_and_exclude_tool_noise(tmp_path):
    session = session_with(
        Turn(role="user", text="build the thing", meta={"synthetic": False}),
        Turn(role="assistant", text="ok"),
        Turn(role="user", text="tool result blob", meta={"synthetic": True}),
        Turn(role="user", text="now fix the bug", meta={"synthetic": False}),
    )
    out = brief_mod.build(session, space(tmp_path))
    assert out.asks == ["build the thing", "now fix the bug"]


def test_heredoc_bodies_are_stripped_but_the_check_after_them_survives():
    command = "python3 - <<'PY'\nprint('lots')\nprint('of work')\nPY\nnpm test -- --run"
    assert brief_mod._headline(command) == "python3 - <<'PY' ; npm test -- --run  [+2 lines of inline script]"


def test_nested_heredocs_do_not_leak_into_the_headline():
    command = "cat > a.py <<'EOF'\nbody\nEOF\ncat > b.py <<'EOF'\nmore\nEOF\npytest -q"
    headline = brief_mod._headline(command)
    assert "body" not in headline and "more" not in headline
    assert "pytest -q" in headline


def test_a_command_writing_a_file_marks_it_changed_not_merely_read(tmp_path):
    (tmp_path / "out.txt").write_text("x")
    (tmp_path / "ref.txt").write_text("y")
    session = session_with(
        Turn(role="assistant", tools=[ToolCall(kind="run", name="bash", command="echo hi > out.txt")]),
        Turn(role="assistant", tools=[ToolCall(kind="run", name="bash", command="grep x ref.txt")]),
    )
    out = brief_mod.build(session, space(tmp_path))
    assert "out.txt" in out.edited
    assert "ref.txt" in out.read and "ref.txt" not in out.edited


def test_redirecting_to_dev_null_is_not_a_write(tmp_path):
    (tmp_path / "ref.txt").write_text("y")
    session = session_with(
        Turn(role="assistant", tools=[ToolCall(kind="run", name="bash", command="cat ref.txt 2>/dev/null")])
    )
    out = brief_mod.build(session, space(tmp_path))
    assert out.edited == []
    assert "ref.txt" in out.read


def test_relative_paths_resolve_against_the_directory_the_session_cd_ed_into(tmp_path):
    nested = tmp_path / "pkg"
    nested.mkdir()
    (nested / "main.py").write_text("x")
    session = session_with(
        Turn(role="assistant", tools=[ToolCall(kind="run", name="bash", command="cd pkg && sed -i s/a/b/ main.py")])
    )
    out = brief_mod.build(session, space(tmp_path))
    assert "pkg/main.py" in out.edited


def test_a_rate_limited_ending_is_named_as_the_reason_it_stopped(tmp_path):
    session = session_with(Turn(role="assistant", text="Error from provider: Rate limit exceeded."))
    out = brief_mod.build(session, space(tmp_path))
    assert out.stopped_because == "the provider rate-limited the session"


def test_the_live_plan_comes_from_the_last_todo_write(tmp_path):
    session = session_with(
        Turn(role="assistant", tools=[ToolCall(kind="todo", name="TodoWrite", extra={"todos": [{"content": "old", "status": "completed"}]})]),
        Turn(role="assistant", tools=[ToolCall(kind="todo", name="TodoWrite", extra={"todos": [{"content": "new", "status": "in_progress"}]})]),
    )
    out = brief_mod.build(session, space(tmp_path))
    assert out.todos == [{"content": "new", "status": "in_progress"}]


def test_a_session_with_nothing_in_it_is_reported_as_thin(tmp_path):
    assert brief_mod.build(session_with(), space(tmp_path)).is_thin()
