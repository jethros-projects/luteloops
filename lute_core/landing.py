"""Landing a lute branch back to its start branch."""

from __future__ import annotations

from .cards import CardService
from .checks import CheckRunner
from .domain import LoopSpec, Verdict
from .errors import Blocked, PreconditionError
from .events import EventBus
from .git_repo import GitRepo
from .runner import Runner


def land(
    runner: Runner,
    root: LoopSpec,
    target: str | None,
    cards: CardService,
    checks: CheckRunner,
    events: EventBus,
) -> None:
    root_id, branch = str(root.id), f"lute/{root.id}"
    git: GitRepo = runner.git
    if not git.branch_exists(branch):
        raise PreconditionError(f"no {branch} branch to land; run lute first")
    target = target or runner.run_origin(root_id)
    if not target:
        raise PreconditionError("can't tell which branch to land into; name it: lute land <branch>")
    if not git.branch_exists(target):
        raise PreconditionError(f"target branch {target!r} does not exist")
    if git.status_porcelain("-uno").strip():
        raise PreconditionError("working tree has uncommitted changes; commit or stash before landing")

    def block(reason: str) -> None:
        path = cards.path(root_id)
        runner.store.safe_write_regular(path, f"BLOCKED: lute land: {reason}\nResolve, then: lute land {target}\n")
        git.shared_text(runner.ctx.shared_root, "add", path)
        git.shared_text(runner.ctx.shared_root, "commit", "-q", "--allow-empty", "-m", f"lute({root_id}): land blocked")
        events.emit("escalated", root_id, card=f"INBOX/{root_id}.md")
        runner.agents.fire_halt(root_id, "blocked", path)
        raise Blocked(reason)

    runner.acquire_lock()
    try:
        git.text("checkout", "-q", target)
        pre = git.head()
        trusted_base = git.text("merge-base", target, branch).strip()
        runner.ctx.trusted_base = trusted_base
        baseline = runner.protection.baseline(root)
        branch_trusted_changes = runner.protection.changed_paths_at_ref(baseline, branch)
        if branch_trusted_changes:
            block(
                f"{branch} modifies trusted exam/control material and was not landed: "
                + ", ".join(branch_trusted_changes)
                + ". Inspect quarantines with: lute quarantine list"
            )
        merge = git.merge("--no-ff", "--no-edit", branch, check=False)
        if merge.returncode:
            git.ok("merge", "--abort")
            git.ok("reset", "-q", "--hard", pre)
            msg = (merge.stderr or merge.stdout).strip()
            last = msg.splitlines()[-1] if msg else "merge failed"
            block(f"landing {branch} into {target} could not complete; {target} left clean; resolve and re-run. git said: {last}")
        q = runner.enforce_quarantine(root, "land-precheck", baseline)
        if q:
            git.text("reset", "-q", "--hard", pre)
            block(f"trusted exam/control changes were quarantined during land as {q.qid}; {target} restored. Inspect: lute quarantine diff {q.qid}")
        verdict = checks.run(root).verdict
        q = runner.enforce_quarantine(root, "land-postcheck", baseline)
        if q:
            git.text("reset", "-q", "--hard", pre)
            block(f"root exam modified trusted material during land; quarantined as {q.qid}; {target} restored. Inspect: lute quarantine diff {q.qid}")
        if verdict != Verdict.PASS:
            git.text("reset", "-q", "--hard", pre)
            block(f"root exam `{root.done_when.command}` fails against the merged tree. NOT landed ({target} restored); the branches integrate badly; fix on {branch} and re-run")
    finally:
        runner.release_lock()
    print(f"✔ landed {branch} → {target}, root exam re-verified green  (tidy up: git branch -d {branch})")
