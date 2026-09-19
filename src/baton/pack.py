"""Packing: pick a session, build the brief, write it next to the project."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from . import brief as brief_mod
from . import render
from . import workspace as workspace_mod
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
        except Exception as error:  # noqa: BLE001 - one unreadable store must not kill the command
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
        except Exception:  # noqa: BLE001, S112 - `doctor` is where store errors get reported
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


def write(
    brief: brief_mod.Brief,
    directory: str,
    out_dir: str | None = None,
    *,
    minimal: bool = False,
) -> dict[str, str]:
    """Write the handoff pair: the document, and the transcript it points at."""
    base = Path(out_dir) if out_dir else Path(directory) / ".baton"
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M%S")
    slug = f"handoff-{brief.session.harness}-{stamp}"
    transcript = base / f"{slug}.transcript.json"
    document = base / f"{slug}.md"

    transcript.write_text(json.dumps(_transcript_payload(brief.session), indent=1))
    document.write_text(render.markdown(brief, transcript_path=str(transcript), minimal=minimal))
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


# --- format canary ---------------------------------------------------------
#
# Every reader parses a store format its vendor never documented and never
# promised to keep. When one drifts, the failure is silent: `list_sessions()`
# still finds sessions, `load()` still returns a Session, and the handoff
# still renders — just empty in the places that matter. Nobody notices until
# the session they needed is gone.
#
# So `doctor` parses the few newest real sessions per harness and asserts the
# signals a handoff is built from are actually coming through. Absent across
# every probed session is drift; absent in one is just a quiet session.

PROBE_SESSIONS = 3

# Below this many turns the probe has not seen enough to call anything missing.
# A quiet sample is absence of evidence; drift is evidence of absence.
PROBE_MIN_TURNS = 6

# signal -> what it means when it is missing everywhere
SIGNALS = {
    "prompts": "no user prompts recovered",
    "assistant": "no assistant text recovered",
    "timestamps": "no timestamps recovered",
    "tools": "no tool calls recovered",
}


def probe(name: str, directory: str | None = None) -> dict:
    """Parse real sessions for one harness and report signals that have gone missing."""
    reader = READERS[name]
    try:
        refs = reader.list_sessions(directory) if directory else reader.list_sessions()
    except Exception as error:  # noqa: BLE001 - reported, never raised
        return {"probed": 0, "problems": [f"store unreadable: {error}"]}

    # Stub sessions have nothing to recover, so their silence is not evidence —
    # probe only the ones `pick()` would actually hand over.
    refs = [ref for ref in refs if ref.messages is None or ref.messages >= SUBSTANTIVE_MESSAGES]
    refs = sorted(refs, key=lambda ref: ref.updated or "", reverse=True)[:PROBE_SESSIONS]
    if not refs:
        return {"probed": 0, "problems": []}

    seen = dict.fromkeys(("turns", *SIGNALS), 0)
    unparsed: list[str] = []
    for ref in refs:
        try:
            session = load(ref)
        except Exception as error:  # noqa: BLE001 - a parse failure IS the finding
            unparsed.append(f"{ref.id[:12]} ({type(error).__name__})")
            continue
        seen["turns"] += len(session.turns)
        seen["prompts"] += len(session.human_prompts())
        seen["assistant"] += bool(session.last_assistant_text())
        seen["timestamps"] += sum(1 for turn in session.turns if turn.ts)
        seen["tools"] += len(session.tool_calls())

    problems = []
    if unparsed:
        problems.append(f"could not parse {len(unparsed)}/{len(refs)}: {', '.join(unparsed)}")
    thin = False
    if not seen["turns"]:
        problems.append(f"0 turns across the {len(refs)} newest sessions — the store format has changed")
    elif seen["turns"] < PROBE_MIN_TURNS:
        thin = True
    else:
        problems += [message for key, message in SIGNALS.items() if not seen[key]]
    return {"probed": len(refs), "problems": problems, "signals": seen, "thin": thin}
