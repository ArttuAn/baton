"""Turn a session into a handoff, mechanically.

Nothing here calls a model. That is the whole point: the handoff has to be
produced *after* the harness has run out of usage, when no model is available to
summarize anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .model import Session, ToolCall
from .workspace import Workspace

STOP_PATTERNS = [
    (re.compile(r"rate.?limit", re.IGNORECASE), "the provider rate-limited the session"),
    (re.compile(r"usage (limit|exceeded)|out of (usage|credit)", re.IGNORECASE), "the plan's usage ran out"),
    (re.compile(r"quota", re.IGNORECASE), "a quota was exhausted"),
    (
        re.compile(r"context (window|length) exceeded|too many tokens", re.IGNORECASE),
        "the context window filled up",
    ),
]

NOISE = re.compile(r"^(cd |ls |pwd|echo |cat |git status|git diff|git log|which |tmux capture)")

# A path-ish token inside a shell command: many sessions edit files through
# heredocs and sed rather than the harness's own edit tool.
# Two overlapping quantifiers in one pattern (e.g. `[~\w./@-]*[\w-]+`) make
# matching a long dot-less command exponential. Tokenize linearly, then check
# for a file extension separately.
PATH_TOKEN = re.compile(r"[~\w./@-]+")
PATH_EXTENSION = re.compile(r"\.[A-Za-z][\w]{0,5}\Z")
PATH_NOISE = re.compile(r"node_modules|/\.git/|\.lock$|^https?://|\.bak$|^/tmp/|/scratchpad/")

# `cd somewhere && …` — relative paths in later commands hang off this.
CD = re.compile(r"(?:^|&&|\|\||[;|&])\s*cd\s+([~\w./@-]+)", re.MULTILINE)

HEREDOC = re.compile(r"<<-?\s*'?(\w+)'?")

# Does this command write files, or only look at them?
WRITE_HINT = re.compile(
    r"(?<![0-9&])>>?\s*(?!&\d|/dev/null)\S"  # a real redirect, not 2>&1 or >/dev/null
    r"|\bsed\s+-i|\btee\b|write_text|\bcp\b|\bmv\b|\bmkdir\b|\brm\b"
)


@dataclass
class Brief:
    session: Session
    workspace: Workspace
    asks: list[str] = field(default_factory=list)
    edited: list[str] = field(default_factory=list)
    read: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    todos: list[dict] = field(default_factory=list)
    last_words: str = ""
    stopped_because: str | None = None

    def is_thin(self) -> bool:
        """True when there is not enough here to be worth handing over."""
        return not self.asks and not self.edited and not self.commands


def _text(value) -> str | None:
    return value if isinstance(value, str) else None


def _normalize(session: Session) -> None:
    """Last line of defence: no store value may crash a handoff build."""
    session.ref.id = session.ref.id if isinstance(session.ref.id, str) else str(session.ref.id)
    session.ref.title = _text(session.ref.title)
    session.model = _text(session.model)
    session.error = _text(session.error)
    for turn in session.turns:
        turn.role = turn.role if isinstance(turn.role, str) else ""
        turn.text = turn.text if isinstance(turn.text, str) else ""
        turn.ts = _text(turn.ts)
        for call in turn.tools:
            call.kind = call.kind if isinstance(call.kind, str) else "other"
            call.name = call.name if isinstance(call.name, str) else "?"
            call.path = _text(call.path)
            call.command = _text(call.command)
            if not isinstance(call.extra, dict):
                call.extra = {}
            todos = call.extra.get("todos")
            call.extra["todos"] = todos if isinstance(todos, list) else []


def build(session: Session, workspace: Workspace, *, max_commands: int = 25, tail: int = 1600) -> Brief:
    _normalize(session)
    brief = Brief(session=session, workspace=workspace)
    brief.asks = [turn.text.strip() for turn in session.human_prompts()]
    calls = session.tool_calls()

    written, consulted = _mined_paths(calls, workspace)
    brief.edited = _merge(_paths(calls, "edit"), _dirty_paths(workspace), written)[:40]
    brief.read = [path for path in _merge(_paths(calls, "read"), consulted) if path not in brief.edited][:25]
    brief.commands = _commands(calls, max_commands)
    brief.failures = _merge([_headline(call.command) or call.line() for call in calls if call.ok is False])[
        -8:
    ]
    brief.todos = _todos(calls)
    brief.last_words = session.last_assistant_text()[-tail:].strip()
    brief.stopped_because = _stopped_because(session, brief.last_words)
    return brief


def _paths(calls: list[ToolCall], kind: str) -> list[str]:
    """Distinct paths, most recently touched last — that ordering is the story."""
    seen: dict[str, None] = {}
    for call in calls:
        if call.kind == kind and call.path:
            seen.pop(call.path, None)
            seen[call.path] = None
    return list(seen)


def _commands(calls: list[ToolCall], limit: int) -> list[str]:
    """The commands worth repeating: deduped, headline-only, navigation noise dropped."""
    seen: dict[str, None] = {}
    for call in calls:
        command = _headline(call.command)
        if not command or NOISE.match(command):
            continue
        seen.pop(command, None)
        seen[command] = None
    return list(seen)[-limit:]


def _headline(command: str | None, width: int = 160) -> str:
    """Keep the parts of a command that inform; drop the parts that only bulk it up.

    A heredoc body is the old agent's own work — the file list already records
    what it produced. What informs the next agent is the shell around it, above
    all whatever ran *after* the heredoc closed, which is usually the check.
    """
    if not command:
        return ""
    raw = [line for line in command.strip().splitlines() if line.strip()]
    if not raw:
        return ""
    shell, hidden = _split_shell(raw)
    head = _clip(" ; ".join(shell[:3]), width)
    if len(shell) > 3:
        head += f" ; …(+{len(shell) - 3} more)"
    if hidden:
        head += f"  [+{hidden} lines of inline script]"
    return head


def _split_shell(lines: list[str]) -> tuple[list[str], int]:
    """Separate the shell commands from heredoc bodies, nested ones included.

    Returns the command lines and how many body lines were left out. The closing
    delimiter counts as neither: it is punctuation, not content.
    """
    out: list[str] = []
    hidden = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        out.append(line.strip())
        opener = HEREDOC.search(line)
        if opener:
            delimiter = opener.group(1)
            index += 1
            while index < len(lines):
                tokens = lines[index].strip().split(maxsplit=1)
                if tokens and tokens[0] == delimiter:
                    break  # `EOF  # done` is still the closing delimiter
                hidden += 1
                index += 1
        index += 1
    return out, hidden


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _merge(*groups: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for group in groups:
        for path in group:
            seen.setdefault(path, None)
    return list(seen)


def _unquote_git_path(path: str) -> str:
    """Decode the C-style quoting git uses for unusual filenames."""
    if not (path.startswith('"') and path.endswith('"')):
        return path
    out: list[str] = []
    raw = path[1:-1]
    index = 0
    while index < len(raw):
        if raw[index] == "\\" and index + 1 < len(raw):
            char = raw[index + 1]
            if char in ('"', "\\"):
                out.append(char)
                index += 2
                continue
            octal = raw[index + 1 : index + 4]
            if len(octal) == 3 and all(digit in "01234567" for digit in octal):
                out.append(chr(int(octal, 8)))
                index += 4
                continue
        out.append(raw[index])
        index += 1
    return "".join(out)


def _dirty_paths(workspace: Workspace) -> list[str]:
    """Git is the ground truth for what actually changed."""
    paths = []
    for line in workspace.dirty:
        path = _unquote_git_path(line[3:].strip())
        if "->" in path:  # a rename: take the destination
            path = path.split("->")[-1].strip()
        if path:
            paths.append(path)
    return paths


def _mined_paths(calls: list[ToolCall], workspace: Workspace) -> tuple[list[str], list[str]]:
    """Files a shell command names that really exist — heredoc and sed edits.

    Relative tokens are resolved against the directory the session last `cd`-ed
    into, which is how a shell-driven session actually addresses its files.
    """
    base = Path(workspace.directory)
    cwd = base
    written: dict[str, None] = {}
    consulted: dict[str, None] = {}
    for call in calls:
        if call.kind != "run" or not call.command:
            continue
        # Heredoc bodies are data, not shell: a `cd` or path inside one must not
        # move the working directory or mark a file as edited.
        shell = "\n".join(_split_shell(call.command.splitlines())[0])
        for match in CD.finditer(shell):
            try:
                target = Path(match.group(1)).expanduser()
            except RuntimeError:  # "~name" with no such user
                continue
            candidate = target if target.is_absolute() else cwd / target
            try:
                if candidate.is_dir():
                    cwd = candidate.resolve()
            except OSError:
                continue
        bucket = written if WRITE_HINT.search(shell) else consulted
        for token in PATH_TOKEN.findall(shell):
            if not PATH_EXTENSION.search(token):
                continue
            if PATH_NOISE.search(token) or token.startswith("-"):
                continue
            try:
                candidate = Path(token).expanduser()
            except RuntimeError:  # "~name" with no such user
                continue
            for resolved in [candidate] if candidate.is_absolute() else [cwd / candidate, base / candidate]:
                try:
                    if resolved.is_file():
                        bucket.setdefault(_display(resolved, base), None)
                        break
                except OSError:
                    continue
    return list(written), [path for path in consulted if path not in written]


def _display(path: Path, base: Path) -> str:
    """Workspace-relative where possible — that is how the next agent will type it."""
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(base))
    except ValueError:
        return str(resolved)


def _todos(calls: list[ToolCall]) -> list[dict]:
    """The agent's own live plan — a full plan wins, task tools accumulate by id."""
    written = [call for call in calls if call.name in {"TodoWrite", "todowrite"} and call.extra.get("todos")]
    if written:
        return _todo_rows(written[-1].extra["todos"])
    merged: dict[str, dict] = {}
    for call in calls:
        for item in call.extra.get("todos") or []:
            if not isinstance(item, dict):
                continue
            content = item.get("content") or item.get("task") or item.get("subject") or item.get("description")
            key = str(item.get("taskId") or item.get("id") or content or "")
            if not key:
                continue
            current = merged.setdefault(key, {"content": "", "status": ""})
            current["content"] = content or current["content"]
            current["status"] = item.get("status") or current["status"]
    return _todo_rows(list(merged.values()))


def _todo_rows(items) -> list[dict]:
    return [
        {
            "content": str(
                item.get("content")
                or item.get("task")
                or item.get("subject")
                or item.get("description")
                or ""
            ),
            "status": str(item.get("status") or ""),
        }
        for item in items
        if isinstance(item, dict)
    ]


def _stopped_because(session: Session, last_words: str) -> str | None:
    haystack = " ".join(filter(None, [session.error or "", last_words]))
    for pattern, reason in STOP_PATTERNS:
        if pattern.search(haystack):
            return reason
    return None
