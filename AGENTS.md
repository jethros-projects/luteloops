# AGENTS.md

## luteloops files (`lute.yaml`) and the `lute` runner

This repo uses `lute`: loops that run an agent repeatedly until a
machine-checkable `done_when` passes. Loops nest; a parent closes only
when its children have closed and its own check passes.

When asked to write, review, or repair a luteloops file (`lute.yaml`, at
the repo root), or to turn any goal into loops, explore the repo first,
then read and follow the canonical luteloops skill before answering:
`luteloops/SKILL.md`.

Compressed index of its rules, in case the skill is not loaded:
one loop per independently verifiable milestone - decompose along
verifiability boundaries, never activity steps; if you can't name a
milestone's exam, fold it into the parent's task. Nesting means AND,
list order means sequence, check-before-work means if; there is no
if/else, depends_on, or expression language - condition logic lives in
shell inside `done_when`. A check may exit 75 = not yet: the runner
re-asks every `check_every`, wakes no agent, and spends no run budget
(time budgets keep ticking - give watchers one). `gate: human` pauses a
passing loop for approval: READY card, exit 4, sealed by
`lute answer <loop> approve` (re-verified once at seal). Pair `protected:`
(globs) with any exam whose own materials the task could edit; Lute quarantines
attempted edits under `.lute/quarantine/<id>/`, restores trusted copies before
checking/committing, and exposes them with `lute quarantine diff <id>`, so a
run cannot buy a pass by deleting the question; the optional container `cage:` (config)
isolates the filesystem and host secrets for every agent and judge spawn. A
parent may set `parallel: true` to run its children at once, each in its own git
worktree, merged back as each closes - only when they touch disjoint files; the
parent's own exam is the integration check, and a real merge conflict escalates
(exit 3) rather than being auto-resolved.
Compile English into exit codes (existence →
build/types → tests → thresholds → judge last). Exams must be runnable
now, terse in output, and must measure the goal, not the agent's
obedience (no circular exams). `judge:` checks require a rubric with
itemized citations, a model different from the worker, and `confirm: 2`.
Every loop gets a budget; the root carries a global time cap.
Runner-owned state under `.lute/` and `INBOX/` is not normal agent work product;
lute repairs its directories and trusted write paths, and loops should measure
repository outcomes rather than edits to runner state. A draft is not done until
`lute lint` passes its dry-run of every exam.
