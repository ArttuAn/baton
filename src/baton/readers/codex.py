"""Codex: rollout JSONL under ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<id>.jsonl."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ..model import Session, SessionRef, ToolCall, Turn

NAME = "codex"
CLI = "codex"


def root() -> Path:
    return Path.home() / ".codex" / "sessions"


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
        if target and meta.get("cwd") and str(Path(meta["cwd"]).resolve()) != target:
            continue
        refs.append(
            SessionRef(
                harness=NAME,
                id=meta.get("session_id") or file.stem,
                directory=meta.get("cwd"),
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
                entry = json.loads(line)
                if entry.get("type") == "session_meta":
                    return entry.get("payload") or {}
                break
    except (OSError, json.JSONDecodeError):
        return None
    return None


def load(ref: SessionRef) -> Session:
    session = Session(ref=ref)
    for line in Path(ref.source).read_text(errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") == "session_meta":
            payload = entry.get("payload") or {}
            session.model = payload.get("model") or payload.get("model_provider")
            session.started = payload.get("timestamp")
            continue
        if entry.get("type") != "response_item":
            continue
        payload = entry.get("payload") or {}
        ptype = payload.get("type")
        ts = entry.get("timestamp")
        if ts:
            session.ended = ts
        if ptype == "message":
            role = payload.get("role")
            # `developer` carries the harness's own instructions, not the human's.
            if role not in ("user", "assistant"):
                continue
            text = "\n".join(
                block.get("text") or ""
                for block in payload.get("content") or []
                if isinstance(block, dict)
            ).strip()
            if not text:
                continue
            session.turns.append(Turn(role=role, text=text, ts=ts, meta={"synthetic": False}))
        elif ptype in ("function_call", "local_shell_call"):
            call = _tool_call(payload)
            if session.turns and session.turns[-1].role == "assistant":
                session.turns[-1].tools.append(call)
            else:
                session.turns.append(Turn(role="assistant", ts=ts, tools=[call]))
    return session


def _tool_call(payload: dict) -> ToolCall:
    name = payload.get("name") or payload.get("type") or "?"
    args = payload.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"raw": args}
    args = args or {}
    command = args.get("command")
    if isinstance(command, list):
        command = " ".join(str(x) for x in command)
    kind = "run" if "shell" in name or command else "edit" if "patch" in name else "other"
    return ToolCall(kind=kind, name=name, path=args.get("path") or args.get("file_path"), command=command)


def resume_command(brief_path: str, directory: str | None) -> list[str]:
    return [CLI, f"Read {brief_path} — it is a handoff from another agent. Follow its Next step section."]
