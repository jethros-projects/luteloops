# AGENTS.md

## luteloops files (`lute.yaml`) and the `lute` runner

This repo uses `lute`: loops that run an agent repeatedly until a
machine-checkable `done_when` passes. Loops nest; a parent closes only
when its children have closed and its own check passes.

When asked to write, review, or repair a luteloops file (`lute.yaml` or
`lute.proposed.yaml`), turn a concrete goal into loops, or use
`lute plan --dag`, inspect the minimal repo facts needed to make exams
runnable and non-circular: package/CI commands, existing tests/check scripts,
target paths, and files that should be protected. Then read and follow the
canonical luteloops skill before answering: `luteloops/SKILL.md`.

If `luteloops/SKILL.md` can be read, it is authoritative; this index is only a
fallback checklist.

Non-negotiables:
- one loop per independently verifiable milestone; decompose along
  verifiability boundaries, never activity steps
- nesting means AND, list order means sequence, check-before-work means if
- there is no if/else, runtime `depends_on`, or expression language; condition
  logic lives in shell inside `done_when`
- `lute plan --dag` may use a workflow DAG as planner scratch, and
  `--keep-dag` must write `lute.plan.yaml`, but the runnable manifest stays
  normal Lute YAML with no `depends_on`, `dag`, `nodes`, `edges`, Mermaid,
  Markdown plans, or prose plans in place of loops
- `protected:` covers exam materials the task could edit; quarantined edits
  land under `.lute/quarantine/<id>/` and can be inspected with
  `lute quarantine diff <id>`
- `gate: human` pauses a passing loop for approval; irreversible next steps
  should be gated
- set `parallel: true` only on a parent whose direct child loops touch
  disjoint files/resources; the parent must have its own integration
  `done_when`
- `cage:` is runner config in `.lute/config.yaml`, not loop YAML; it isolates
  filesystem and host secrets for agent and judge commands, while
  `done_when` checks stay host-side
- exit 75 means not yet; the runner re-checks on `check_every`, wakes no agent,
  and spends no run budget, so watcher loops need time budgets
- `judge:` checks require a rubric with itemized citations, a model different
  from the worker, and `confirm: 2`
- every loop gets a budget; the root carries a global time cap

Validation:
Compile English into exit codes (existence -> build/types -> tests ->
thresholds -> judge last). Exams must be runnable now, terse in output, and
measure the goal rather than obedience. Runner-owned state under `.lute/` and
`INBOX/` is not normal agent work product; loops should measure repository
outcomes rather than edits to runner state. A draft is not done until
you run `lute lint`, review its warnings, and it passes its dry-run of every
exam.
