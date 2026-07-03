# Handing a task to lute (paste into your repo's AGENTS.md)

This repo has `lute` available: it loops a coding agent until a machine-checkable
shell check passes, committing every attempt to a git branch. The runner re-runs
the check itself, so a close means the exam passed — not that the agent said so.
lute's verbs are exposed as MCP tools (`lute_once`, `lute_plan`, `lute_run`,
`lute_watch`, `lute_status`, `lute_inbox`, `lute_answer`, …) and also run from the
shell as `lute <verb>`.

## Hand a task to lute when all three hold

- **There is one clear pass/fail command** for "done" (`pytest -q`,
  `npm run build`, `! grep -rn 'legacyApi' src/`). No such command → not a lute job.
- **The work is grindy and bounded** — repair, upgrade, migration, keep-green
  maintenance; several edit-and-check iterations.
- **Running it to completion is fine** — the user wants the outcome, not to watch
  each step.

Do NOT use lute for interactive, exploratory, or design work, for a one-line fix,
or when the user is steering turn by turn. When in doubt, do it yourself.

## How

- A single check: `lute_once` with `task`, `until` (the check), `agent`
  (`codex exec --sandbox workspace-write`), and a `budget` (e.g. `20 runs`). It
  detaches; poll `lute_watch` (JSON `outcome`).
- A multi-step goal: `lute_plan "<goal>"` drafts `lute.proposed.yaml`; review it,
  then `lute_run`.
- If a loop blocks/gates, `lute_inbox` names the exact `lute_answer` to send.
  When the root closes, the work is on `lute/<root>` — review, then merge or
  `lute_land`.

Tell the user you are handing the task to lute, name the check/branch/budget, and
never present lute's close as your own verification — the exam verified it, and the
transcript is on the branch.
