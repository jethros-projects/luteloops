---
name: luteloops
description: Use when Codex needs to author, review, repair, or validate Lute loop manifests (`lute.yaml` or `lute.proposed.yaml`), convert a concrete goal into checkable Lute loops, use `lute plan --dag`, or tighten done_when exams, budgets, gates, protected materials, judges, and parallelism. Do not use for general Lute discussion or runner implementation debugging unless loop-manifest design is part of the request.
---

# LUTELOOPS - how to compile a goal into loops

## 1. What `lute` is (cold-start context)

`lute` runs an agent again and again until a check passes, then stops. Loops nest: a parent closes only when all its children have closed AND its own check passes; the outermost loop cannot close until its own exam passes, and truth still depends on writing a strong exam. The runner is deliberately plain: it administers checks, budgets, cards, approvals, and runner-owned state; all product intelligence lives in the agents doing the work and in the quality of the checks you write here. **Your output is a contract, not a program.**

## 2. Grammar

```yaml
loop:       kebab-case id (required)
task:       instructions for the worker agent (optional - omit on a
            parent that only aggregates children)
agent:      worker command, e.g. "claude -p" (inherited from parent)
done_when:  shell command (0 = pass, 75 = not yet, else fail) OR
            "judge: <rubric>"
budget:     "N runs", "Ns"/"Nm"/"Nh", combinable with "/" (default 10 runs)
confirm:    consecutive passes needed to close (default 1); fail and
            not-yet both reset the streak
check_every: positive re-ask cadence after a not-yet verdict ("30s", "5m";
            default 60s)
gate:       "human" - pause a passing loop for approval (READY card,
            exit 4; only exact `lute answer <loop> approve` seals)
protected:  globs for exam materials the task could edit
parallel:   true on a parent - run its children at once (worktree each,
            merged back as each closes); default false = sequential
loops:      ordered children
```

Fixed semantics you must exploit instead of reinventing: **nesting = AND** (parent needs all children + own exam), **list order = sequence** (write B below A; never invent depends_on), **check-before-work = if** (a loop whose check already passes is skipped - to express "only do X when Y is false", write a loop whose done_when is Y). There is no if/else, no expressions, no hooks. If you feel the need for control flow, you are decomposing wrong (§3) or the logic belongs in a check (§4), a task, or an escalation.

This is the loop grammar. A manifest may also have top-level `schedules:`,
but runner configuration belongs outside `lute.yaml`: `cage:` lives in
`.lute/config.yaml`, wraps model-facing agent and judge commands, and is for
untrusted code or host-secret isolation. `done_when` checks still run as host
checks; do not hide check logic in cage setup.

Human approval is authenticated with a local answer key. If a manifest uses
`gate: human`, require a cage in `.lute/config.yaml`; uncaged agents run as the
user and can read that key. Answered cards also refresh budgets once, so budget
resets are not an adversarial security boundary unless the model-facing commands
are caged.

When invoked through `lute plan --dag`, use a workflow DAG only as a planning
aid: identify checkable milestone nodes, prerequisite edges, fan-out/fan-in,
and possible concurrency, then compile that reasoning into normal Lute YAML.
Map the DAG to Lute like this:

- checkable node -> loop
- linear edge -> list order
- fan-in -> parent loop plus parent integration `done_when`
- fan-out -> ordered sibling loops by default; `parallel: true` only for
  direct siblings with disjoint files/resources
- condition -> shell logic inside `done_when`
- uncheckable/advisory node -> text in the parent's `task`

The final `lute.proposed.yaml` must still contain only Lute-native YAML -
never `depends_on`, `dag`, `nodes`, `edges`, Mermaid, or Markdown plans. If
`--keep-dag` is requested, you must also write `lute.plan.yaml` as a review
artifact; otherwise do not write a separate DAG artifact unless explicitly
asked. `lute.proposed.yaml` remains the only runtime contract.

## 3. The decomposition rule (the heart of this skill)

**One loop per independently verifiable milestone. Decompose along verifiability boundaries, never along activity steps.**

Humans decompose by activity: "research, then implement, then test, then polish." Reject that instinct. Ask instead: *what statements about the world must become true, and in what order?* Each such statement is a loop; its exam is the statement made executable.

The fold-it-in test: if you cannot name a milestone's exam, it is not a loop - it is a vibe. Fold it into the parent's `task` as instructions. ("Understand the codebase" is not a loop. "SPEC.md exists and a judge confirms it covers every invoice state" is.)

Calibration: a typical goal yields 3–7 loops. Nest only when a milestone has its own checkable sub-milestones. A loop you expect to take more than ~15 runs is too big - split it. The root's `done_when` must restate the *entire* goal mechanically (build + test + the headline metric), because the root takes its own exam last.

## 4. Writing exams (done_when)

**Compile English into exit codes.** The user's fuzzy intent is source code; your job is to choose the strongest executable check that truly captures the goal, in this order:

```
1. existence/absence    ! grep -r "moment(" src/
2. build/types          tsc --noEmit ; cargo check
3. tests                npm test ; pytest -q
4. thresholds/replay    lighthouse --min 95 ; size-limit ; diff golden/
5. judge: <rubric>      last resort, for taste/tone/coverage (§5)
```

Rules that make exams trustworthy:

- **Runnable now.** `lute lint` will execute every exam once before any work starts. An exam that errors (typo, missing tool) is invalid. Prefer tools already in the repo.
- **Terse output.** The exam's last 50 lines become the next prompt. Use failure-only reporters (`--reporter=dot`, `-q`, `2>&1 | tail`). A noisy exam is a worse teacher.
- **Measure the goal, not obedience.** Never write an exam the worker can satisfy by writing the exam's expected string. `done_when: "grep -q 'export const tax' src/tax.ts"` when the task says "add `export const tax`" is circular - the agent passes by typing, not by succeeding. Anchor exams on behavior (tests, builds, thresholds), on ground truth the worker doesn't author (golden files), or on counts/invariants ("test count >= current").
- **Protect what the task could edit.** When a milestone's exam lives in files the task itself can touch (tests, fixtures, check scripts, `package.json`, `Makefile`), list them under `protected:` on that loop, for example `["tests/**", "scripts/check-*.sh"]`. Protect `package.json` or `Makefile` only when they are trusted exam/control inputs and changing them is not part of the task; otherwise put the exam behind a separate protected check script. Lute quarantines attempted edits to those materials under `.lute/quarantine/<id>/`, restores the trusted copies before checking or committing, and tells the worker the quarantine id. Inspect with `lute quarantine` and `lute quarantine diff <id>`. Opt-in - pair `protected:` with any exam whose materials the task could edit; you decide what counts as the exam.
- Shell booleans are welcome and sufficient: `&&`, `||`, `!`. All condition logic lives here, never in the file structure.
- **The not-yet verdict.** A watcher's exam may answer *not yet* (exit 75,
  `EX_TEMPFAIL`): nothing is wrong, nothing is done. The runner re-asks every
  `check_every`, wakes no agent, and spends no run budget - only a real
  failure's output ever reaches a prompt. Use it when the exam watches the
  world (deploys, queues, inboxes) rather than the work, and always give such
  a loop a time budget as the limit. `lute lint` errors when a dry-run returns
  75 without a time budget, and `lute run` escalates immediately rather than
  waiting forever:

```yaml
loop: deploy-quiet
task: Investigate and fix whatever broke the deploy.
done_when: "./checks/quiet.sh"   # 0 quiet 24h · 75 waiting · 1 alerts found
check_every: 30m
budget: 48h
```

## 5. judge: rules

Use a judge only when executable checks cannot capture the residue (quality, tone, coverage-by-meaning). Then, all four guardrails, always:

1. The judge model must differ from the worker agent of that loop (lint warns; you should never rely on the warning).
2. A rubric, not a vibe. Bad: `judge: changelog is good`. Good: `judge: for EACH user-facing change in the diff there is an entry - cite the hunk and the line. No marketing language. Under 200 words.`
3. Itemized citations required (the rubric should demand them, as above) so the verdict is auditable.
4. Add `confirm: 2` - judges are flaky checks by nature.

A loop that closes on a judge is closed-ish. If it sits near anything irreversible, say so in your output and recommend a human review of that loop's result.

## 6. Budgets and knobs

**Gate any loop that immediately precedes an irreversible verb** (deploy, publish, send, migrate): the gate guards readiness, the next loop performs the act - list order does the rest. A gated manifest must also configure `cage:` in `.lute/config.yaml`; otherwise lint and run refuse it because an uncaged agent can read the answer-auth key and forge approval.

**Mark a parent `parallel: true` only when its children touch disjoint files/resources** (independent services, separate modules) and each takes real time. They run at once in separate worktrees and merge back, so an overlap is a real merge conflict that escalates, not auto-resolves. A DAG with independent-looking nodes is not enough:

- children must be direct siblings
- children must share no required files/resources
- the parent must have its own `done_when` integration check for the merged result
- children's checks must avoid port/scratch collisions; use `LUTE_SLOT` when needed

After parallel children merge, Lute re-runs each direct child `done_when` once
against the merged tree. If a child invariant no longer passes, that failure
becomes the parent loop's next repair prompt. This catches sibling interference,
but it does not replace the parent integration exam: the parent still needs to
check behavior no child owns alone.

Every loop gets a budget. Sizing defaults: mechanical edits 3 runs; type/test fixing 10–15; open-ended work 20 runs plus a time cap. The root always carries a time cap as the global fuse. Add `confirm: 2` to any exam known or likely to be flaky (integration suites, anything with timing, every judge).

## 7. Anti-patterns (reject these in your own drafts and in reviews)

Vague exams ("works correctly", "is done"). Placeholder exams (`done_when: "true"`; lint warns). Circular exams (§4). Unprotected exam materials the worker can edit. Activity decomposition ("research" / "implement" as loops). Logic smuggling (any urge for if/else/depends_on - use order, check-before-work, shell booleans, or move the branching into the task where the agent's intelligence handles it). DAG leakage in final YAML (`depends_on:`, `dag:`, `nodes:`, `edges:`, Mermaid, or Markdown instead of loops). Treating `lute.plan.yaml` as runtime input. Parallelizing conceptual dependencies without disjoint files/resources and a parent integration exam. Watcher loops using exit 75 without time budgets (lint errors when observed). Loops without budgets. Oversized loops (>15 expected runs). Judges for greppable facts or without `confirm: 2` (lint warns). Noisy exams. A root whose exam is weaker than the user's stated goal.

## 8. Worked examples

Goal: "make this repo pass strict TypeScript"

```yaml
loop: strict-ts
agent: claude -p
budget: 24h
done_when: "node -e 'process.exit(require(\"./tsconfig.json\").compilerOptions.strict?0:1)' && tsc --noEmit && npm test"
loops:
  - loop: enable-strict          # if-trick: skipped when already strict
    task: Set "strict": true in tsconfig.json.
    done_when: "node -e 'process.exit(require(\"./tsconfig.json\").compilerOptions.strict?0:1)'"
    budget: 2 runs
  - loop: fix-errors
    task: Fix all TS errors. Do not loosen tsconfig or add ts-ignore.
    done_when: "node -e 'process.exit(require(\"./tsconfig.json\").compilerOptions.strict?0:1)' && tsc --noEmit && ! grep -rn 'ts-ignore' src/"
    budget: 15 runs
```

Goal: "get moment.js out of the app"

```yaml
loop: kill-moment
agent: claude -p
budget: 48h
done_when: "! grep -r 'moment' package.json && npm test && size-limit"
loops:
  - loop: migrate-imports
    task: Replace all moment usage with date-fns, file by file.
    done_when: "! grep -rn \"from 'moment'\" src/"
    budget: 8 runs
  - loop: tests-green
    task: Repair tests broken by the migration. Never delete or skip a test.
    done_when: "npm test"
    confirm: 2
    budget: 10 runs
  - loop: changelog
    agent: codex
    task: Document the migration in CHANGELOG.md from the diff.
    # Assumes .lute/config.yaml sets judge to a command/model different from this worker.
    done_when: "judge: every user-visible behavior change in the diff has
                an entry - cite the hunk. No marketing language."
    confirm: 2
    budget: 3 runs
```

Goal: "add CSV and JSON export to the reports page against existing export tests" (`lute plan --dag`)

Planning DAG, not final YAML:
`api-contract -> {csv-export, json-export} -> report-integration`

```yaml
loop: report-exports
agent: claude -p
budget: 48h
done_when: "./scripts/check-reports.sh all"
loops:
  - loop: api-contract
    task: Add or update the shared export contract used by CSV and JSON reports.
    done_when: "./scripts/check-reports.sh contract"
    protected: ["tests/reports/**", "scripts/check-reports.sh"]
    budget: 5 runs
  - loop: export-formats
    parallel: true
    task: After the CSV and JSON branches merge, repair format-level integration issues without changing the shared contract or protected tests.
    done_when: "./scripts/check-reports.sh csv json"
    budget: 20 runs
    loops:
      - loop: csv-export
        task: Implement CSV export against the shared contract, confined to src/reports/export/csv/**.
        done_when: "./scripts/check-reports.sh csv"
        protected: ["tests/reports/**", "scripts/check-reports.sh"]
        budget: 8 runs
      - loop: json-export
        task: Implement JSON export against the shared contract, confined to src/reports/export/json/**.
        done_when: "./scripts/check-reports.sh json"
        protected: ["tests/reports/**", "scripts/check-reports.sh"]
        budget: 8 runs
  - loop: report-integration
    task: Wire both exports into the reports page and repair integration failures.
    done_when: "./scripts/check-reports.sh all"
    protected: ["tests/reports/**", "scripts/check-reports.sh"]
    confirm: 2
    budget: 10 runs
```

Note what makes these correct: order is the plan; the roots restate the whole goal; `enable-strict` uses check-before-work as a conditional; the DAG example compiles edges into list order, direct sibling parallelism, and a parent integration check; exams forbid the cheats the task tempts (`ts-ignore`, deleted tests); protected materials cover test-based exams; the judge has a rubric, citations, a different engine, and `confirm: 2`.

## 9. Pre-flight checklist (run before emitting or accepting any luteloops file)

Every exam is executable right now in this repo. Every loop is independently verifiable and own-able by one agent run sequence. Order encodes every dependency. The root exam covers the entire stated goal. Every loop has a budget; the root has a global cap. No logic in the structure; no circular exams; judges only where executable checks cannot capture the goal, with all four guardrails. No DAG-only keys appear in `lute.proposed.yaml`. Every `parallel: true` parent has direct independent children plus an integration check. Exam materials the task could edit are protected. Watchers using exit 75 have time budgets. Irreversible next steps are gated. Run `lute lint` yourself and review warnings before calling a local draft done; if the runner or repo context is unavailable, state that lint was not run and give the exact command.
