"""The normalized shape every harness reader produces.

Deliberately small: a handoff needs what the next agent must know, not a
byte-exact replay of the old session.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolCall:
    """One tool invocation, flattened to the few fields a handoff cares about."""

    kind: str  # edit | read | run | search | task | other
    name: str  # the harness's own tool name
    path: str | None = None
    command: str | None = None
    ok: bool | None = None  # None when the harness does not record an outcome
    extra: dict = field(default_factory=dict)  # only what a handoff needs, e.g. a todo list

    def line(self) -> str:
        if self.command:
            return self.command
        return self.path or self.name


@dataclass
class Turn:
    role: str  # user | assistant
    text: str = ""
    ts: str | None = None
    tools: list[ToolCall] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def is_human(self) -> bool:
        """A typed prompt, as opposed to a tool result replayed into the user role."""
        return self.role == "user" and bool(self.text.strip()) and not self.meta.get("synthetic")


@dataclass
class SessionRef:
    """Enough to list and pick a session without parsing the whole thing."""

    harness: str
    id: str
    directory: str | None = None
    title: str | None = None
    updated: str | None = None
    messages: int | None = None
    source: str | None = None  # file path or db path

    def label(self) -> str:
        return f"{self.harness}:{self.id}"


@dataclass
class Session:
    ref: SessionRef
    turns: list[Turn] = field(default_factory=list)
    model: str | None = None
    started: str | None = None
    ended: str | None = None
    error: str | None = None  # why it stopped, when the harness recorded it

    @property
    def harness(self) -> str:
        return self.ref.harness

    @property
    def directory(self) -> str | None:
        return self.ref.directory

    def human_prompts(self) -> list[Turn]:
        return [turn for turn in self.turns if turn.is_human]

    def tool_calls(self) -> list[ToolCall]:
        return [call for turn in self.turns for call in turn.tools]

    def last_assistant_text(self) -> str:
        for turn in reversed(self.turns):
            if turn.role == "assistant" and turn.text.strip():
                return turn.text.strip()
        return ""
