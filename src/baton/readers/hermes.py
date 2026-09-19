"""Hermes: sessions and messages in SQLite at ~/.hermes/state.db."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ..model import Session, SessionRef, ToolCall, Turn

NAME = "hermes"
CLI = "hermes"

TOOL_KINDS = {
    "edit_file": "edit",
    "write_file": "edit",
    "apply_patch": "edit",
    "read_file": "read",
    "bash": "run",
    "shell": "run",
    "run_command": "run",
    "grep": "search",
}


def db_path() -> Path:
    return Path.home() / ".hermes" / "state.db"


def _stamp(value) -> str | None:
    """Hermes stores epoch seconds as floats; nobody reads those."""
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return str(value)


def _connect() -> sqlite3.Connection | None:
    path = db_path()
    if not path.exists():
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def list_sessions(directory: str | None = None) -> list[SessionRef]:
    db = _connect()
    if db is None:
        return []
    with db:
        rows = db.execute(
            "select id, cwd, title, last_activity_at, message_count from sessions"
            " order by coalesce(last_activity_at, started_at) desc"
        ).fetchall()
    target = str(Path(directory).resolve()) if directory else None
    refs = []
    for sid, cwd, title, updated, count in rows:
        if target and cwd and str(Path(cwd).resolve()) != target:
            continue
        refs.append(
            SessionRef(
                harness=NAME,
                id=sid,
                directory=cwd,
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
            "select model, started_at, last_activity_at, end_reason from sessions where id = ?", (ref.id,)
        ).fetchone()
        if row:
            session.model, session.started, session.ended, session.error = (
                row[0],
                _stamp(row[1]),
                _stamp(row[2]),
                row[3],
            )
        rows = db.execute(
            "select role, content, tool_calls, tool_name, timestamp from messages"
            " where session_id = ? order by coalesce(display_order, rowid)",
            (ref.id,),
        ).fetchall()

    for role, content, tool_calls, tool_name, ts in rows:
        if role not in ("user", "assistant"):
            continue
        tools = _tool_calls(tool_calls, tool_name)
        text = (content or "").strip()
        if not text and not tools:
            continue
        session.turns.append(
            Turn(role=role, text=text, ts=_stamp(ts), tools=tools, meta={"synthetic": False})
        )
    return session


def _tool_calls(raw, fallback_name) -> list[ToolCall]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if isinstance(parsed, dict):
        parsed = [parsed]
    calls = []
    for item in parsed or []:
        if not isinstance(item, dict):
            continue
        function = item.get("function") or item
        name = function.get("name") or fallback_name or "?"
        args = function.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        args = args or {}
        calls.append(
            ToolCall(
                kind=TOOL_KINDS.get(name, "other"),
                name=name,
                path=args.get("path") or args.get("file_path"),
                command=args.get("command"),
            )
        )
    return calls


def resume_command(brief_path: str, directory: str | None) -> list[str]:
    prompt = f"Read {brief_path} — it is a handoff from another agent. Follow its Next step section."
    cmd = [CLI, prompt]
    if directory:
        cmd += ["--in", directory]
    return cmd
