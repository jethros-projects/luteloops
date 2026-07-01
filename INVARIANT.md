# The invariant

Lute makes exactly one safety claim. Every other mechanism serves it, and every
adversarial notch in the suite defends some clause of it:

> **The builder cannot author its own verdict.**

The agent writes code; it does not get to say whether the code is done. That
sentence is only trustworthy if four things hold. Each is a deliberate design
choice, and each is pinned by a notch in `test.sh` so it cannot regress in
silence. A reviewer auditing Lute's safety should read those notches first.

## 1. The verdict is computed from a spec the agent cannot reach

`done_when` is parsed once, before the agent runs, into an in-memory `LoopSpec`
(`schema.load` → `domain.LoopSpec`). The agent edits the working tree; it never
touches the runner's memory, so the exam the runner administers is the one the
author wrote, not one the agent rewrote. The manifest and `.lute/config.yaml`
are *trusted control inputs*: an on-disk edit to them is quarantined and the
trusted copy restored before the next check.

*Enforced by:* **t8** (the check executes in the runner, never inside an agent
process) and **t44** (trusted control/exam edits are quarantined and restored).

## 2. Exam materials are restored from a pinned base before every check

`protected:` globs plus the control files are re-hashed against their committed
originals at the pinned `trusted_base` before and after each check and after
each run. A modified, deleted, or newly-added watched file is set aside under
`.lute/quarantine/` and the trusted copy restored — so the exam an agent sees is
always the pinned one, even across runs and even if the tamper was committed.

*Enforced by:* **t37** (a tampered exam stays caught on re-run), **t38** (a
committed grader/manifest/exam tamper can't buy a pass on a later run), and
**t17** (a bought pass is voided by the protected guard under a cage).

## 3. The agent's exit code is logged, never branched on

The runner records the agent's duration and exit code as evidence (`agent_end`),
then ignores them for the decision: the only verdict is the runner re-running
`done_when` and reading *its* exit code (`0` / `75` / else). A wrapper that lies
about success changes nothing.

*Enforced by:* **t8** (the verdict comes from the runner's own check run) and
**t1** (a loop closes when the exam passes, regardless of the agent's exit).

## 4. Budget is re-derived from committed history, never from agent files

Spend is recomputed each iteration from the committed run-commit count and an
*authenticated* view of the ledger (`ledger.budget_spent`). Deleting,
truncating, symlinking, or rewriting `.lute/ledger.jsonl` cannot buy budget: the
runner restores the trusted snapshot, the committed run count is a floor, and a
budget *reset* counts only `answer` lines carrying a valid HMAC over
`(loop_id, nonce)` — a token a ledger edit alone cannot forge (an uncaged agent
can read the key and forge it; see `THREAT_MODEL.md`).

*Enforced by:* **t42** (time budgets survive truncation, rewrites, forged runs,
answer replay) and **t39** (forged `answer` events can't refresh budget; a
genuine one still does).

---

If any of these four clauses fails, "done means done" fails with it. They are
the load-bearing notches. The two parsers that turn untrusted text into these
trusted decisions — `schema.load` (the manifest) and the `ledger` accounting
(the JSONL) — carry a property/fuzz layer in `tests/test_lute_core.py` asserting
they never crash and never misclassify malformed input into a pass or a refund.
