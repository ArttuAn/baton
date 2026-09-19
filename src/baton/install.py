"""Wiring the skill into every harness, so a sentence is enough to trigger it."""

from __future__ import annotations

from pathlib import Path

SKILL_NAME = "session-handoff"

# Where each harness looks for skills. `~/.agents/skills` is the shared overlay
# location, not a harness of its own.
TARGETS = {
    "claude": Path.home() / ".claude" / "skills",
    "opencode": Path.home() / ".config" / "opencode" / "skills",
    "codex": Path.home() / ".codex" / "skills",
    "hermes": Path.home() / ".hermes" / "skills",
    "shared": Path.home() / ".agents" / "skills",
}


def source() -> Path:
    path = Path(__file__).resolve().parent / "skill" / SKILL_NAME
    if not (path / "SKILL.md").is_file():
        raise FileNotFoundError(f"skill source missing at {path}")
    return path


def install(targets: dict[str, Path] | None = None) -> list[tuple[str, str, str]]:
    """Link the skill into each harness that is actually present.

    Returns (harness, outcome, detail) so the caller can report honestly rather
    than claim success for harnesses that are not installed.
    """
    skill = source()
    results = []
    for name, directory in (targets or TARGETS).items():
        # Only wire harnesses the user actually has: never conjure their config.
        if not directory.parent.is_dir():
            results.append((name, "skipped", f"{directory.parent} does not exist"))
            continue
        link = directory / SKILL_NAME
        try:
            directory.mkdir(parents=True, exist_ok=True)
            if link.is_symlink() or link.exists():
                if link.is_symlink() and link.readlink() == skill:
                    results.append((name, "already linked", str(link)))
                    continue
                if not link.is_symlink():
                    results.append((name, "left alone", f"{link} exists and is not a symlink"))
                    continue
                link.unlink()
            link.symlink_to(skill, target_is_directory=True)
            results.append((name, "linked", str(link)))
        except OSError as error:
            results.append((name, "failed", str(error)))
    return results
