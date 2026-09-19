<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="assets/logo-light.svg">
    <img alt="baton" src="assets/logo-light.svg" width="380">
  </picture>
</p>

<p align="center">
  <em>Your plan ran out of usage. The work didn't.</em><br>
  Hand a coding session from one agent harness to another — Claude Code, opencode, Codex, Hermes — without re-explaining anything.
</p>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white">
  <img alt="Zero dependencies" src="https://img.shields.io/badge/runtime-zero%20dependencies-1f2937">
  <img alt="No model calls" src="https://img.shields.io/badge/model%20calls-none-16a34a?logo=ghostery&logoColor=white">
  <img alt="Offline" src="https://img.shields.io/badge/network-offline%20capable-0ea5e9?logo=wifi&logoColor=white">
  <img alt="Tests" src="https://img.shields.io/badge/tests-34%20passing-16a34a?logo=pytest&logoColor=white">
  <img alt="Lint" src="https://img.shields.io/badge/ruff-clean-261230?logo=ruff&logoColor=white">
  <img alt="Harnesses" src="https://img.shields.io/badge/harnesses-4-7c3aed">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-0f766e">
</p>

<p align="center">
  <a href="#-quickstart">Quickstart</a> ·
  <a href="#-what-it-reads">What it reads</a> ·
  <a href="#-just-say-it">Just say it</a> ·
  <a href="#-whats-in-a-handoff">What's in a handoff</a> ·
  <a href="#-architecture">Architecture</a>
</p>

---

## 🧠 The problem with asking an agent to summarize itself

Every "write a handover document" workflow has the same flaw: it needs the model
that is about to die. When Claude says *"you've hit your limit, come back in 4
hours"*, you cannot ask it for anything, least of all a careful summary.

So `baton` never asks. It reads the harness's own session store off disk and
distills the handoff mechanically. It works fine at 100% usage, on a plane, with
the API down.

```
claude ▸ "5-hour limit reached"
      ▸ baton handoff opencode
      ▸ opencode opens, reads the handoff, keeps building
```

## ⚡ Quickstart

```sh
git clone git@github.com:ArttuAn/baton.git ~/baton && cd ~/baton
uv venv && uv pip install -e ".[dev]"
ln -s ~/baton/.venv/bin/baton ~/.local/bin/baton
baton install          # link the skill into every harness you have
baton doctor           # confirm what's readable and launchable
```

Restart a harness once after `baton install`. Then you never run `baton` again —
see below.

## 📚 What it reads

| Harness | Store | Read | Launch |
|---|---|---|---|
| Claude Code | `~/.claude/projects/<cwd-slug>/*.jsonl` | ✅ | `claude "<prompt>"` |
| opencode | `~/.local/share/opencode/opencode.db` (SQLite) | ✅ | `opencode <dir> --prompt` |
| Codex | `~/.codex/sessions/**/rollout-*.jsonl` | ✅ | `codex "<prompt>"` |
| Hermes | `~/.hermes/state.db` (SQLite) | ✅ | `hermes "<prompt>" --in <dir>` |

> [!IMPORTANT]
> Every store is opened **read-only** — the harness you are leaving may still be running.

## 🎯 Just say it

```sh
baton install
```

That links a `session-handoff` skill into every harness you have — Claude Code,
opencode, Codex, Hermes, plus the shared `~/.agents/skills`. After that you run
nothing. You say:

```text
the coding session died with opencode, continue the project where I left off
```

and the agent you are talking to recognizes it, runs `baton continue` itself,
reads the handoff and carries on.

The skill also tells the receiving agent the things that keep a handoff honest:
state which session it picked up so you can correct it, re-read the changed
files, treat the old agent's work as done — and **its claims as claims**.

## 🕹️ Or drive it yourself

```sh
baton doctor                  # which harnesses are readable, launchable, and still parse
baton doctor --quick          # ...without parsing real sessions
baton sessions                # every session for this project, any harness, newest first
baton continue                # print the handoff for the last real session
baton pack                    # just write .baton/handoff-<harness>-<stamp>.md
baton handoff opencode        # pack the live session and open it in opencode
baton resume claude           # later: come back to Claude on the same handoff
```

| Flag | Does |
|---|---|
| `--dir <path>` | Work on another project instead of `$PWD` |
| `--from <harness>` | Force the source harness instead of the most recent session |
| `--session <id>` | Pick a specific session (id or prefix) |
| `--dry-run` | Print the launch command instead of running it |
| `--out <dir>` | Write the handoff somewhere other than `<dir>/.baton` |
| `--minimal` | Only what the repo cannot say itself — see below |

`baton` skips one-turn stub sessions when it picks "the last session" — a launch
test or a crash on the first prompt is never what you meant.

### ✂️ `--minimal`

Roughly a third of a handoff is doing the real work. **Files changed** and
**commands run** are reconstructions of things `git status` and `git diff`
already know more accurately — so `--minimal` drops them and keeps only what
the repository cannot tell the next agent itself:

> the verbatim brief · why it stopped · what already failed

In practice that is a **~65% shorter document** on a real session (6.9 KB → 2.3 KB).
Short handoffs get read; long ones get skimmed. The full transcript is still
linked either way.

### 🐤 The format canary

Every reader parses a store format its vendor never documented and never
promised to keep. When one drifts the failure is **silent**: sessions still
list, the document still renders, it is just empty where it matters.

So `baton doctor` parses the newest real sessions per harness and asserts the
signals a handoff is built from are still coming through:

```
claude      60 sessions    launchable
          format ok — probed 3: 30 prompts, 140 tool calls
opencode    71 sessions    launchable
          format ok — probed 3: 96 prompts, 898 tool calls
codex        1 sessions    launchable
          too few turns to judge the format (2)
hermes      12 sessions    launchable
          format ok — probed 3: 3 prompts, 7 tool calls
```

A signal missing from **every** probed session is drift, and `doctor` exits `1`.
A signal missing from one quiet session is not — stub sessions are skipped and
a sample under six turns reports *"too few turns to judge"* rather than crying
wolf. Absence of evidence is not evidence of absence.

## 📄 What's in a handoff

No summary of a summary — the load-bearing parts are **quoted, not paraphrased**.

| Section | What it holds |
|---|---|
| 🗣️ **What was asked** | Every prompt you typed, verbatim, in order. Tool results and injected system reminders are filtered out, so this is your brief and nothing else. |
| 🛑 **Where it stopped** | The last thing the agent said, and *why* it stopped if the log shows a rate limit, an exhausted quota or a filled context window. |
| 🗂️ **The plan** | The agent's own last todo list. |
| ✏️ **Files changed** | From edit tools, from `git status`, and mined out of shell commands (heredocs and `sed -i` edit files too) — split from files it merely read. |
| ⌨️ **Commands run** | Deduplicated, newest last, heredoc bodies stripped, so what survives is the shell and the verification step after it. |
| 💥 **Things that failed** | So the next agent does not walk into them again. |
| ➡️ **Next step** | Including the command the previous agent verified its work with. |
| 🧾 **Full transcript** | Normalized to JSON next to the document, for when the summary is not enough. The receiving agent can grep it. |

## 🔒 Design guarantees

- 🚫 **No model calls.** The distillation is deterministic Python. Nothing is inferred, nothing is invented.
- 📖 **Read-only at the source.** Session stores are never written, moved or locked.
- 🌐 **No network.** Works offline, mid-flight, or with the API down.
- 📦 **Zero runtime dependencies.** Standard library only.
- 🤫 **Never fakes a handoff.** If it cannot find the session, it says so instead of guessing.

## 🏗️ Architecture

```
  readers/            ─┐
    claude.py          │  harness-specific: the ONLY code that knows
    opencode.py        │  a store format
    codex.py           │
    hermes.py         ─┘
        │
        ▼  normalized Session
  brief.py      distill: prompts, files, commands, failures, stop reason
  render.py     write the markdown handoff
  pack.py       persist to .baton/ alongside the JSON transcript
  workspace.py  project + slug resolution
  cli.py        doctor · sessions · continue · pack · handoff · resume · install
```

Everything downstream of `readers/` works on one normalized `Session`.
**Adding a harness is a single new file** in `src/baton/readers/` exposing
`list_sessions()`, `load()` and `resume_command()`.

## 🧪 Develop

```sh
.venv/bin/python -m pytest -q     # 34 tests
.venv/bin/ruff check .            # clean
```

Readers are tested against **synthetic** fixtures, which prove the parser
matches our idea of each format — they cannot notice a vendor changing it.
That is what `baton doctor` is for: the fixtures guard the logic, the canary
guards the assumption.

## 📜 License

MIT © Arttu Antikainen
