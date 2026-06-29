# Lute Loops v0.1.0

> Turtles all the way down

AI coding agents changed the bottleneck. The hard part is no longer getting a
model to write code. The hard part is proving the code is actually done.

Did the tests really pass? Did it delete the test? Did it leave a half-fixed
migration on a branch somewhere? Did it spend twenty runs rediscovering the
same dead end?

**Lute is the missing primitive: a while-loop for agents with a real exam.**
Give it any CLI agent that reads stdin and edits the working directory
(`claude -p`, `codex`, a shell script, your own tool). Lute runs the agent,
re-runs the check, commits the attempt, and repeats until the machine-checkable
`done_when` passes.

It is deliberately plain. A Lute run is just your repo, a `lute.yaml`, a git
branch, logs, cards, and checks.
The agent can be clever. The runner stays boring. That is the point.

**In one sentence:** Lute turns "I think the agent is done" into "the exam
passed, the work is on a branch, and the transcript is inspectable."

**Who this is for:**

- **Founders and solo builders** who want AI agents to grind through real work
  without pretending a vibes-only answer is done
- **Staff engineers and maintainers** running upgrades, migrations, bug fixes,
  release prep, or test repair across a repo
- **Teams using Claude, Codex, or custom agents** who want one shared finish
  line: shell checks, budgets, gates, and human escalation
- **People who like small tools**: everything important is in git, `.lute/`,
  `INBOX/`, and the files your agent changed

## Quick Start

1. Install Lute
2. Try a one-shot loop against a real check
3. For bigger work, write or generate a `lute.yaml`
4. Let the loop run until it closes, blocks, or asks for approval

```sh
lute once --until "pytest -q" --agent "claude -p" -- "make the failing tests pass"
```

`once` writes no `lute.yaml`: it runs the agent on a throwaway `lute/once`
branch until `--until` exits 0, then stops. The check is mandatory. That is
what makes Lute different from a bare `while agent; do ...; done`.

For larger jobs:

```sh
lute plan "upgrade this repo to React 19"
lute lint lute.proposed.yaml
mv lute.proposed.yaml lute.yaml
lute run
```

For dependency-heavy jobs, `lute plan --dag "..."` asks the planner to reason
from a workflow dependency graph first, while still writing ordinary
`lute.proposed.yaml`. Pass `--keep-dag` to also write the intermediate
`lute.plan.yaml` review artifact.

`lute plan` builds a bounded repo briefing first: git status, detected
build/test/CI signals, existing test/check files, path hints from the goal, and
root `AGENTS.md` guidance. It then gives the agent that briefing plus the
packaged luteloops skill. Run `lute init --skill` only when you want a local
copy to inspect or customize.

## Install: One Paste

Paste this in a terminal to install Lute:

```sh
curl -fsSL https://raw.githubusercontent.com/jethros-projects/luteloops/v0.1.0/scripts/install.sh | bash
```

Uninstall just the tool, leaving project repos, `.lute/`, `INBOX/`, branches,
logs, and crontab entries alone:

```sh
curl -fsSL https://raw.githubusercontent.com/jethros-projects/luteloops/v0.1.0/scripts/uninstall.sh | bash
```

Or ask Codex/Claude Code:

```text
Install Lute from https://github.com/jethros-projects/luteloops by running the project installer. Verify with lute --help.
```

When the PyPI package is published, Python 3.10+ only:

```sh
pipx install luteloops
lute --help
```

To install the tagged release directly:

```sh
pipx install git+https://github.com/jethros-projects/luteloops.git@v0.1.0
```

Or run it straight from a checkout. The executable is a tiny `lute` script, and
the runtime stays Python standard library + PyYAML:

```sh
python3 -m pip install pyyaml      # only needed for checkout-style use
python3 lute --help                # zero-install: run it from the checkout
export PATH="$PWD:$PATH"           # optional: keep this checkout on PATH
```

Keep `lute`, `lute_core/`, and `luteloops/` together for checkout-style use;
the script is intentionally tiny and imports the package next to it.

## See It Work

```text
You: lute once --until "pytest -q" --agent "claude -p" -- "fix the failing tests"

Lute: check failed. Starting run 1 on branch lute/once.
Agent: edits app.py, appends the journal, exits.
Lute: pytest still fails. Commit the attempt. Start run 2.
Agent: fixes the edge case it missed.
Lute: pytest passes. Commit the close. Done.
```

That is the whole product. Agent writes. Lute checks. Git records. Budgets stop
runaway loops. Cards pull you back in when the model needs help. The runner
never trusts the agent's confidence or exit code. The exam decides.

## The Loop

Lute is a process, not a prompt library:

**Check -> Work -> Verify -> Commit -> Repeat -> Escalate**

- **Check first:** if `done_when` already passes, no agent wakes up
- **Work in fresh iterations:** each run gets the failing output and the loop
  task on stdin
- **Verify outside the agent:** Lute re-runs `done_when` itself
- **Commit every attempt:** inspect, diff, bisect, land, or throw away the branch
- **Escalate when needed:** budgets, gates, merge conflicts, and missing
  decisions produce `INBOX/` cards instead of fake success

Nested loops close from the inside out. A parent closes only when its children
have closed and its own check passes. There is no `if`/`else`, `depends_on`, or
expression language. Order plus shell exit codes are the control flow.

## Nested Loops Scale The Work

This is where Lute gets big without getting complicated. A loop can contain
loops, and those loops can contain loops. The same rule applies at every level:
children close first, then the parent exam proves the integrated result.

That means a repo-sized migration can become a set of smaller exams instead of
one giant prompt. Each loop can have its own task, check, budget, confirm
streak, gate, protected files, and even its own agent. The root stays honest by
running the final exam for the whole project.

```yaml
loop: billing-migration
agent: claude -p
budget: 72h
done_when: "pytest tests/billing tests/api && npm test"
loops:
  - loop: data-model
    budget: 30 runs
    done_when: "pytest tests/billing/db"
    loops:
      - loop: ledger-schema
        task: Add the new ledger tables and migrations.
        done_when: "python scripts/check_schema.py"
        protected: ["scripts/check_schema.py"]
        budget: 8 runs
      - loop: backfill
        task: Write the idempotent backfill and its tests.
        done_when: "pytest tests/billing/test_backfill.py"
        budget: 10 runs

  - loop: billing-api
    budget: 30 runs
    done_when: "pytest tests/api/billing"
    loops:
      - loop: invoice-endpoints
        task: Move invoice reads and writes onto the new ledger.
        done_when: "pytest tests/api/billing/test_invoices.py"
        budget: 10 runs
      - loop: webhooks
        task: Preserve webhook behavior through the migration.
        done_when: "pytest tests/api/billing/test_webhooks.py"
        confirm: 2
        budget: 10 runs

  - loop: billing-ui
    budget: 20 runs
    done_when: "npm test -- --run billing"
    loops:
      - loop: invoice-screen
        task: Update the invoice UI for the ledger-backed API.
        done_when: "npm test -- --run invoice-screen"
        budget: 8 runs
      - loop: admin-reporting
        task: Keep admin reports consistent with the migrated data.
        done_when: "npm test -- --run admin-reporting"
        budget: 8 runs
```

The scale claim is real, but bounded. Lute is not literally infinite:
your runtime, repo size, git operations, checks, agents, and patience are all
finite. The scalable part is that the runner does not need a new abstraction
when the work gets larger. If the job can be decomposed into independently
checkable milestones, you can keep nesting the same primitive and let each
child loop close under its own proof.

## DAG Planning, Lute Output

`lute plan --dag "<goal>"` is an authoring aid for complicated plans. The
planner first identifies checkable milestones and prerequisite edges, then
compiles that reasoning back into normal Lute YAML: list order for sequence,
nesting for integration, shell checks for conditions, and `parallel: true` only
for independent direct siblings with disjoint files/resources.

The final `lute.proposed.yaml` never gains `depends_on`, `dag`, `nodes`,
`edges`, Mermaid, Markdown plans, or a graph scheduler. It is the same contract
as a hand-written `lute.yaml`: children close first, the parent proves the
merged result, and the root exam proves the whole goal. Use `--keep-dag` when
you want to inspect the planner's `lute.plan.yaml` review artifact; Lute still
runs only the compiled proposal after you review and rename it.

## The Commands

| verb | what it does |
|---|---|
| `lute init` | scaffold a `lute.yaml` and `.lute/` (or `lute init --skill` to write a local copy of the packaged luteloops skill) |
| `lute lint [file]` | validate the schema, resolve agents, and **execute every `done_when` once**, classifying each pass / fail / error; an error fails the lint, because an exam must be administrable before work begins |
| `lute run [root-id]` | run loops depth-first until everything is green (`--agent CMD`, `--file F`, `--plain`, `--bg` to detach, `--dry-run` to preview the plan + first prompt without spending); child loops run through their parent |
| `lute once --until C -- "task"` | one-shot, no file: run an agent until check `C` passes (`--agent`, `--id`, `--budget`) |
| `lute watch [file]` | read-only event snapshot for a running or finished run (`--snapshot` text, `--json` machine-readable) |
| `lute status [file]` | re-run each check once and print the loop hierarchy: ✔ done / ↻ running / ⏳ waiting / ✗ blocked / ✋ gated, plus cumulative agent time |
| `lute inbox` | list every blocked/gated loop with the exact command to answer it |
| `lute answer <loop> "..."` | reply to a card in `INBOX/`; the next run injects it and refreshes that loop's run budget once |
| `lute quarantine [list|diff <id>|drop <id>|drop --all]` | inspect or remove stored patches for trusted exam/control edits that Lute quarantined out of run commits |
| `lute stop` | cleanly stop the active run (and any parallel children) in this repo |
| `lute land [branch]` | merge `lute/<root>` into the start branch **only if the root exam still passes against the merged tree**; conflict or a failed re-check aborts clean and escalates (opt-in; the default is review-then-merge-yourself) |
| `lute plan [--dag] [--keep-dag] "<goal>"` | an agent reads the luteloops skill and drafts `lute.proposed.yaml`; `--dag` uses dependency planning first, and `--keep-dag` also writes `lute.plan.yaml` for review |

Plus `lute cron sync` / `lute cron remove` for the `schedules:` manifest (below), and
`lute --help` / `lute <verb> --help` / `lute --version`.

## What Makes It Safe To Let Run

| feature | why it matters |
|---|---|
| Machine checks | "Done" means a command exited 0, not that the model sounds confident |
| Budgets | Cap loops by run count or wall-clock time; stuck agents become cards |
| Nested loops | Turn huge goals into independently checkable milestones |
| Journals | Keep short memory across fresh agent processes |
| Confirm streaks | Require multiple consecutive passes for flaky checks |
| `gate: human` | Pause before deploy, publish, migrate, send, or other irreversible steps |
| `protected:` | Quarantine edits to exam materials before they can buy a pass or enter the run commit |
| `cage:` | Run model-facing commands in a container with explicit mounts |
| `parallel: true` | Run independent child loops in separate worktrees, then integrate |
| `watch --json` | Stable machine-readable status for wrappers, cron, dashboards, and scripts |

### Contracts (for scripting lute / bringing your own agent)

**Exit codes:** a wrapper branches on these:

| code | meaning |
|---|---|
| `0` | all loops closed (or landed) |
| `1` | usage / precondition (bad invocation, missing file, dirty tree) |
| `2` | internal/git error |
| `3` | blocked: a loop hit its budget or a parallel/land merge conflicted; see `lute inbox`, then `lute answer` |
| `4` | gated: a passing loop is awaiting human approval (`lute answer <loop> approve`) |

For a detached or cron run the exit code reaches no one; read `lute watch --snapshot --json`, a
pure projection of events (no rechecks). Its shape is stable:

```jsonc
{
  "root": "build",            // root loop id
  "outcome": "blocked",       // running | closed | blocked | gated: the canonical verdict
  "exit": 3,                  // matching exit int; null while outcome is "running"
  "ended": true,              // a run_end event was seen
  "branch": "lute/build",
  "tree": { "id": "build", "word": "blocked", "runs": 2, "secs": 41.0, "active": false,
            "children": [ /* same shape, recursively */ ] },
  "cards": [ { "lid": "build", "gated": false, "answered": false, "next": "lute answer build \"...\"" } ]
}
```

Match on `outcome` (and per-node `word`), not the per-node `mark` glyph; `mark` is presentational
and may change. `exit` is `null` while `outcome` is `running`, then the integer code once it halts.

**The agent contract:** any CLI is a valid engine if: it reads the prompt on **stdin**, makes its
edits **in the working directory**, and exits. The runner stages tracked changes plus new files
created during that run; it leaves pre-existing untracked clutter and `INBOX/` cards alone. The
agent's **exit code is logged but never trusted**; the *only* verdict is the runner re-running
`done_when`. That is why lute can't lie about doneness, and why your wrapper need not produce a
meaningful exit code.

**State ownership:** normal repo content outside `.lute/` and `INBOX/` is agent-owned work
product. Runner-owned state is `.lute/config.yaml`, `.lute/ledger.jsonl`, `.lute/events.jsonl`,
`.lute/lock`, `.lute/journal/*`, `.lute/logs/*`, `.lute/wt/`, and `INBOX/*`. Before writing events,
ledger entries, logs, cards, or lock files, lute repairs those paths as real files/directories and
never follows agent-created symlinks. Journals are prompt memory: agents append to them by contract,
but budget and closure decisions never trust journal contents. If an agent deletes `.lute/logs`,
symlinks the ledger to `/dev/null`, truncates it, or rewrites durations, the runner restores trusted
state and budget accounting continues from the ledger snapshot plus committed run history.

## Write Your First `lute.yaml`

Upgrade React, the lute way: write the exams, then let the loop grind:

```yaml
# lute.yaml
loop: react-19
agent: claude -p
budget: 48h
done_when: "npm test && npm run build"
loops:
  - loop: bump-react             # if-trick: skipped when already on 19
    task: Upgrade react and react-dom to ^19 in package.json, npm install.
    done_when: "node -e 'process.exit(require(\"react/package.json\").version.startsWith(\"19\")?0:1)'"
    budget: 3 runs
  - loop: fix-build
    task: Fix every build error from the upgrade. No downgrades, no ts-ignore.
    done_when: "npm run build"
    budget: 15 runs
  - loop: fix-tests
    task: Repair tests broken by the upgrade. Never delete or skip a test.
    done_when: "npm test"
    confirm: 2
    budget: 15 runs
```

Then:

```sh
lute lint     # every exam is executed once before any work starts
lute run      # grinds on branch lute/react-19, one commit per iteration
lute status   # ✔ done / ↻ in progress / ◌ untouched
```

If a loop exhausts its budget you get `INBOX/<loop>.md` and exit code 3;
reply with `lute answer fix-tests "the snapshot tests are obsolete; regenerate them"`
and run again. Writing good loops is a skill, literally: see
`luteloops/SKILL.md`, which `lute plan` injects into its drafting prompt after a
bounded repo briefing.

> **On cost:** `budget` caps *iterations* (`N runs`) and *wall-clock* (`48h`);
> never tokens or dollars; lute can't see your agent's API spend. `lute status`
> reports cumulative runs and agent time so you can eyeball consumption, and
> `lute inbox` shows what's waiting on you. Set a tight `runs` budget if the bill matters.

## Unattended runs

Start it, walk away, get pulled back only when it needs you:

- **Detach:** `lute run --bg` returns immediately; the run lives in its own session and
  survives the terminal closing; re-attach with `lute watch`, end it with `lute stop`
- **Get notified:** set `on_halt:` in `.lute/config.yaml` to your own notifier; it fires
  the instant a loop blocks or gates, with `$LUTE_LOOP`, `$LUTE_REASON` (`blocked`/`gated`)
  and `$LUTE_CARD` in the environment (fire-and-forget; a failing hook never breaks the run):
  ```yaml
  # .lute/config.yaml
  on_halt: 'curl -fsS -d "$LUTE_LOOP $LUTE_REASON" https://ntfy.sh/your-topic'
  ```
- **Come back to it:** `lute inbox` lists what's waiting and the exact `lute answer` to type;
  `lute watch --snapshot --json` is a stable surface for a wrapping script

## Run State And Watch

Runs write files; renderers read files. Every agent run's full transcript lands
in `.lute/logs/<loop>.run<N>.log` (`tail -f` works mid-run), and the runner
appends one JSON event per line to `.lute/events.jsonl`.

In a real terminal, `lute run` detaches into its own session and prints the
process id plus the follow-up commands:

```text
detached: run continues (pid N) · re-attach: lute watch · stop: lute stop
```

`lute run --bg` takes the same detached path explicitly; output from the runner
itself lands in `.lute/logs/runner.log`, which is handy for cron and scripts.
Use `lute run --plain` when you want a foreground process that streams one
compact line per event and exits with the run result.

`lute watch` is read-only and replay-only: it renders the current loop hierarchy
once from `.lute/events.jsonl`, without re-running checks. `lute watch --json`
emits the same replay state for wrappers, dashboards, and cron probes. To inspect
the active agent transcript, tail the log path named by the stream or event file;
`lute watch --filter .lute/logs/<loop>.run<N>.log` prints that log with repeated
blocks collapsed to a single copy with a `×N` marker. Logs, events, worktrees,
and the run lock are runner-owned runtime state and stay out of your commits;
journals and the ledger are durable runner files committed by Lute after a run,
with ledger writes repaired and authenticated through the state store.

## Parallel siblings (`parallel: true`)

By default children run **sequentially**, in document order. When independent
child loops each take real time, mark their **parent** `parallel: true` and all of
its direct children run **at once**, each in its own git worktree on its own
branch, as a separate `lute run` process:

```yaml
loop: ship-services
done_when: "./integration-test.sh"   # the parent exam IS the integration check
parallel: true
loops:
  - loop: api          # the three run concurrently, each in .lute/wt/<root>__<id>
    task: Build the API. Bind PORT=$((3000+LUTE_SLOT)).
    done_when: "cd api && npm test"
  - loop: web
    task: Build the web app.
    done_when: "cd web && npm test"
  - loop: worker
    task: Build the worker.
    done_when: "cd worker && npm test"
```

Isolation is a worktree per child; reconciliation is `git merge` as each closes.
**Children must be genuinely independent: touching disjoint files.**
Non-overlapping edits auto-merge; a real conflict is **not** auto-resolved: the
run halts with an escalation card naming the conflicting files and loops, the
parent branch left clean, **exit 3**; make the edits disjoint (or merge by
hand) and re-run. If a child escalates or gates instead of closing, the parent
collects all children to a stopping point, relays their cards, merges none, and
exits with the most severe child code. After every child merges cleanly, Lute
re-runs each direct child `done_when` once against the merged tree before the
parent can close. If a child invariant was broken by the merge, the failure
becomes the parent loop's next repair prompt. The parent still runs **its own
`done_when` on the integrated tree**, so write the parent exam to cover
cross-child behavior that no child owns alone.

`LUTE_SLOT` (1, 2, 3… per child) lets checks dodge collisions: a per-slot port
(`PORT=$((3000+LUTE_SLOT))`) or scratch path. A run is **crash-durable by
re-derivation**: `git worktree list` and the child branches are the state, so a
re-run skips children whose work is already merged and resumes the rest in their
worktrees. Only one top-level `lute run` may be active per repo; a `.lute/lock`
(pid + start) guards it, and a lock whose pid is dead is stale and reclaimed.

## Watchers (exit 75 = "not yet")

A check has three honest answers, not two: exit 0 is pass, **exit 75 is
"not yet"**: nothing is wrong, nothing is done, ask me later. Anything
else is fail. On a not-yet the runner wakes **no** agent and spends **no**
run budget; it sleeps `check_every` (a new optional per-loop field: `30s`,
`5m`, `2h`; default 60s) and re-asks. Because run budgets do not tick while
waiting, any loop whose check returns 75 must have an `s`/`m`/`h` time budget.
`lute lint` errors when a dry-run returns 75 without a time cap, and `lute run`
escalates immediately instead of hanging. Only a real failure's output ever
rides into an agent prompt; silence is not evidence.

```yaml
loop: deploy-quiet
task: Investigate and fix whatever broke the deploy.
done_when: "./checks/quiet.sh"   # 0 quiet 24h · 75 waiting · 1 alerts found
check_every: 30m
budget: 48h
```

Combine the trio: a not-yet check, `lute run --bg`, and `lute cron sync`,
and lute is a monitor that costs ~nothing while things are healthy and spends
exactly one agent run per real problem. The event stream, `watch`, and plain
mode show waiting loops as ⏳ (`⏳ deploy-quiet: not yet · next check in 30m`),
and `lute lint` classifies a capped 75 dry-run as `not_yet`: a valid,
lint-passing outcome.

## Gates (`gate: human`)

Some loops precede an irreversible verb: deploy, publish, send, migrate,
and must not auto-close on a green check. Mark them `gate: human`: when such
a loop would close, the run pauses with a **READY** card in `INBOX/` and
**exit 4** ("needs approval", distinct from exit 3's "needs help"). The
canonical pattern is a checkpoint between *preparing* and *acting*; list
order does the rest:

```yaml
loops:
  - loop: release-ready
    done_when: "npm run build && test -f CHANGELOG.md"
    gate: human                  # ✋ READY card, exit 4
  - loop: ship                   # runs only after your approval
    task: npm publish, then verify.
    done_when: "npm view mypkg version | grep -qx 1.4.0"
```

Because approval is authenticated by a key under `~/.lute/keys/`, `gate: human`
requires a configured `cage:`. Without a cage, the agent runs as your user and
can read the answer-auth key, so it can forge the approval token. `lute lint`
reports this as an error, and `lute run` refuses a gated manifest before work
starts. Use the cage even if the host has no other secrets; for gates, it is the
trust anchor, not just a convenience.

Approve with `lute answer release-ready approve` (any answer text counts;
the word is convention, the text is recorded). On the next run the exam is
re-verified once before sealing; if the world moved overnight the card is
marked `SUPERSEDED` and the loop takes the normal fail path. To **reject**,
you don't answer. Change whatever needed changing and re-run; the gate
re-examines the new state. A gated loop waiting for you is exempt from
time-budget expiry: the limit exists for unattended waiting, and a gate is
attended by definition.

## Protected exams (`protected:`)

An agent runs as you, with your toolchain, so a careless or adversarial one
can pass an exam by *editing the exam* instead of doing the work: delete the
failing test and `done_when` goes green. List the exam's own materials under
`protected:`: globs, relative to the repo root, matched against the working
tree (so untracked check scripts count too):

```yaml
loop: make-it-pass
task: Make the suite pass. Do not touch the tests.
done_when: "pytest -q"
protected:
  - "tests/**"
  - "checks/**"
budget: 20 runs
```

Lute compares those globs to their committed originals at the branch base.
Before and after checks, and after each agent run, it re-hashes the watched
materials plus trusted control files (the active manifest, whether `lute.yaml`
or `--file`, and `.lute/config.yaml`). If an agent modifies, deletes, or newly adds a watched file,
Lute saves the attempted edit under `.lute/quarantine/<id>/`, restores the
trusted copy, and leaves the quarantined edit out of the normal run commit. The
next prompt names the quarantine record so the agent can fix the actual work
instead of redefining the exam. Inspect records with:

```sh
lute quarantine
lute quarantine diff <id>
lute quarantine drop <id>     # or: lute quarantine drop --all
```

The guard is opt-in per loop for `protected:` exam materials, while the active
manifest and config file are trusted control inputs. `lute once` is fileless, so
a committed `lute.yaml` is ordinary work there unless it is also listed under
`protected:`. `done_when` checks still run host-side; this protects the exam's
materials and control inputs from model-facing commands. `lute lint` warns when
a `protected:` glob matches nothing and when an inferable local check file is not
covered by `protected:`.

## The cage (`cage:`)

By default an agent shares your filesystem and can read `~/.ssh`. Set
`cage: docker` in `.lute/config.yaml` and every command lute runs *on behalf of
a model*: per-loop agents and `judge:` commands run inside a container that
sees only your repo (read-write at `/work`) and whatever you name explicitly.
`done_when` checks stay on the host (they're yours and need your toolchain):

```yaml
# .lute/config.yaml
cage: docker                # or a custom template (podman) using {repo} {image} {cmd} {mounts}
cage_image: my-agent-cage   # YOUR image; it must contain your agent CLI
cage_mounts:                # extra host paths, mounted read-only, by name
  - "~/.config/my-agent"    # agent auth enters here; never implicitly
```

The prompt still flows on stdin; output still lands in the same per-run log.
**Secrets policy is absence:** nothing of the host is visible except the repo
and what `cage_mounts` names, so `~/.ssh` and your environment simply aren't
there. The image is yours to build: it must contain your agent CLI, and auth
enters read-only through `cage_mounts`, by name, never implicitly.
`contrib/cage/Dockerfile` is a worked Codex sample (`node:20-slim` +
`@openai/codex` + `git`). The initial release boundary is **filesystem +
secrets isolation**. Network egress control is a later notch: a caged
container can still reach the network.

The same isolation protects Lute's own answer-auth key. Answered cards can
refresh a loop's budget once, and gated cards seal human approval. If agents are
uncaged, those mechanisms are useful operator workflow, not adversarial security
boundaries; `lint` warns for budget-refreshable loops and errors for human gates.

## Schedules (cron, not a daemon)

A top-level `schedules:` section is a manifest, never a runtime:

```yaml
schedules:
  - run: react-19        # root-level loops only
    at: "0 9 * * *"
```

`lute cron sync` compiles it into a managed block in your crontab
(`# BEGIN lute <repo> … # END lute <repo>`), idempotent and removable with
`lute cron remove`. Each tick is a fresh `lute run <root-id>`; loops
themselves never gain a time field. Note: cron jobs run with a minimal
environment; make sure your agent CLI is on cron's `PATH`, and check
`mail` (or wrap the entry) for tick output.

## What's deliberately not here

The initial release is the small durable primitive: foreground, branch-only,
fast-check-first (parallel siblings are opt-in per parent, but a lone loop still
runs as one plain process). `lute plan --dag` does not add runtime DAG syntax,
automatic graph scheduling, or a `depends_on` manifest key. The verdict cache,
cron-resumed ticks on an always-on box, merge gates, agent-resolved merge
conflicts, registry, and cage network egress control are deliberately outside
the initial release. They should enter only when a real loop fails without them,
and only if they add no required fields.
