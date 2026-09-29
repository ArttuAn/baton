"""Click a terminal window, get the agent session running in it.

Every gnome-terminal window belongs to one server process, so the window's
_NET_WM_PID says nothing about what runs inside it. Instead each agent's pty
is asked to name itself: an OSC title escape written to /dev/pts/N retitles
whichever window shows that pty. Read the titles back and every window maps
to a pid, the pid maps to a session, and the original titles are put back.

X11 only — Wayland does not let one client inspect another's windows.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import pack
from .pack import BatonError

HARNESSES = ("claude", "opencode", "codex", "hermes")
PROBE = "baton-pick #{pid}"
PROBE_RE = re.compile(r"baton-pick #(\d+)")
CLAUDE_SESSIONS = Path.home() / ".claude" / "sessions"
SETTLE = 0.25  # seconds for the terminal to apply the retitle
ROUNDS = 3


@dataclass
class Picked:
    harness: str
    pid: int
    directory: str
    session_id: str | None
    title: str


def pick(timeout: int = 60) -> Picked:
    _require_x11()
    agents = {pid: harness for pid, harness in _agents() if pid not in _ancestors()}
    ttys = {pid: tty for pid in agents if (tty := _tty(pid))}
    if not ttys:
        raise BatonError("no other agent session is running in a terminal")

    before = _titles()
    try:
        owner: dict[str, int] = {}
        for _ in range(ROUNDS):  # a busy agent redraws its title and can clobber a probe
            for pid in set(ttys) - set(owner.values()):
                _retitle(ttys[pid], PROBE.format(pid=pid))
            time.sleep(SETTLE)
            owner |= _mapped()
        print(f"click the terminal to continue from ({len(owner)} labelled 'baton-pick #…')", flush=True)
        clicked = _click(timeout)
        owner |= _mapped()
    finally:
        for wid, pid in _mapped().items():
            if wid in before:
                _retitle(ttys[pid], before[wid])

    pid = owner.get(clicked)
    if pid is None:
        raise BatonError(
            "that window has no other agent session in it — or its session is in a background tab "
            "(bring the tab to front and pick again)"
        )
    return _resolve(pid, agents[pid], before.get(clicked, ""))


def _mapped() -> dict[str, int]:
    """Window → pid, re-read at restore time so a late retitle is still undone."""
    return {wid: int(m.group(1)) for wid, t in _titles().items() if (m := PROBE_RE.search(t))}


def _resolve(pid: int, harness: str, title: str) -> Picked:
    directory = os.readlink(f"/proc/{pid}/cwd")
    session_id = None
    if harness == "claude":
        record = CLAUDE_SESSIONS / f"{pid}.json"
        if record.exists():
            data = json.loads(record.read_text())
            session_id, directory = data.get("sessionId"), data.get("cwd") or directory
    if not session_id:
        session_id = _by_title(directory, harness, title)
    return Picked(harness, pid, directory, session_id, title)


def _by_title(directory: str, harness: str, title: str) -> str | None:
    """Harnesses without a pid record: the session whose title the window shows."""
    refs = pack.sessions_for(directory, [harness])
    for ref in refs:
        name = (ref.title or "").strip()
        if name and name in title:
            return ref.id
    return refs[0].id if refs else None


def _require_x11() -> None:
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" or not os.environ.get("DISPLAY"):
        raise BatonError("--pick needs an X11 display (Wayland hides other windows)")
    for tool in ("wmctrl", "xwininfo"):
        if not shutil.which(tool):
            raise BatonError(f"--pick needs `{tool}` on PATH")


def _agents() -> list[tuple[int, str]]:
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            comm = (proc / "comm").read_text().strip().lstrip(".")
        except OSError:
            continue
        if comm in HARNESSES:
            found.append((int(proc.name), comm))
    return found


def _ancestors() -> set[int]:
    """This process's own chain — the session asking is never the one to pick."""
    chain, pid = set(), os.getpid()
    while pid > 1:
        chain.add(pid)
        try:
            pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            break
    return chain


def _tty(pid: int) -> str | None:
    try:
        target = os.readlink(f"/proc/{pid}/fd/0")
    except OSError:
        return None
    return target if target.startswith("/dev/pts/") else None


def _retitle(tty: str, title: str) -> None:
    try:
        with open(tty, "w") as terminal:
            terminal.write(f"\033]0;{title}\007")
    except OSError:
        pass


def _titles() -> dict[str, str]:
    out = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, check=False).stdout
    titles = {}
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 3:
            titles[_norm(parts[0])] = parts[3] if len(parts) == 4 else ""
    return titles


def _click(timeout: int) -> str:
    try:
        out = subprocess.run(
            ["xwininfo"], capture_output=True, text=True, timeout=timeout, check=False
        ).stdout
    except subprocess.TimeoutExpired as error:
        raise BatonError(f"no window clicked within {timeout}s") from error
    match = re.search(r"Window id: (0x[0-9a-f]+)", out)
    if not match:
        raise BatonError("could not read the clicked window")
    return _norm(match.group(1))


def _norm(wid: str) -> str:
    return hex(int(wid, 16))
