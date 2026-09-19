---
name: session-handoff
description: Pick up work from a coding session that ended in a different agent harness. Use whenever the user says a session died, crashed, froze, ran out of usage, hit a rate limit or the 5-hour limit, or that they were working in Claude Code / opencode / Codex / Hermes and want to continue, resume, or pick up that project here — including phrasings like "the session died with opencode, continue where I left off", "carry on from my last Claude session", "I ran out of usage in X, keep going". Reads the other harness's session log off disk via `baton`; it needs no model on the dead side.
---

# Continuing someone else's session

The user is not starting new work. Another agent was mid-task, that session
ended, and they want you to carry it. Your first job is to find out what was
happening — never to guess, and never to ask them to re-explain it.

## Get the handoff

Run this before anything else:

```sh
baton continue --dir "$PWD"
```

It prints a handoff document built mechanically from the other harness's own
session log. Add `--from <harness>` when the user named one — `claude`,
`opencode`, `codex` or `hermes`:

```sh
baton continue --dir "$PWD" --from opencode
```

If the work happened somewhere other than the current directory, point `--dir`
at that project.

## When it does not resolve cleanly

- **No sessions found for this directory** — run `baton sessions --dir "$PWD"`.
  If that is empty too, the work happened elsewhere; ask which project, and ask
  nothing else.
- **Wrong session** — `baton sessions` lists every session across all harnesses,
  newest first. Re-run with `--session <id>` once you know which one.
- **`baton: command not found`** — it is not installed. Tell the user, and offer
  to continue from whatever they can tell you instead. Do not fake a handoff.

## Then

1. Read the whole document before touching anything. The **What was asked**
   section is the user's own words, verbatim — that is the brief.
2. Say in one line what you are picking up and where it stopped, so the user can
   correct you immediately if you landed on the wrong session.
3. Re-read the files under **Files this session changed**. They hold the work in
   progress, and the repo is the truth about their current state.
4. Treat the previous agent's work as done. Do not redo it. Do not re-derive
   decisions it already made.
5. Treat the previous agent's *claims* as claims. If it said tests pass, run
   them — the document names the command it used.
6. Continue from the **Next step** section.

The document is saved under `.baton/` in the project, and the full normalized
transcript sits next to it as JSON. Grep that transcript when the summary is not
enough — it holds every turn of the old session.
