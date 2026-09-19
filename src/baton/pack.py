"""Packing: pick a session, build the brief, write it next to the project."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from . import brief as brief_mod
from . import render, workspace as workspace_mod
from .model import Session, SessionRef
from .readers import READERS


class BatonError(RuntimeError):
    """Something the user can fix, reported without a traceback."""


def available() -> dict[str, dict]:
    """Which harnesses we can read from, and which we can launch."""
    report = {}
    for name, reader in READERS.items():
        try:
            sessions = reader.list_sessions()
        except Exception as error:  # a harness store we cannot read must not kill the command
            report[name] = {"readable": False, "error": str(error), "cli": shutil.which(reader.CLI)}
            continue
        report[name] = {
            "readable": True,
            "sessions": len(sessions),
            "cli": shutil.which(reader.CLI),
        }
    return report


def sessions_for(directory: str, harnesses: list[str] | None = None) -> list[SessionRef]:
    refs: list[SessionRef] = []
    for name, reader in READERS.items():
        if harnesses and name not in harnesses:
            continue
        try:
            refs += reader.list_sessions(directory)
        except Exception:
            continue
    return sorted(refs, key=lambda ref: ref.updated or "", reverse=True)


# A session this short is a false start — a launch test, a stray prompt, a crash
# on the first turn. "Continue where I left off" never means one of these.
SUBSTANTIVE_MESSAGES = 3


def pick(directory: str, harness: str | None = None, session_id: str | None = None) -> SessionRef:
    refs = sessions_for(directory, [harness] if harness else None)
    if session_id:
        for ref in refs:
            if ref.id == session_id or ref.id.startswith(session_id):
                return ref
        raise BatonError(f"no session {session_id!r} for {directory}")
    if not refs:
        raise BatonError(f"no sessions found for {directory} — run `baton doctor` to see what is readable")
    for ref in refs:
        if ref.messages is None or ref.messages >= SUBSTANTIVE_MESSAGES:
            return ref
    return refs[0]


def load(ref: SessionRef) -> Session:
    return READERS[ref.harness].load(ref)


def build(ref: SessionRef, directory: str) -> tuple[brief_mod.Brief, Session]:
    session = load(ref)
    space = workspace_mod.inspect(directory, since=session.started)
    return brief_mod.build(session, space), session


def write(brief: brief_mod.Brief, directory: str, out_dir: str | None = None) -> dict[str, str]:
    """Write the handoff pair: the document, and the transcript it points at."""
    base = Path(out_dir) if out_dir else Path(directory) / ".baton"
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = f"handoff-{brief.session.harness}-{stamp}"
    transcript = base / f"{slug}.transcript.json"
    document = base / f"{slug}.md"

    transcript.write_text(json.dumps(_transcript_payload(brief.session), indent=1))
    document.write_text(render.markdown(brief, transcript_path=str(transcript)))
    latest = base / "latest.md"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(document.name)
    except OSError:
        shutil.copyfile(document, latest)
    return {"document": str(document), "transcript": str(transcript), "latest": str(latest)}


def _transcript_payload(session: Session) -> dict:
    return {
        "harness": session.harness,
        "session": session.ref.id,
        "directory": session.directory,
        "model": session.model,
        "started": session.started,
        "ended": session.ended,
        "turns": [
            {
                "role": turn.role,
                "ts": turn.ts,
                "text": turn.text,
                "human": turn.is_human,
                "tools": [asdict(call) for call in turn.tools],
            }
            for turn in session.turns
        ],
    }


def resume_command(target: str, document: str, directory: str | None) -> list[str]:
    reader = READERS.get(target)
    if reader is None:
        raise BatonError(f"unknown harness {target!r} — one of: {', '.join(READERS)}")
    if shutil.which(reader.CLI) is None:
        raise BatonError(f"{reader.CLI} is not on PATH")
    return reader.resume_command(document, directory)
