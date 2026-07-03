"""Attestation: bind a closed run's verdict to coordinates git cannot forge.

`lute attest` is a sibling of the primitive, like the judge: the runner never
calls it, and the grammar gains nothing. It emits an in-toto Statement whose
facts all re-derive from immutable objects — the trusted base and head SHAs,
the sha256 of the manifest as committed at that base (the exam the runner
actually administered), the run-commit count on the base..head segment, and a
hash chain over the event stream as local evidence. `--verify` re-derives each
fact and refuses on the first mismatch, naming it. The one thing a statement
deliberately does not claim: that the exam would pass if re-run today —
re-administering is `lute land`'s job. The full scope, and how a CI identity
turns this into a signed artifact, is `docs/ATTESTATION.md`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

from .cards import CardService
from .context import AppContext
from .domain import LoopSpec
from .errors import PreconditionError, UsageError
from .events import replay_events
from .git_repo import GitRepo
from .watch import state_json

STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://github.com/jethros-projects/luteloops/attestation/v0"
CHAIN_SEED = b"lute-attest-v0"
RUN_SUBJECT_RE = re.compile(r"^lute\([^)]+\): run \d+$")


def chain_events(path: str, limit: int | None = None) -> tuple[str, int]:
    """Rolling sha256 over the first `limit` event lines (all when None).
    Any edit, reorder, or removal inside the covered prefix changes the tail
    digest; a file shorter than `limit` reports its true line count."""
    digest = hashlib.sha256(CHAIN_SEED).digest()
    lines = 0
    try:
        f = open(path, "rb")
    except OSError as exc:
        raise PreconditionError(f"cannot read events at {path}: {exc}") from exc
    with f:
        for line in f:
            if limit is not None and lines == limit:
                break
            digest = hashlib.sha256(digest + line).digest()
            lines += 1
    return digest.hex(), lines


def run_commits(git: GitRepo, base: str, head: str) -> int:
    subjects = git.text("log", "--format=%s", f"{base}..{head}").splitlines()
    return sum(1 for subject in subjects if RUN_SUBJECT_RE.match(subject))


def statement(ctx: AppContext, git: GitRepo, root: LoopSpec, cards: CardService) -> dict:
    branch = f"lute/{root.id}"
    if not git.branch_exists(branch):
        raise PreconditionError(f"no {branch} branch here; there is no run to attest")
    state = replay_events(ctx.paths.events)
    outcome = state_json(root, state, cards)["outcome"]
    if outcome != "closed":
        raise PreconditionError(f"only a closed run attests; this one is {outcome}")
    head = git.text("rev-parse", branch).strip()
    base = git.branch_base(ref=branch)
    manifest = os.path.relpath(ctx.manifest_path, ctx.repo_root)
    pinned = git.show_bytes(f"{base}:{manifest}")
    if pinned is None:
        raise PreconditionError(
            f"{manifest} is not committed at the trusted base {base[:12]}; a fileless or uncommitted exam has nothing to bind"
        )
    chain, lines = chain_events(ctx.paths.events)
    return {
        "_type": STATEMENT_TYPE,
        "subject": [{"name": branch, "digest": {"gitCommit": head}}],
        "predicateType": PREDICATE_TYPE,
        "predicate": {
            "root": str(root.id),
            "branch": branch,
            "outcome": "closed",
            "trusted_base": base,
            "manifest": manifest,
            "exam_sha256": hashlib.sha256(pinned).hexdigest(),
            "done_when": root.done_when.command,
            "runs": run_commits(git, base, head),
            "agent_secs": round(sum(ls.get("secs", 0.0) for ls in state["loops"].values()), 1),
            "closed_ts": state["last"],
            "events": {"lines": lines, "chain_sha256": chain},
        },
    }


def verify(ctx: AppContext, git: GitRepo, path: str) -> str:
    """Re-derive every attested fact; the first mismatch names itself."""
    try:
        with open(path, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError) as exc:
        raise UsageError(f"cannot read statement {path}: {exc}") from exc
    if not isinstance(s, dict) or s.get("_type") != STATEMENT_TYPE or s.get("predicateType") != PREDICATE_TYPE:
        raise PreconditionError(f"{path} is not a lute attestation statement")
    p = s.get("predicate") or {}
    subject = (s.get("subject") or [{}])[0]
    head = (subject.get("digest") or {}).get("gitCommit") or ""
    base = p.get("trusted_base") or ""
    if not git.ok("cat-file", "-e", f"{head}^{{commit}}"):
        raise PreconditionError(f"attested head {head[:12]} is not in this repository")
    if not git.ok("cat-file", "-e", f"{base}^{{commit}}"):
        raise PreconditionError(f"attested base {base[:12]} is not in this repository")
    if not git.ok("merge-base", "--is-ancestor", base, head):
        raise PreconditionError("attested base is not an ancestor of the attested head")
    pinned = git.show_bytes(f"{base}:{p.get('manifest', '')}")
    if pinned is None:
        raise PreconditionError(f"manifest {p.get('manifest')} is not committed at the attested base")
    if hashlib.sha256(pinned).hexdigest() != p.get("exam_sha256"):
        raise PreconditionError("exam hash does not match the manifest at the attested base")
    runs = run_commits(git, base, head)
    if runs != p.get("runs"):
        raise PreconditionError(f"run-commit count on base..head is {runs}; the statement says {p.get('runs')}")
    want = p.get("events") or {}
    chain, lines = chain_events(ctx.paths.events, limit=want.get("lines"))
    if lines < (want.get("lines") or 0):
        raise PreconditionError(f"events file is shorter than attested ({lines} < {want.get('lines')} lines)")
    if chain != want.get("chain_sha256"):
        raise PreconditionError("events do not match the attested chain")
    return f"verified: exam {p.get('exam_sha256', '')[:12]} passed at {head[:12]} ({p.get('root')}, {runs} run(s))"
