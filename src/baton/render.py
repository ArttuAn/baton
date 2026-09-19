"""Render a Brief as the document the next agent actually reads."""

from __future__ import annotations

from .brief import Brief

HEADER_NOTE = (
    "*Written by `baton`, mechanically, from the previous session's own log. "
    "No model wrote this — the handoff has to work when the old agent has no usage left.*"
)


def markdown(brief: Brief, *, transcript_path: str | None = None) -> str:
    session = brief.session
    out: list[str] = []
    title = session.ref.title or (brief.asks[0].splitlines()[0] if brief.asks else "Session handoff")
    out.append(f"# Handoff — {title.strip()[:90]}")
    out.append("")
    out.append(HEADER_NOTE)
    out.append("")
    out.extend(_facts(brief))
    out.extend(_asks(brief))
    out.extend(_stopped(brief))
    out.extend(_plan(brief))
    out.extend(_files(brief))
    out.extend(_commands(brief))
    out.extend(_failures(brief))
    out.extend(_next_step(brief))
    out.extend(_transcript(brief, transcript_path))
    return "\n".join(out).rstrip() + "\n"


def _facts(brief: Brief) -> list[str]:
    session, workspace = brief.session, brief.workspace
    rows = [
        ("Project", workspace.directory),
        ("Git", _git_line(brief)),
        ("Handed over by", f"{session.harness}" + (f" · {session.model}" if session.model else "")),
        ("Source session", f"`{session.ref.id}`"),
        ("Session ran", f"{session.started or '?'} → {session.ended or '?'}"),
        ("Turns", str(len(session.turns))),
    ]
    if brief.stopped_because:
        rows.append(("Stopped because", brief.stopped_because))
    lines = ["| | |", "|---|---|"]
    lines += [f"| {key} | {value} |" for key, value in rows if value]
    return lines + [""]


def _git_line(brief: Brief) -> str | None:
    workspace = brief.workspace
    if not workspace.is_git:
        return "not a git repository"
    parts = [f"`{workspace.branch}`"]
    if workspace.head:
        parts.append(f"at {workspace.head}")
    if workspace.dirty:
        parts.append(f"— {len(workspace.dirty)} uncommitted file(s)")
    return " ".join(parts)


def _asks(brief: Brief) -> list[str]:
    if not brief.asks:
        return []
    out = ["## What was asked", "", "Verbatim, in order — this is the actual brief, not a summary of one.", ""]
    for index, ask in enumerate(brief.asks, 1):
        body = ask.strip()
        if len(body) > 700:
            body = body[:700].rstrip() + " […]"
        out.append(f"{index}. {body}")
        out.append("")
    return out


def _stopped(brief: Brief) -> list[str]:
    if not brief.last_words:
        return []
    quoted = "\n".join(f"> {line}" for line in brief.last_words.splitlines())
    return ["## Where it stopped", "", "The last thing the previous agent said:", "", quoted, ""]


def _plan(brief: Brief) -> list[str]:
    if not brief.todos:
        return []
    out = ["## The plan it was working through", ""]
    for item in brief.todos:
        mark = {"completed": "x", "in_progress": "~"}.get(item["status"], " ")
        out.append(f"- [{mark}] {item['content']}")
    out.append("")
    return out


def _files(brief: Brief) -> list[str]:
    out: list[str] = []
    if brief.edited:
        out += ["## Files this session changed", ""]
        out += [f"- `{path}`" for path in brief.edited]
        out.append("")
    if brief.workspace.diffstat:
        out += ["Uncommitted right now:", "", "```", brief.workspace.diffstat, "```", ""]
    if brief.workspace.commits:
        out += ["Committed during the session:", ""]
        out += [f"- {line}" for line in brief.workspace.commits]
        out.append("")
    if brief.read:
        out += ["<details><summary>Files it only read (context, not changes)</summary>", ""]
        out += [f"- `{path}`" for path in brief.read]
        out += ["", "</details>", ""]
    return out


def _commands(brief: Brief) -> list[str]:
    if not brief.commands:
        return []
    return [
        "## Commands it ran",
        "",
        "Newest last. Inline scripts are shown by their first line only.",
        "",
        "```sh",
        *brief.commands,
        "```",
        "",
    ]


def _failures(brief: Brief) -> list[str]:
    if not brief.failures:
        return []
    return [
        "## Things that failed",
        "",
        "Do not walk into these again without reading why they failed:",
        "",
        *[f"- `{line[:160]}`" for line in brief.failures],
        "",
    ]


def _next_step(brief: Brief) -> list[str]:
    out = ["## Next step", ""]
    if brief.asks:
        out.append(f"Pick up request #{len(brief.asks)} above. It is the live one.")
    else:
        out.append("Read 'Where it stopped' — that is the live thread.")
    out.append("")
    out += [
        "Before you touch anything:",
        "",
        "1. Re-read the files under **Files this session changed** — they hold the work in progress.",
        "2. Treat everything above as *already done*. Do not redo it; verify it if you doubt it.",
        "3. The previous agent's claims are claims. Check them against the repo before building on them.",
        "",
    ]
    verify = _verification(brief)
    if verify:
        out += ["The previous agent verified its work with:", "", "```sh", verify, "```", ""]
    return out


def _verification(brief: Brief) -> str | None:
    """The last test-ish command is the fastest way for the next agent to get its bearings."""
    for command in reversed(brief.commands):
        lowered = command.lower()
        if any(hint in lowered for hint in ("test", "pytest", "npm run", "make ", "cargo ", "lint")):
            return command
    return None


def _transcript(brief: Brief, transcript_path: str | None) -> list[str]:
    if not transcript_path:
        return []
    return [
        "## The full transcript",
        "",
        f"Every turn of the old session, normalized: `{transcript_path}`",
        "",
        "Grep it when this summary is not enough. It is JSON: "
        "`{turns: [{role, text, ts, tools:[{kind,name,path,command,ok}]}]}`.",
        "",
    ]
