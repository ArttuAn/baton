"""What the repository itself says about the work — the half no transcript records."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Workspace:
    directory: str
    is_git: bool = False
    branch: str | None = None
    head: str | None = None
    dirty: list[str] = field(default_factory=list)
    diffstat: str | None = None
    commits: list[str] = field(default_factory=list)


def _git(directory: str, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", directory, *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip()


def inspect(directory: str, since: str | None = None) -> Workspace:
    workspace = Workspace(directory=str(Path(directory).resolve()))
    if _git(directory, "rev-parse", "--is-inside-work-tree") != "true":
        return workspace
    workspace.is_git = True
    workspace.branch = _git(directory, "rev-parse", "--abbrev-ref", "HEAD")
    workspace.head = _git(directory, "log", "-1", "--format=%h %s")
    status = _git(directory, "status", "--porcelain") or ""
    workspace.dirty = [line for line in status.splitlines() if line.strip()]
    workspace.diffstat = _git(directory, "diff", "--stat") or None
    if since:
        log = _git(directory, "log", f"--since={since}", "--format=%h %s", "-20") or ""
        workspace.commits = [line for line in log.splitlines() if line.strip()]
    return workspace
