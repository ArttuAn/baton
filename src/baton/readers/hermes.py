"""Hermes: sessions and messages in SQLite at ~/.hermes/state.db."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from ..handoff import opening_prompt
from ..model import Session, SessionRef, ToolCall, Turn

NAME = "hermes"
CLI = "hermes"

TOOL_KINDS = {
    "edit_file": "edit",
    "write_file": "edit",
    "apply_patch": "edit",
    "patch": "edit",
    "read_file": "read",
    "bash": "run",
    "shell": "run",
    "terminal": "run",
    "run_command": "run",
    "grep": "search",
    "search_files": "search",
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
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def list_sessions(directory: str | None = None) -> list[SessionRef]:
    db = _connect()
    if db is None:
        return []
    with closing(db):
        rows = db.execute(
            "select id, cwd, title, last_activity_at, message_count from sessions"
            " order by coalesce(last_activity_at, started_at) desc"
        ).fetchall()
    target = str(Path(directory).resolve()) if directory else None
    refs = []
    for sid, cwd, title, updated, count in rows:
        cwd = cwd if isinstance(cwd, str) else None
        title = title if isinstance(title, str) else None
        # A session with no cwd cannot be attributed to this project — exclude it when filtering.
        if target and (not cwd or str(Path(cwd).resolve()) != target):
            continue
        refs.append(
            SessionRef(
                harness=NAME,
                id=str(sid),
                directory=cwd,
                title=title,
                updated=_stamp(updated),
                messages=count if isinstance(count, int) else None,
                source=str(db_path()),
            )
        )
    return refs


def load(ref: SessionRef) -> Session:
    session = Session(ref=ref)
    db = _connect()
    if db is None:
        return session
    with closing(db):
        row = db.execute(
            "select model, started_at, last_activity_at, end_reason from sessions where id = ?", (ref.id,)
        ).fetchone()
        if row:
            model = row[0] if isinstance(row[0], str) else None
            error = row[3] if isinstance(row[3], str) else None
            session.model, session.started, session.ended, session.error = (
                model,
                _stamp(row[1]),
                _stamp(row[2]),
                error,
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
        content = content if isinstance(content, str) else str(content or "")
        text = content.strip()
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
    except (TypeError, ValueError):
        return []
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []
    calls = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        function = item.get("function") or item
        if not isinstance(function, dict):
            continue
        name = function.get("name") or fallback_name or "?"
        name = name if isinstance(name, str) else str(name)
        args = function.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, ValueError):
                args = {}
        if not isinstance(args, dict):
            args = {}
        path = args.get("path") or args.get("file_path")
        command = args.get("command")
        calls.append(
            ToolCall(
                kind=TOOL_KINDS.get(name, "other"),
                name=name,
                path=path if isinstance(path, str) else None,
                command=command if isinstance(command, str) else None,
            )
        )
    return calls


def resume_command(brief_path: str, directory: str | None) -> list[str]:
    prompt = opening_prompt(brief_path)
    cmd = [CLI, prompt]
    if directory:
        cmd += ["--in", directory]
    return cmd
