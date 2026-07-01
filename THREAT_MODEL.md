# Threat model

Lute has two trust bases, and only one holds without a cage. This page states
which is which, so a reader learns Lute's boundaries in five minutes instead of
by reading the runner. It is the contractual form of honesty the README carries
in prose and `BLOCKERS.md` carries as engineering notes.

**Intent, in one sentence:** the cage isolates the *filesystem* and *host
secrets*; network egress is the operator's to seal.

## The two trust bases

| | holds **uncaged** (agent runs as you) | requires a **real cage** |
|---|---|---|
| **Exam-pass integrity** — a close means the pinned exam passed | ✅ against an adversarial, daemon-free agent | (already holds without one) |
| **Protected exams** — the agent can't grade itself by editing the exam | ✅ restored from the pinned base every check | (already holds without one) |
| **Ledger tamper-resistance** — deleting/rewriting the ledger can't buy budget | ✅ budget re-derived from committed history | (already holds without one) |
| **Budget *reset*** — an answered card refreshes a budget only when *you* answered | ❌ agent can read the answer-auth key and forge the token | ✅ key isn't in the container |
| **`gate: human`** — a loop pauses for real human approval | ❌ same forgeable token; **refused before work starts** | ✅ the trust anchor |
| **Secret isolation** — `~/.ssh`, env, `~/.lute/keys` are unreachable | ❌ agent runs as you | ✅ absent from the container |

The asymmetry is deliberate. The core guarantee — *"a pass means the exam
passed"* — needs no daemon and no container. The guarantees that hinge on a
**secret** (budget reset, human approval) need the container to keep that secret
from an agent that otherwise runs as you.

## What each mechanism guarantees, and under which assumption

**Exam integrity (uncaged).** The verdict is the runner re-running `done_when`
and reading its exit code (`0`/`75`/else); the agent's own exit code is *logged,
never branched on* (`runner.run_agent_iteration`, `checks.run_shell_check`). The
`LoopSpec` that carries the exam is parsed once, before the agent runs, and lives
in the runner's memory where the agent can't reach it. So a pass is a fact about
the pinned exam, not the agent's word — this is why Lute "can't lie about
doneness," and it holds against an adversarial agent with no cage.

**Protected exams / quarantine (uncaged).** `protected:` globs plus the trusted
control files (the active manifest and `.lute/config.yaml`) are re-hashed against
their committed originals at the pinned base before and after every check and
after every run. An edit to a watched file is saved under `.lute/quarantine/`,
the trusted copy is restored, and the attempt is left out of the run commit
(`runner.enforce_quarantine`, `protection.Protection`). The agent cannot pass by
rewriting the exam.

**Ledger / budget accounting.** Budget is re-derived from *committed run history*
and an authenticated view of the ledger, never from agent-written files
(`ledger.budget_spent`). Deleting, truncating, symlinking, or rewriting
`.lute/ledger.jsonl` cannot buy budget: the runner restores the trusted snapshot
after each run, the committed run-commit count is a floor, and forged `run`
lines are bounded by that floor. This tamper-resistance holds **uncaged**. What
does *not* hold uncaged is budget **reset**: an `answer` line only refreshes a
budget if it carries a valid HMAC over `(loop_id, nonce)` keyed by the secret at
`~/.lute/keys/`. An uncaged agent runs as you and can read that key, so it can
mint a valid answer and refresh its own budget. Under a cage the key is absent,
so budget reset is a real boundary.

**`gate: human` (caged only, enforced).** A human gate seals with the same
authenticated answer. Because an uncaged agent could forge it, a manifest with
any `gate: human` loop is **refused before work starts** unless a cage is
configured (`runner.require_human_authority_cage`; `lint` reports it as an
error). Human approval is the one place Lute turns a warning into a hard
precondition.

**`cage:` (filesystem + host-secret isolation).** With `cage:` set, every
model-facing command runs in a container that sees only your repo (read-write at
`/work`) and paths you name in `cage_mounts` (read-only). `~/.ssh`, your
environment, and `~/.lute/keys` simply aren't there — *secrets policy is
absence*. `done_when` checks stay on the host (they're yours); judges run in an
empty working directory over the stdin diff only.

## Explicitly out of scope

These are named boundaries, not oversights. Each already lives in `BLOCKERS.md`
as an engineering note; here they are the user-facing contract.

- **Network egress.** The cage isolates the filesystem and host secrets, not the
  network — the built-in `docker` template keeps egress on so model CLIs can
  reach their APIs. If egress isolation matters, supply a custom template with
  the policy you want (e.g. `--network none`). Sealing must never be the default:
  it would brick the very LLM agents the cage exists to run.
- **Container orphan on `SIGKILL`.** `lute stop` reaps the host-side `docker run`
  client it owns; a `SIGINT`-ignoring container can detach. Reaching inside needs
  a deterministic `--name` + `docker stop`, which belongs in the operator's cage
  template, not the runner.
- **`setsid()` daemonization.** An uncaged agent is host code and can deliberately
  daemonize out of Lute's process group. No host-side runner logic contains that;
  a real cage does.
- **Exam material inside a submodule.** A `git submodule` under a `protected:` glob
  is policed as a boundary: quarantine flags it (and restores the pinned mount) when
  it is deleted, replaced with plain files, or moved to a different commit. What it
  cannot police is a *dirty working tree of a checkout still at the recorded commit* —
  an untracked file the agent adds inside a genuine submodule is that other
  repository's business, not a superproject blob. A `done_when` that reads such a file
  is trusting content Lute does not pin; keep exam material in the superproject.
- **Cost / token limits.** Budget is bounded by `runs` and `secs` proxies only. A
  real spend cap belongs to an agent-reported hook, never to the runner guessing
  at dollars.

## The one sentence a skeptic should leave with

An uncaged Lute run tells the truth about *doneness* against an adversarial
agent; it does not *contain* that agent. Containment — keeping host secrets, the
answer-auth key, and human approval away from the code you're running — is what
the cage is for.
