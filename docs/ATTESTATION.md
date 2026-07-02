# Attestation — the verdict as an artifact

A closed run leaves a branch, logs, and events. `lute attest` folds the
verdict into one machine-readable statement a third party — a merge gate, a
reviewer, an auditor — can check without trusting the machine that ran the
loop's *narrative*, only its git objects:

```sh
lute attest > lute-attestation.json
lute attest --verify lute-attestation.json
# verified: exam 9b1f0c2a44de passed at 7d3e11a09c42 (react-major, 6 run(s))
```

## What a statement binds

The output is an [in-toto Statement](https://in-toto.io/Statement/v1) whose
subject is the run's branch at its closing commit, and whose predicate holds
only facts that re-derive from immutable objects:

| fact | where it re-derives from |
|---|---|
| `trusted_base` | the first non-`lute(` commit walking the branch first-parent — the base the runner pinned |
| `exam_sha256` | sha256 of the manifest **as committed at that base** — the exam the runner actually administered, not the file on disk today |
| subject `gitCommit` | the branch head that closed |
| `runs` | the count of `lute(...): run N` commits on `base..head` — the same un-rewritable floor budget accounting trusts |
| `events` | a rolling sha256 chain over `.lute/events.jsonl`, line by line — local evidence, tamper-evident against the statement |
| `outcome`, `done_when`, `agent_secs`, `closed_ts` | the event stream's replay (bound by the chain) |

`--verify` recomputes each fact and refuses on the first mismatch, naming it:
a head this repository does not contain, a base that is not an ancestor, an
exam hash that does not match the pinned manifest, a run count that moved, an
events file edited, reordered, or shorter than attested.

## What it guarantees — and deliberately does not

**It guarantees** the statement is internally consistent with this
repository's immutable objects: *this* exam text, pinned at *this* base,
closed at *this* head after *these* attempts, with an event narrative that
has not changed since attestation. Nothing in the statement rests on a
mutable file alone.

**It does not guarantee** the exam would pass if re-run today — the world
moves; re-administering against the merged tree is `lute land`'s job, and
re-running `done_when` yourself is one command. It does not guarantee the
exam was *worth passing* — exam quality stays the author's job, as
everywhere in lute. And it does not, by itself, prove **who** ran the loop:
the statement is unsigned bytes.

## Signing — the identity is CI's, on purpose

Lute proves internal consistency and git anchoring; *who ran it* is proven by
whoever signs the artifact, and CI already has a machine identity for exactly
this. From a GitHub workflow (the `contrib/action` composite writes
`lute-attestation.json` on every closed run):

```yaml
- uses: actions/attest-build-provenance@v1
  with:
    subject-path: lute-attestation.json
```

That wraps the statement in a Sigstore-signed provenance record tied to the
workflow's OIDC identity — verifiable with `gh attestation verify`. The
runner itself never touches keys: signing is a trust decision, and lute keeps
those with the operator (the same reason the cage owns its own network
policy).

## Reading one by hand

```sh
python3 -c "import json,sys; p=json.load(open('lute-attestation.json'))['predicate']; \
print(p['root'], p['outcome'], p['runs'], 'runs'); print('exam:', p['done_when'])"
git show $(python3 -c "import json; print(json.load(open('lute-attestation.json'))['predicate']['trusted_base'])")":lute.yaml"
```

The second command prints the exam exactly as the runner administered it —
the file your reviewer should read before trusting any green.
