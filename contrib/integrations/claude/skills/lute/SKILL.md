---
name: lute
description: Use when a coding task in the current session is a bounded, machine-checkable, walk-away job — one clear pass/fail shell command decides "done", and you would rather run it to completion (fix a failing suite, repair a dependency-bump PR, a mechanical migration, "make this check pass") than grind it inline turn by turn. Hand it to lute, which loops a coding agent until the check passes, commits each attempt, and cannot fake done. Do NOT use for interactive/exploratory work, or when there is no machine-checkable finish line.
---

# Handing a task to lute

`lute` runs a coding agent again and again until a machine-checkable shell check
exits 0, committing every attempt to a git branch. The runner re-runs the check
itself, so a close means the exam passed — not that the agent said so, and the
agent cannot pass by deleting the test. It is the harness for the runs you leave.

You reach lute through its MCP tools (`lute_once`, `lute_plan`, `lute_run`,
`lute_watch`, `lute_status`, `lute_inbox`, `lute_answer`, …). If they are not
available, the same verbs run from the shell as `lute <verb>` — tell the user to
register the server (`docs/INTEGRATIONS.md`) or install lute.

## When to hand off (all three must hold)

1. **A clear finish line exists** — one shell command whose exit 0 means done
   (`pytest -q`, `npm run build`, `! grep -rn 'legacyApi' src/`). If you cannot
   name that command, this is not a lute job.
2. **The work is grindy and bounded** — repair, upgrade, migration, keep-green
   maintenance; the kind of thing that takes several iterations of edit-and-check.
3. **Running it to completion is fine** — the user wants the result, not to watch
   every step. If they are actively pairing with you on this, keep doing it here.

## When NOT to hand off (do it yourself)

- Interactive, exploratory, or design work; anything without a machine-checkable
  check; a one-line fix you can just make now; or a task the user is steering
  turn by turn. Handing those to lute adds a slow, ceremonious detour. When in
  doubt, do it yourself — hand off only when the three signals above are clear.

## How to hand off

**A single check → `lute_once`.** No manifest needed.
- `task`: what one iteration should do. `until`: the shell check. `agent`: the
  engine lute drives — `codex exec --sandbox workspace-write`, or
  `claude -p --permission-mode acceptEdits` (full command power like installs
  needs `claude -p --dangerously-skip-permissions`, best inside a cage). `budget`:
  cap it (e.g. `20 runs`) so it can't run away.
- It detaches and returns fast. Then poll `lute_watch` (JSON: `outcome` is
  `running`/`closed`/`blocked`/`gated`).

**A multi-step goal → `lute_plan` then `lute_run`.** `lute_plan` drafts a nested
`lute.proposed.yaml`; show the user the draft, then `lute_run` it. Use for
upgrades/migrations that need sequencing.

**While it runs.** `lute_watch` (or `lute_status`) shows the loop tree. If a loop
blocks or gates, `lute_inbox` names it and the exact `lute_answer` to send
(a blocked loop takes guidance; a gated loop seals only on `approve`). When the
root closes, the work is on branch `lute/<root>` — review it, then merge (or
`lute_land`, which merges only if the exam still passes on the merged tree).

## What to tell the user

Say plainly that you are handing the task to lute, why (the check, the branch,
the budget), and how they'll see it (watch/status). Never present lute's close as
your own verification — the point of lute is that the *exam* verified it, and the
transcript is on the branch for them to inspect.
