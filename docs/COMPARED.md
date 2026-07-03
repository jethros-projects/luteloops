# Compared, honestly

Lute is one idea — **the builder cannot author its own verdict** — wrapped in
the smallest runner that holds it. Most tools nearby share a surface with Lute
but not that idea. This page says exactly where each one is stronger than Lute
and where Lute is stronger, by mechanism rather than by marketing. Claims about
other tools describe their documented behavior as of mid-2026; they evolve —
check their docs, not this page, for what they do today.

## vs. a bare `while ! check; do agent; done`

The thirty-line loop is real: `contrib/lute.sh` is one, and it passes the two
kernel notches of Lute's own suite. If all you need is "retry until green" on
your own machine against an agent you trust, write the bash loop and enjoy it.

What the bash loop cannot say is *why you should believe the green*. The agent
runs as you, so it can edit the failing test, weaken the check command, wipe
the evidence, or daemonize past the loop. Lute's runner re-administers the exam
itself, restores `protected:` exam materials from a pinned base before every
check, quarantines the tampered edit for inspection (`lute quarantine diff`),
re-derives budgets from committed history a wiped ledger cannot rewind, commits
every attempt, and escalates to an `INBOX/` card instead of looping forever.
None of that fits in thirty lines; all of it is what "the exam passed" means.

- **Bash loop is better at:** zero install, zero concepts, infinite flexibility.
- **Lute is better at:** being unable to lie to you; nesting; budgets; escalation;
  parallel worktrees; a transcript you can audit afterwards.

## vs. ralph-style loops (run the agent all night in a loop)

The popular overnight-loop scripts and plugins share Lute's spirit — fresh
iterations, persistent notes, let the model grind. The load-bearing difference
is the termination condition: those loops typically stop when the *agent emits
a completion marker* (a promise string in its output). The judge and the
defendant are the same process. An agent that is confidently wrong — or that
learned the marker ends the loop — stops the loop without finishing the work.
Even the loop pattern's own popularizers say it degenerates without real test
backpressure.

Lute never reads the agent's output for a verdict. The loop ends when the
runner re-runs `done_when` and the exit code says so, and the exam's materials
are restorable from a pinned base the agent cannot rewrite.

- **Ralph-style loops are better at:** zero setup inside your existing agent
  CLI; community momentum; no YAML.
- **Lute is better at:** a finish line the agent cannot declare; budgets that
  survive tampering; cards instead of infinite loops; per-attempt forensics.

## vs. Aider (`--auto-test`)

Aider had runner-run tests early: after each edit it can run your test command
and feed failures back. Within a pairing session, that is the same honest shape
as Lute's check-work-verify. Aider is also a much richer *editing* tool than
Lute will ever be — Lute does not edit anything.

The difference is the trust boundary and the session shape. Aider is an
interactive pair tool: you are present, and its protections (read-only files)
are prevention flags inside the same process, not an external runner that
restores tampered exams, accounts budgets against committed history, or
escalates to a card when you are not there. Lute is for the run you *leave*.

- **Aider is better at:** interactive work, repo-map-aware editing, being the
  agent. (Aider can be Lute's engine: it reads a message and edits the tree.)
- **Lute is better at:** unattended runs, tamper quarantine, nested goals,
  human gates, one contract across any engine.

## vs. Claude Code's own loops, hooks, and goal features

The vendor loop features are the same instinct moving into the platform: keep
iterating until a goal is met, with hooks around tool use. Two structural
differences remain. First, the goal evaluators are *judges of the transcript* —
they read what the agent did and decide whether it looks done, without
independently administering your exam. Second, everything (worker, evaluator,
hooks) runs inside one vendor's process, with your configuration files
writable by the agent under test. There is no quarantine-and-restore of exam
materials, and "done" is not pinned to a shell exit code you chose.

Lute is engine-neutral by contract: `claude -p`, `codex exec`, a shell script —
same runner, same exam, same forensics. When the vendors ship a true external
verifier, that is good for everyone; the neutral referee across engines is the
part a single vendor structurally cannot ship.

- **Claude Code is better at:** the agent itself; distribution; IDE and cloud
  surfaces; zero extra install.
- **Lute is better at:** a verdict computed outside the vendor process, exam
  tamper quarantine, engine neutrality, and an audit trail in plain files.

## vs. plain CI (GitHub Actions + branch protection)

CI is a real exam — that is why Lute's checks look like CI steps. If your
workflow is "agent opens a PR, CI gates the merge," you already have external
verification *at the merge*. Keep it; Lute does not replace it.

What CI does not give you is the *loop*. CI examines what the agent chose to
push, once, after the fact; nothing feeds the failure back, re-runs the agent,
counts the attempts, restores the test the agent deleted *before* the run
commits it, or stops the fifth identical failure from becoming a sixth. Lute
administers the exam inside the loop, owns the commits, and arrives at CI with
work that already passed — CI becomes the second, independent administration
of the same exam. They compose: see `docs/UPGRADE-BOT.md`.

- **CI is better at:** org-wide policy, required reviews, deploy pipelines,
  infrastructure you already trust.
- **Lute is better at:** the iterate-until-green middle, attempt accounting,
  tamper quarantine, and escalation cards while you are away.

## Which should I use?

- **Supervising interactively?** Use your agent CLI or Aider; add `lute once`
  when you want to walk away mid-task.
- **Leaving an agent unattended on real work?** Lute — that is the whole tool.
- **Trusting one vendor end-to-end and wanting zero setup?** The vendor's loop
  features; read what their evaluator actually checks first.
- **Only gating merges?** Plain CI is enough; Lute earns its keep when the
  agent should *fix* what CI finds, repeatedly, without you.

## The one-sentence version

Every alternative either trusts the agent's own account of doneness somewhere,
or verifies once without looping. Lute is the small runner that does neither:
work loops until an exam the worker cannot grade — and cannot rewrite — says
it is done.
