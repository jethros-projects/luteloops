"""Parallel child worktrees."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess

from . import processes
from .domain import LoopSpec
from .errors import Blocked, Gated, InternalError
from .runner import self_argv


def child_branch(runner, child: LoopSpec) -> str:
    return f"lute/{runner.ctx.root_id}__{child.id}"


def worktree_dir(runner, child: LoopSpec) -> str:
    return os.path.join(runner.ctx.paths.worktrees, f"{runner.ctx.root_id}__{child.id}")


def pid_file(runner, child: LoopSpec) -> str:
    return worktree_dir(runner, child) + ".pid"


def ensure_worktree(runner, child: LoopSpec, head: str) -> str:
    worktree, branch = worktree_dir(runner, child), child_branch(runner, child)
    runner.git.worktree_prune()
    if os.path.isfile(os.path.join(worktree, ".git")):
        return worktree
    if os.path.exists(worktree):
        shutil.rmtree(worktree, ignore_errors=True)
    if runner.git.branch_exists(branch):
        runner.git.worktree_add(worktree, branch)
    else:
        runner.git.worktree_add(worktree, "-b", branch, head)
    return worktree


def reap_orphans(runner, children: list[LoopSpec]) -> None:
    for child in children:
        try:
            pid_text, path = (open(pid_file(runner, child)).read().split("\n", 1) + [""])[:2]
            pid = int(pid_text)
        except (ValueError, OSError):
            continue
        if pid and processes.pid_alive(pid):
            cmd = processes.command_line(pid)
            if path and path in cmd and f"run {child.id} --plain" in cmd:
                try:
                    os.killpg(pid, 9)
                except Exception:
                    try:
                        os.kill(pid, 9)
                    except Exception:
                        pass


def drop_worktree(runner, child: LoopSpec) -> None:
    worktree, branch = worktree_dir(runner, child), child_branch(runner, child)
    if os.path.exists(worktree):
        runner.git.worktree_remove(worktree)
    runner.git.delete_branch(branch)
    try:
        os.remove(pid_file(runner, child))
    except OSError:
        pass


def spawn_child(runner, child: LoopSpec, worktree: str, slot: int):
    trusted_base = runner.ctx.trusted_base or runner.git.branch_base()
    env = dict(os.environ, LUTE_STATE_DIR=runner.ctx.paths.state, LUTE_SLOT=str(slot), LUTE_TRUSTED_BASE=trusted_base)
    cmd = [*self_argv(), "run", str(child.id), "--plain", "--file", runner.ctx.manifest_path]
    marker = cmd[2] if len(cmd) > 2 and cmd[1] == "-m" else cmd[1]
    runner.store.ensure_layout()
    proc = processes.spawn_detached(cmd, cwd=worktree, env=env, stdout_path=runner.ctx.paths.runner_log)
    runner.store.safe_write_regular(pid_file(runner, child), f"{proc.pid}\n{marker}")
    return proc


def stop_children(procs) -> None:
    """Tear down child runners we own. We hold their handles, so we ask each to
    stop (it reaps its own agent), then reap it ourselves — concurrently, and
    with no zombie left behind. A child that won't exit in time is forced."""
    for _, proc in procs:
        try:
            proc.send_signal(signal.SIGINT)
        except (OSError, ProcessLookupError):
            pass
    for _, proc in procs:
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            processes.stop_group(proc.pid)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass


def join_children(procs):
    try:
        return [(child, proc.wait()) for child, proc in procs]
    except BaseException:
        stop_children(procs)
        raise


def merge_all(runner, parent: LoopSpec, results) -> None:
    merged: list[str] = []
    baseline = runner.protection.baseline(parent)
    for child, _ in results:
        trusted_changes = runner.protection.changed_paths_at_ref(baseline, child_branch(runner, child))
        if trusted_changes:
            trusted_merge_escalate(runner, parent, child, trusted_changes)
        result = runner.git.merge(
            "--no-edit",
            "-m",
            f"lute({parent.id}): merge {child.id}",
            child_branch(runner, child),
            check=False,
        )
        if result.returncode:
            conflicts = runner.git.text("diff", "--name-only", "--diff-filter=U").split()
            runner.git.ok("merge", "--abort")
            merge_escalate(runner, parent, child, merged, conflicts)
        merged.append(str(child.id))


def trusted_merge_escalate(runner, parent: LoopSpec, child: LoopSpec, paths: list[str]) -> None:
    lid = str(parent.id)
    text = (
        f"BLOCKED: parallel child {child.id} modified trusted exam/control material\n"
        f"Quarantined by child or refused before merge: {', '.join(paths)}\n"
        f"The parent branch was left clean; inspect child quarantine records with `lute quarantine list`.\n"
        f"Resolve: make exam changes explicitly as reviewed work, or remove them from the child branch and re-run.\n"
    )
    runner.cards.raise_block(lid, text, f"lute({lid}): trusted merge blocked", trusted=paths)


def merge_escalate(runner, parent: LoopSpec, child: LoopSpec, merged: list[str], conflicts: list[str]) -> None:
    lid = str(parent.id)
    text = (
        f"BLOCKED: merge conflict integrating parallel children of {lid}\n"
        f"Conflicting file(s): {', '.join(conflicts) or '(unknown)'}\n"
        f"Loops involved: {child.id} vs already-merged {', '.join(merged) or '(parent base)'}\n"
        f"The parent branch was left clean (merge aborted); no broken commit exists.\n"
        f"Resolve: make the children touch disjoint regions (or merge by hand), then re-run.\n"
    )
    runner.cards.raise_block(lid, text, f"lute({lid}): merge conflict", conflict=conflicts)


def run_parallel(runner, parent: LoopSpec, agents_by_loop: dict[str, str | None]) -> None:
    head = runner.git.head()
    pending = [
        (index, child)
        for index, child in enumerate(parent.children, 1)
        if child.children or child.gate or runner.checks.run(child, lenient=True).verdict.value != "pass"
    ]
    if pending:
        pending_children = [child for _, child in pending]
        reap_orphans(runner, pending_children)
        procs = [(child, spawn_child(runner, child, ensure_worktree(runner, child, head), slot)) for slot, child in pending]
        runner.events.emit("parallel", str(parent.id), children=[str(child.id) for child, _ in procs])
        results = join_children(procs)
        codes = [code for _, code in results if code != 0]
        if codes:
            if 3 in codes:
                raise Blocked()
            if 4 in codes:
                raise Gated()
            raise InternalError(f"parallel child exited {max(codes)}")
        merge_all(runner, parent, results)
    for child in parent.children:
        drop_worktree(runner, child)
    runner.git.worktree_prune()
