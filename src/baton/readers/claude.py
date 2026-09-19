"""Claude Code: JSONL transcripts under ~/.claude/projects/<slugged-cwd>/<uuid>.jsonl."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from ..model import Session, SessionRef, ToolCall, Turn

NAME = "claude"
CLI = "claude"

TOOL_KINDS = {
    "Edit": "edit",
    "Write": "edit",
    "NotebookEdit": "edit",
    "Read": "read",
    "Bash": "run",
    "Grep": "search",
    "Glob": "search",
    "WebSearch": "search",
    "WebFetch": "search",
    "Task": "task",
    "Agent": "task",
    "TodoWrite": "todo",
}


def root() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"


def slug(directory: str) -> str:
    """Claude Code's project folder name: the path with separators flattened."""
    return "-" + str(Path(directory).resolve()).strip("/").replace("/", "-").replace(".", "-")


def _stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat(timespec="seconds")


def list_sessions(directory: str | None = None) -> list[SessionRef]:
    base = root()
    if not base.is_dir():
        return []
    folders = [base / slug(directory)] if directory else sorted(base.iterdir())
    refs: list[SessionRef] = []
    for folder in folders:
        if not folder.is_dir():
            continue
        for file in folder.glob("*.jsonl"):
            head = _peek(file)
            if head is None:
                continue
            refs.append(
                SessionRef(
                    harness=NAME,
                    id=file.stem,
                    directory=head.get("cwd"),
                    title=head.get("title"),
                    updated=_stamp(file.stat().st_mtime),
                    messages=head.get("messages"),
                    source=str(file),
                )
            )
    return refs


def _peek(file: Path) -> dict | None:
    """Pull cwd/title cheaply without decoding every line of a large transcript."""
    cwd = title = None
    messages = 0
    try:
        with file.open() as handle:
            for line in handle:
                if '"cwd"' not in line and '"ai-title"' not in line and '"type":"user"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cwd = cwd or entry.get("cwd")
                if entry.get("type") == "ai-title":
                    title = title or entry.get("title") or entry.get("aiTitle")
                if entry.get("type") in ("user", "assistant"):
                    messages += 1
    except OSError:
        return None
    if cwd is None and messages == 0:
        return None
    return {"cwd": cwd, "title": title, "messages": messages}


def _blocks(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [block for block in (content or []) if isinstance(block, dict)]


def _is_injected(text: str) -> bool:
    """System reminders and slash-command scaffolding are not things the human typed."""
    stripped = text.strip()
    return stripped.startswith(("<system-reminder", "<command-"))


def load(ref: SessionRef) -> Session:
    session = Session(ref=ref)
    pending: dict[str, ToolCall] = {}
    for line in Path(ref.source).read_text(errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = entry.get("type")
        if kind == "system" and entry.get("subtype") == "error":
            session.error = str(entry.get("content"))[:400]
        if kind not in ("user", "assistant"):
            continue
        message = entry.get("message") or {}
        ts = entry.get("timestamp")
        session.model = session.model or message.get("model")
        session.started = session.started or ts
        if ts:
            session.ended = ts

        texts: list[str] = []
        tools: list[ToolCall] = []
        synthetic = kind == "user"
        for block in _blocks(message.get("content")):
            btype = block.get("type")
            if btype == "text":
                text = block.get("text") or ""
                if kind == "user" and _is_injected(text):
                    continue
                texts.append(text)
                if kind == "user":
                    synthetic = False
            elif btype == "tool_use":
                tools.append(_tool_call(block))
                pending[block.get("id") or ""] = tools[-1]
            elif btype == "tool_result":
                call = pending.get(block.get("tool_use_id") or "")
                if call is not None:
                    call.ok = not block.get("is_error")

        if kind == "user" and entry.get("origin", {}).get("kind") == "human":
            synthetic = False
        if not texts and not tools:
            continue
        session.turns.append(
            Turn(
                role=kind,
                text="\n".join(t for t in texts if t.strip()).strip(),
                ts=ts,
                tools=tools,
                meta={"synthetic": synthetic},
            )
        )
    return session


def _tool_call(block: dict) -> ToolCall:
    name = block.get("name") or "?"
    args = block.get("input") or {}
    return ToolCall(
        kind=TOOL_KINDS.get(name, "other"),
        name=name,
        path=args.get("file_path") or args.get("path") or args.get("notebook_path"),
        command=args.get("command"),
        extra={"todos": args["todos"]} if name == "TodoWrite" and args.get("todos") else {},
    )


def resume_command(brief_path: str, directory: str | None) -> list[str]:
    return [CLI, _opening_prompt(brief_path)]


def _opening_prompt(brief_path: str) -> str:
    return f"Read {brief_path} — it is a handoff from another agent. Follow its Next step section."
