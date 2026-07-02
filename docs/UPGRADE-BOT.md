# Driving a bot's update PR with lute

Renovate and Dependabot open the PR; they do not repair what the bump broke.
That repair — loop an agent against the real merge bar until it passes, on a
branch, with the transcript kept — is exactly lute's shape. This page wires
the two together without inventing anything: a recipe is an ordinary
`lute.yaml`, and CI stays your independent second administration of the same
exam.

## The local flow (start here)

The bot opened `renovate/some-dep-5.x` and CI is red:

```sh
git fetch origin renovate/some-dep-5.x
git checkout renovate/some-dep-5.x
cp path/to/contrib/recipes/npm-dep-major.yaml lute.yaml   # answer its four questions
lute lint
lute run                       # grinds on branch lute/dep-major-repair
```

Lute branches from the bot's branch, so the bump is in the base and the
recipe's exams prove the *bumped* tree installs, tests, and builds. When the
loop closes:

```sh
git checkout renovate/some-dep-5.x
git merge lute/dep-major-repair       # or: lute land renovate/some-dep-5.x
git push
```

CI on the PR re-runs the same commands the recipe's exams ran — a second,
independent administration. If a loop blocks instead (`exit 3`), `lute inbox`
names the card and the exact `lute answer` to type; nothing merges by itself.

## Unattended: cron picks up bot branches

For a steady diet of update PRs, run the same flow on a schedule from a
worktree, one branch at a time — `lute run --skip-if-running` makes overlapping
ticks exit clean, and `on_halt` in `.lute/config.yaml` pings you the moment a
repair blocks or gates. The pattern stays "check out the bot's branch, run the
recipe"; only the invoker changes. Budgets are your cost fuse: a recipe with
`budget: 5 runs` cannot burn more than five agent calls per branch, ever.

## In CI: the `lute-verify` action

`contrib/action/` ships a composite GitHub Action that runs a manifest until
green inside a workflow job and uploads the evidence — `.lute/logs/`,
`.lute/events.jsonl`, and on a closed run `lute-attestation.json`, the
verifiable statement that *this exam passed at this SHA*
(`docs/ATTESTATION.md`; sign it with `actions/attest-build-provenance` to
bind your workflow's identity to it):

```yaml
# .github/workflows/repair-updates.yml
name: repair-updates
on:
  pull_request:
    branches: [main]
jobs:
  repair:
    if: startsWith(github.head_ref, 'renovate/')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { ref: "${{ github.head_ref }}" }
      - uses: jethros-projects/luteloops/contrib/action@v0.2.0
        with:
          manifest: .lute/recipes/dep-major.yaml   # a recipe committed to YOUR repo
          agent: "claude -p --dangerously-skip-permissions"
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - run: |                                    # push the repaired branch back to the PR
          git config user.name  "lute-repair"
          git config user.email "lute-repair@users.noreply.github.com"
          git merge --no-edit "lute/dep-major-repair"
          git push origin "HEAD:${{ github.head_ref }}"
```

Say the honest things about this mode out loud:

- **The agent runs with your secrets' rights.** A CI job is already a cage of
  sorts (ephemeral VM, scoped token), but the API key you hand the engine is
  real. Budget tightly; the action fails the job on exit 3/4 rather than
  looping forever.
- **Gates cannot be approved in CI.** `gate: human` needs a cage and an
  answer key that CI does not have; a gated loop exits 4 and fails the job on
  purpose. Approve locally, or keep gated loops out of CI manifests.
- **Pushing back is your call.** The action itself only runs the loop and
  uploads evidence; the explicit `git push` step above is where repaired work
  re-enters the PR — delete it and the action is verify-and-report only.
- **Cost is bounded by budgets, not by hope.** `runs` budgets cap agent
  invocations per loop; the wall-clock budget caps the job. Start with
  `budget: 5 runs` and read the artifact before raising it.

## Why not let the bot auto-merge on green?

Auto-merge trusts one administration of the exam, taken while an agent with
edit rights was in the room. Lute's runner administers exams against restored,
protected materials and quarantines any attempt to edit them — then CI
re-administers on the pushed result. Two independent green runs of the same
bar, plus a per-attempt transcript, is a defensible reason to let an update
land without a human reading every diff. One green checkmark is not.
