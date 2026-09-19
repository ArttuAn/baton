"""One reader per harness. Each exposes `list_sessions()` and `load(ref)`."""

from __future__ import annotations

from . import claude, codex, hermes, opencode

READERS = {
    "claude": claude,
    "opencode": opencode,
    "codex": codex,
    "hermes": hermes,
}

__all__ = ["READERS", "claude", "codex", "hermes", "opencode"]
