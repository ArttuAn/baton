"""baton — hand a coding session from one agent harness to another."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from . import install, pack
from .pack import BatonError

USAGE = "baton <doctor|sessions|pack|resume|handoff>"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="baton", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    doctor = sub.add_parser("doctor", help="which harnesses baton can read and launch")
    doctor.add_argument(
        "--quick", action="store_true", help="skip the format canary (do not parse real sessions)"
    )
    doctor.set_defaults(func=cmd_doctor)

    sessions = sub.add_parser("sessions", help="recent sessions for this project, newest first")
    sessions.add_argument("--dir", default=os.getcwd())
    sessions.add_argument("--limit", type=int, default=10)
    sessions.set_defaults(func=cmd_sessions)

    packer = sub.add_parser("pack", help="write a handoff from a session (no model involved)")
    _pack_args(packer)
    packer.set_defaults(func=cmd_pack)

    resume = sub.add_parser("resume", help="start another harness on an existing handoff")
    resume.add_argument("target", help="claude | opencode | codex | hermes")
    resume.add_argument("--dir", default=os.getcwd())
    resume.add_argument("--pack", help="handoff document (default: .baton/latest.md)")
    resume.add_argument("--dry-run", action="store_true", help="print the command instead of running it")
    resume.set_defaults(func=cmd_resume)

    installer = sub.add_parser(
        "install", help="link the session-handoff skill into every harness you have"
    )
    installer.set_defaults(func=cmd_install)

    carry = sub.add_parser(
        "continue", help="print the handoff for the last session — what an agent calls for you"
    )
    _pack_args(carry)
    carry.set_defaults(func=cmd_continue)

    handoff = sub.add_parser("handoff", help="pack the live session and open it in another harness")
    handoff.add_argument("target", help="claude | opencode | codex | hermes")
    _pack_args(handoff)
    handoff.add_argument("--dry-run", action="store_true")
    handoff.set_defaults(func=cmd_handoff)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except BatonError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 2


def _pack_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dir", default=os.getcwd())
    parser.add_argument("--from", dest="harness", help="source harness (default: most recent session)")
    parser.add_argument("--session", help="source session id or prefix")
    parser.add_argument("--out", help="directory for the handoff (default: <dir>/.baton)")
    parser.add_argument(
        "--minimal",
        action="store_true",
        help="only what the repo cannot say itself: the brief, why it stopped, what failed",
    )


def cmd_doctor(args) -> int:
    drifted = False
    for name, info in pack.available().items():
        read = f"{info['sessions']:>4} sessions" if info.get("readable") else "unreadable"
        launch = "launchable" if info.get("cli") else f"no `{name}` on PATH"
        print(f"{name:<9} {read:<16} {launch}")
        if info.get("error"):
            print(f"{'':<9} {info['error']}")
        if args.quick or not info.get("readable") or not info.get("sessions"):
            continue
        check = pack.probe(name)
        if not check["probed"]:
            continue
        if check.get("thin"):
            print(f"{'':<9} too few turns to judge the format ({check['signals']['turns']})")
        elif check["problems"]:
            drifted = True
            for problem in check["problems"]:
                print(f"{'':<9} \u26a0 {problem}")
        else:
            signals = check["signals"]
            print(
                f"{'':<9} format ok \u2014 probed {check['probed']}: "
                f"{signals['prompts']} prompts, {signals['tools']} tool calls"
            )
    if drifted:
        print(
            "\nA signal missing from every probed session means that harness changed its\n"
            "store format. Handoffs will render, but with holes. Fix the reader in\n"
            "src/baton/readers/ before trusting one.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_sessions(args) -> int:
    refs = pack.sessions_for(args.dir)[: args.limit]
    if not refs:
        print(f"no sessions recorded for {args.dir}")
        return 0
    for ref in refs:
        title = (ref.title or "").strip().replace("\n", " ")[:52]
        count = f"{ref.messages}m" if ref.messages is not None else "?"
        print(f"{ref.updated or '?':<22} {ref.harness:<9} {count:>6}  {ref.id[:28]:<30} {title}")
    return 0


def cmd_pack(args) -> int:
    paths, brief = _do_pack(args)
    print(paths["document"])
    if brief.is_thin():
        print("baton: warning — that session has almost nothing in it", file=sys.stderr)
    return 0


def _do_pack(args):
    ref = pack.pick(args.dir, args.harness, args.session)
    brief, _ = pack.build(ref, args.dir)
    return pack.write(brief, args.dir, args.out, minimal=args.minimal), brief


def cmd_install(args) -> int:
    for harness, outcome, detail in install.install():
        print(f"{harness:<9} {outcome:<15} {detail}")
    print("\nRestart any harness that was already running, then just say what happened:")
    print('  "the session died with opencode, continue where I left off"')
    return 0


def cmd_continue(args) -> int:
    """One call, everything an agent needs: the handoff document on stdout."""
    ref = pack.pick(args.dir, args.harness, args.session)
    brief, _ = pack.build(ref, args.dir)
    paths = pack.write(brief, args.dir, args.out, minimal=args.minimal)
    print(f"<!-- baton: {ref.harness} session {ref.id} · saved to {paths['document']} -->")
    print(Path(paths["document"]).read_text())
    if brief.is_thin():
        print("baton: warning — that session has almost nothing in it", file=sys.stderr)
    return 0


def cmd_resume(args) -> int:
    document = args.pack or os.path.join(args.dir, ".baton", "latest.md")
    if not os.path.exists(document):
        raise BatonError(f"no handoff at {document} — run `baton pack` first")
    return _launch(pack.resume_command(args.target, os.path.abspath(document), args.dir), args)


def cmd_handoff(args) -> int:
    paths, _ = _do_pack(args)
    print(f"handoff written: {paths['document']}")
    return _launch(pack.resume_command(args.target, paths["document"], args.dir), args)


def _launch(command: list[str], args) -> int:
    printable = " ".join(_quote(part) for part in command)
    if args.dry_run:
        print(printable)
        return 0
    print(f"$ {printable}")
    try:
        return subprocess.call(command, cwd=args.dir)
    except OSError as error:
        raise BatonError(f"could not start {command[0]}: {error}") from error


def _quote(part: str) -> str:
    return f'"{part}"' if " " in part else part
