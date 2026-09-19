"""opencode: sessions live in SQLite at ~/.local/share/opencode/opencode.db."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ..model import Session, SessionRef, ToolCall, Turn

NAME = "opencode"
CLI = "opencode"

TOOL_KINDS = {
    "edit": "edit",
    "write": "edit",
    "patch": "edit",
    "read": "read",
    "bash": "run",
    "grep": "search",
    "glob": "search",
    "webfetch": "search",
    "task": "task",
    "todowrite": "todo",
    "skill": "skill",
    "question": "question",
}


def db_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    return Path(base) / "opencode" / "opencode.db"


def _connect() -> sqlite3.Connection | None:
    path = db_path()
    if not path.exists():
        return None
    # Read-only: opencode may well be running against this same file.
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _stamp(millis) -> str | None:
    if not millis:
        return None
    return datetime.fromtimestamp(millis / 1000, tz=UTC).isoformat(timespec="seconds")


def list_sessions(directory: str | None = None) -> list[SessionRef]:
    db = _connect()
    if db is None:
        return []
    with db:
        rows = db.execute(
            "select id, directory, title, time_updated,"
            " (select count(*) from message where message.session_id = session.id)"
            " from session order by time_updated desc"
        ).fetchall()
    target = str(Path(directory).resolve()) if directory else None
    refs = []
    for sid, sdir, title, updated, count in rows:
        if target and sdir and str(Path(sdir).resolve()) != target:
            continue
        refs.append(
            SessionRef(
                harness=NAME,
                id=sid,
                directory=sdir,
                title=title,
                updated=_stamp(updated),
                messages=count,
                source=str(db_path()),
            )
        )
    return refs


def load(ref: SessionRef) -> Session:
    session = Session(ref=ref)
    db = _connect()
    if db is None:
        return session
    with db:
        row = db.execute(
            "select model, time_created, time_updated from session where id = ?", (ref.id,)
        ).fetchone()
        if row:
            model, created, updated = row
            session.model = _model_name(model)
            session.started = _stamp(created)
            session.ended = _stamp(updated)
        messages = db.execute(
            "select id, data from message where session_id = ? order by time_created, id", (ref.id,)
        ).fetchall()
        parts: dict[str, list[dict]] = {}
        for message_id, data in db.execute(
            "select message_id, data from part where session_id = ? order by time_created, id", (ref.id,)
        ):
            parts.setdefault(message_id, []).append(json.loads(data))

    for message_id, data in messages:
        info = json.loads(data)
        role = info.get("role") or "assistant"
        texts, tools = [], []
        for part in parts.get(message_id, []):
            ptype = part.get("type")
            if ptype == "text":
                texts.append(part.get("text") or "")
            elif ptype == "tool":
                tools.append(_tool_call(part))
        if not texts and not tools:
            continue
        session.turns.append(
            Turn(
                role="user" if role == "user" else "assistant",
                text="\n".join(t for t in texts if t.strip()).strip(),
                ts=_stamp((info.get("time") or {}).get("created")),
                tools=tools,
                meta={"synthetic": False},
            )
        )
    return session


def _model_name(raw) -> str | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return str(raw)
    if isinstance(parsed, dict):
        return "/".join(x for x in (parsed.get("providerID"), parsed.get("id") or parsed.get("modelID")) if x)
    return str(raw)


def _tool_call(part: dict) -> ToolCall:
    tool = part.get("tool") or "?"
    state = part.get("state") or {}
    args = state.get("input") or {}
    status = state.get("status")
    return ToolCall(
        kind=TOOL_KINDS.get(tool, "other"),
        name=tool,
        path=args.get("filePath") or args.get("file_path") or args.get("path"),
        command=args.get("command"),
        ok=None if status in (None, "running", "pending") else status == "completed",
        extra={"todos": args["todos"]} if tool == "todowrite" and args.get("todos") else {},
    )


def resume_command(brief_path: str, directory: str | None) -> list[str]:
    prompt = f"Read {brief_path} — it is a handoff from another agent. Follow its Next step section."
    # opencode takes the project directory as a positional, not a flag.
    return [CLI, *([directory] if directory else []), "--prompt", prompt]
