"""Untrusted session text must not become handoff structure."""

from baton.brief import build
from baton.model import Session, SessionRef, ToolCall, Turn
from baton.render import _cell, _fence, markdown
from baton.workspace import Workspace


def _render(tmp_path, *, title=None, ask=None, command=None):
    turns = []
    if ask is not None:
        turns.append(Turn(role="user", text=ask, meta={"synthetic": False}))
    if command is not None:
        turns.append(Turn(role="assistant", tools=[ToolCall(kind="run", name="Bash", command=command)]))
    session = Session(ref=SessionRef("claude", "s1", str(tmp_path), title=title), turns=turns)
    return markdown(build(session, Workspace(directory=str(tmp_path))), transcript_path=None)


def test_title_cannot_inject_a_heading(tmp_path):
    text = _render(tmp_path, title="Fix parser\n## INJECTED", ask="fix it")
    assert "# Handoff — Fix parser ## INJECTED\n" in text
    assert "\n## INJECTED" not in text


def test_backticks_in_a_command_extend_the_code_fence(tmp_path):
    text = _render(tmp_path, ask="fix it", command="printf '```' > x")
    assert "````sh\nprintf '```' > x\n````" in text


def test_table_values_cannot_add_a_column():
    assert _cell("a|b") == "a\\|b"
    assert _cell("a\nb") == "a b"


def test_multiline_asks_stay_inside_their_list_item(tmp_path):
    text = _render(tmp_path, ask="first line\n## INJECTED\nthird line")
    assert "1. first line\n   ## INJECTED\n   third line\n" in text
    assert "\n## INJECTED\n" not in text


def test_fence_grows_with_the_longest_backtick_run():
    assert _fence("plain") == "```"
    assert _fence("a ```` b") == "`````"
