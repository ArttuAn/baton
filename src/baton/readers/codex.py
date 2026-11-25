"""Codex: rollout JSONL under ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<id>.jsonl."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from ..handoff import opening_prompt
from ..model import Session, SessionRef, ToolCall, Turn

NAME = "codex"
CLI = "codex"


def root() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions"


def _stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat(timespec="seconds")


def list_sessions(directory: str | None = None) -> list[SessionRef]:
    base = root()
    if not base.is_dir():
        return []
    target = str(Path(directory).resolve()) if directory else None
    refs = []
    for file in base.rglob("rollout-*.jsonl"):
        meta = _meta(file)
        if meta is None:
            continue
        cwd = meta.get("cwd")
        cwd = cwd if isinstance(cwd, str) else None
        # A session with no cwd cannot be attributed to this project — exclude it when filtering.
        if target and (not cwd or str(Path(cwd).resolve()) != target):
            continue
        session_id = meta.get("session_id") or file.stem
        refs.append(
            SessionRef(
                harness=NAME,
                id=session_id if isinstance(session_id, str) else str(session_id),
                directory=cwd,
                title=None,
                updated=_stamp(file.stat().st_mtime),
                messages=None,
                source=str(file),
            )
        )
    return refs


def _meta(file: Path) -> dict | None:
    try:
        with file.open() as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and entry.get("type") == "session_meta":
                    payload = entry.get("payload")
                    return payload if isinstance(payload, dict) else {}
    except OSError:
        return None
    return None


def load(ref: SessionRef) -> Session:
    session = Session(ref=ref)
    pending: dict[str, ToolCall] = {}
    for line in Path(ref.source).read_text(errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "session_meta":
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            model = payload.get("model") or payload.get("model_provider")
            session.model = model if isinstance(model, str) else None
            started = payload.get("timestamp")
            session.started = started if isinstance(started, str) else None
            continue
        if entry.get("type") != "response_item":
            continue
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        ptype = payload.get("type")
        ts = entry.get("timestamp")
        ts = ts if isinstance(ts, str) else None
        if ts:
            session.ended = ts
        if ptype == "message":
            role = payload.get("role")
            # `developer` carries the harness's own instructions, not the human's.
            if role not in ("user", "assistant"):
                continue
            blocks = payload.get("content")
            blocks = blocks if isinstance(blocks, list) else []
            text = "\n".join(
                str(block.get("text") or "") for block in blocks if isinstance(block, dict)
            ).strip()
            if not text:
                continue
            session.turns.append(Turn(role=role, text=text, ts=ts, meta={"synthetic": False}))
        elif ptype in ("function_call", "local_shell_call"):
            call = _tool_call(payload)
            call_id = payload.get("call_id")
            if isinstance(call_id, str):
                pending[call_id] = call
            if session.turns and session.turns[-1].role == "assistant":
                session.turns[-1].tools.append(call)
            else:
                session.turns.append(Turn(role="assistant", ts=ts, tools=[call]))
        elif ptype == "function_call_output":
            call_id = payload.get("call_id")
            call = pending.get(call_id) if isinstance(call_id, str) else None
            if call is not None:
                output = payload.get("output")
                text = output if isinstance(output, str) else json.dumps(output)
                failed = bool(payload.get("is_error")) or bool(
                    re.search(r"(?i)\b(?:failed|error|denied|timed out)\b", text)
                )
                call.ok = not failed
    return session


def _tool_call(payload: dict) -> ToolCall:
    name = payload.get("name") or payload.get("type") or "?"
    name = name if isinstance(name, str) else str(name)
    args = payload.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, ValueError):
            args = {"raw": args}
    if not isinstance(args, dict):
        args = {}
    command = args.get("command")
    if isinstance(command, list):
        command = " ".join(str(x) for x in command)
    command = command if isinstance(command, str) else None
    path = args.get("path") or args.get("file_path")
    path = path if isinstance(path, str) else None
    kind = "run" if "shell" in name or command else "edit" if "patch" in name else "other"
    return ToolCall(kind=kind, name=name, path=path, command=command)


def resume_command(brief_path: str, directory: str | None) -> list[str]:
    return [CLI, opening_prompt(brief_path)]
