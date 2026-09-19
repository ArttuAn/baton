"""The format canary, and the minimal handoff."""

from baton import pack, render
from baton.model import Session, SessionRef, ToolCall, Turn
from baton.readers import READERS


class FakeReader:
    """A harness store whose shape we control, to stand in for a real one."""

    CLI = "fake"

    def __init__(self, sessions, claims=None):
        self._sessions = sessions
        # What the store's own index claims, which is NOT what parsing recovers.
        # A drifted reader still sees sessions listed; it just gets nothing out
        # of them — so the two numbers have to be able to disagree.
        self._claims = claims or [len(session.turns) for session in sessions]

    def list_sessions(self, directory=None):
        return [
            SessionRef(harness="fake", id=f"s{index}", messages=claimed)
            for index, claimed in enumerate(self._claims)
        ]

    def load(self, ref):
        return self._sessions[int(ref.id[1:])]


def turns(count, *, tools=True, text=True, ts=True):
    out = []
    for index in range(count):
        out.append(Turn(role="user", text=f"ask {index}", ts="2026-01-01" if ts else None,
                        meta={"synthetic": False}))
        out.append(Turn(
            role="assistant",
            text="working on it" if text else "",
            ts="2026-01-01" if ts else None,
            tools=[ToolCall(kind="run", name="Bash", command="pytest")] if tools else [],
        ))
    return out


def install(monkeypatch, sessions, claims=None):
    monkeypatch.setitem(READERS, "fake", FakeReader(sessions, claims))


def session(turn_list):
    return Session(ref=SessionRef(harness="fake", id="s0"), turns=turn_list)


def test_a_healthy_store_reports_no_problems(monkeypatch):
    install(monkeypatch, [session(turns(5))])
    check = pack.probe("fake")
    assert check["problems"] == []
    assert check["signals"]["tools"] == 5


def test_a_store_that_parses_to_nothing_is_reported_as_drift(monkeypatch):
    install(monkeypatch, [session([]), session([]), session([])], claims=[20, 20, 20])
    assert "store format has changed" in " ".join(pack.probe("fake")["problems"])


def test_a_signal_missing_from_every_session_is_drift(monkeypatch):
    install(monkeypatch, [session(turns(6, tools=False))])
    assert "no tool calls recovered" in pack.probe("fake")["problems"]


def test_a_signal_present_somewhere_is_not_drift(monkeypatch):
    install(monkeypatch, [session(turns(4, tools=False) + turns(2))])
    assert pack.probe("fake")["problems"] == []


def test_a_thin_sample_is_never_called_drift(monkeypatch):
    """Absence of evidence is not evidence of absence — two turns prove nothing."""
    install(monkeypatch, [session(turns(1, tools=False, text=False))], claims=[20])
    check = pack.probe("fake")
    assert check["thin"] is True
    assert check["problems"] == []


def test_stub_sessions_are_not_probed(monkeypatch):
    """A one-turn launch test has nothing to recover, so its silence means nothing."""
    install(monkeypatch, [session(turns(6)), session([Turn(role="user", text="hi")])])
    assert pack.probe("fake")["probed"] == 1


def test_an_unparseable_session_is_the_finding(monkeypatch):
    class Broken(FakeReader):
        def load(self, ref):
            raise KeyError("role")

    monkeypatch.setitem(READERS, "fake", Broken([session(turns(5))]))
    assert "could not parse 1/1" in " ".join(pack.probe("fake")["problems"])


# --- minimal handoff -------------------------------------------------------


def brief_for(tmp_path):
    from baton.brief import build
    from baton.workspace import Workspace

    body = Session(
        ref=SessionRef(harness="claude", id="s1", directory=str(tmp_path), title="Fix the parser"),
        turns=[
            Turn(role="user", text="fix the parser", meta={"synthetic": False}),
            Turn(role="assistant", text="done",
                 tools=[ToolCall(kind="edit", name="Edit", path="src/p.py"),
                        ToolCall(kind="run", name="Bash", command="pytest -q", ok=False)]),
        ],
    )
    return build(body, Workspace(directory=str(tmp_path)))


def test_minimal_keeps_only_what_the_repo_cannot_say_itself(tmp_path):
    text = render.markdown(brief_for(tmp_path), minimal=True)
    for kept in ("## What was asked", "## Where it stopped", "## Next step"):
        assert kept in text
    for dropped in ("## Files this session changed", "## Commands it ran"):
        assert dropped not in text
    assert "fix the parser" in text


def test_minimal_sends_the_next_agent_to_git_instead_of_a_file_list(tmp_path):
    text = render.markdown(brief_for(tmp_path), minimal=True)
    assert "git status" in text
    assert "Files this session changed**" not in text


def test_minimal_is_substantially_shorter(tmp_path):
    brief = brief_for(tmp_path)
    assert len(render.markdown(brief, minimal=True)) < len(render.markdown(brief))
