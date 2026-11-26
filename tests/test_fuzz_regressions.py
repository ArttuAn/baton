"""Regressions found by throwing adversarial values at the pure API."""

import time

from baton.brief import build
from baton.model import Session, SessionRef, ToolCall, Turn
from baton.render import markdown
from baton.workspace import Workspace


def test_long_dotless_command_is_not_a_regex_bomb():
    session = Session(
        ref=SessionRef("claude", "s", "/p"),
        turns=[
            Turn(
                role="assistant",
                tools=[ToolCall(kind="run", name="bash", command="x" * 20000)],
            )
        ],
    )
    start = time.monotonic()
    markdown(build(session, Workspace(directory="/p")))
    assert time.monotonic() - start < 2


def test_malformed_session_values_are_normalized_before_render():
    session = Session(
        ref=SessionRef("claude", ["bad"], "/p", title={"bad": True}),
        model=1,
        error=1e30,
        turns=[
            Turn(
                role="assistant",
                text=["bad"],
                ts={},
                tools=[
                    ToolCall(
                        kind="run",
                        name=["bad"],
                        path={"bad": True},
                        command=42,
                        extra={"todos": [{"content": ["bad"], "status": ["running"]}]},
                    )
                ],
            )
        ],
    )
    document = markdown(build(session, Workspace(directory="/p")))
    assert "# Handoff" in document
    assert "\ud800" not in document
