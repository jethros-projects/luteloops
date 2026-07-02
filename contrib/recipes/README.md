# Recipes — verified upgrades, ready to adapt

A recipe is an ordinary `lute.yaml` whose exams already encode the honest
merge bar for one well-known job. Nothing here is a new mechanism: copy the
file into your repo as `lute.yaml`, answer the four questions below, run
`lute lint`, then `lute run`.

| recipe | job |
|---|---|
| [`react-major-upgrade.yaml`](react-major-upgrade.yaml) | move a repo across a React major |
| [`npm-dep-major.yaml`](npm-dep-major.yaml) | repair the breakage behind one Renovate/Dependabot major bump |
| [`django-major-upgrade.yaml`](django-major-upgrade.yaml) | move a repo across a Django major, deprecation-clean |
| [`node-lts-bump.yaml`](node-lts-bump.yaml) | move a repo to the next Node LTS |

Driving a bot's update PR (Renovate, Dependabot) with a recipe — checkout
flow, CI wiring, landing — is `docs/UPGRADE-BOT.md`.

## The four questions (answer them before `lute run`)

1. **What is the exam?** The root `done_when` must restate the whole merge
   bar mechanically — install from lockfile, full suite, build. If your repo
   has a smoke script or a golden file, add it to the root exam; it is worth
   more than any instruction in a task.
2. **What is protected?** Everything the task could edit to buy a pass:
   `tests/**`, `checks/**`, golden files. The recipes protect the usual
   suspects; add yours. Lute quarantines edits to protected materials and
   restores the trusted copy — inspect with `lute quarantine`.
3. **What is the budget?** The shipped budgets assume a mid-size repo.
   Tighten them for a first unattended run (`budget: 5 runs` fails fast and
   cheap); loosen only after you have read a transcript or two.
4. **What gates?** If the loop's branch will auto-land or deploy, add
   `gate: human` to the last loop (requires `cage:` — see the README). For
   review-then-merge-yourself, the default is already right: `lute run`
   stops at a branch.

## Engine rights

Every recipe's agent must run package managers, so the examples use full
command rights (`claude -p --dangerously-skip-permissions`), which belongs
inside `cage: docker`. Swap in any engine that reads stdin and can edit and
run commands — `codex exec --sandbox workspace-write` works when the job
needs no network beyond what its sandbox allows. The README's *engine
gotchas* section is the contract.

## What a finished run gives you

A `lute/<loop>` branch with one commit per attempt, per-run transcripts under
`.lute/logs/`, the event stream in `.lute/events.jsonl`, and an exit code you
can script against (0 closed · 3 blocked · 4 gated). `lute land` merges only
if the root exam re-passes on the merged tree.
