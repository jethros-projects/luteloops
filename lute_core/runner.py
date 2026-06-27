"""Core loop orchestration.

The runner reads like the product: children, protection, check, budget, card,
agent, ledger, commit, gate, close.  It uses `LoopSpec` directly and keeps all
runtime state in `AppContext` and services.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import time

from .agents import AgentRunner, human
from .budget import BudgetService
from .cards import CardService
from .checks import CheckRunner
from .config import AnswerAuthority, freeze_config
from .context import AppContext
from .domain import Gate, LoopSpec, Verdict
from .errors import Blocked, Gated, PreconditionError, UsageError
from .events import EventBus, now_iso
from .git_repo import GitRepo
from .ledger import append_entry, ledger_totals, read_entries, restore_if_changed, snapshot, total_runs
from . import processes
from .protection import Protection
from .state_store import StateStore


def entrypoint_path() -> str:
    import sys

    invoked = sys.argv[0] if sys.argv else ""
    resolved = shutil.which(invoked) if invoked and not os.path.dirname(invoked) else invoked
    if resolved and os.path.exists(resolved):
        return os.path.abspath(resolved)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "lute"))


def self_argv() -> list[str]:
    import sys

    entry = entrypoint_path()
    if os.path.basename(entry) == "cli.py" and os.path.basename(os.path.dirname(entry)) == "lute_core":
        return [sys.executable, "-m", "lute_core.cli"]
    return [sys.executable, entry]


def self_cmd() -> str:
    import shlex

    return " ".join(shlex.quote(part) for part in self_argv())


class Runner:
    def __init__(self, ctx: AppContext, git: GitRepo, store: StateStore | None = None):
        self.ctx = ctx
        self.git = git
        self.store = store or StateStore(ctx.paths)
        self.store.ensure_layout()
        self.events = EventBus(ctx, self.store)
        self.authority = AnswerAuthority(ctx)
        self.budget = BudgetService(self.git, self.ledger_entries, self.authority.token)
        self.agents = AgentRunner(ctx, self.store, ctx.repo_root, self_cmd)
        self.checks = CheckRunner(ctx, self.git, self.agents.cage_wrap)
        self.protection = Protection(ctx, self.git)
        self.cards = CardService(
            ctx,
            self.store,
            self.git,
            self.events,
            self.authority,
            self.ledger_entries,
            self.ledger_append,
            self.agents.fire_halt,
        )
        self._lock_registered = False
        self.quarantine_notes: dict[str, list[str]] = {}
        self.quarantine_paths: dict[str, set[str]] = {}

    def ledger_entries(self) -> list[dict]:
        return read_entries(self.ctx.paths.ledger)

    def ledger_append(self, obj: dict) -> None:
        append_entry(self.ctx.paths.state, self.ctx.paths.ledger, obj)

    def assign_agents(self, loop: LoopSpec, default: str | None, inherited: str | None = None) -> dict[str, str | None]:
        agent = loop.agent or inherited or default
        if loop.task is not None and not agent:
            raise UsageError(
                f"loop '{loop.id}' has a task but no agent: set agent:, pass --agent, "
                f"or set agent in {self.ctx.paths.config}"
            )
        agents = {str(loop.id): agent}
        for child in loop.children:
            agents.update(self.assign_agents(child, default, agent))
        return agents

    def run_toplevel(self, root: LoopSpec, agents_by_loop: dict[str, str | None]) -> None:
        self.acquire_lock()
        start = self.git.current_branch()
        if start.startswith("lute/"):
            start = self.run_origin(str(root.id)) or start
        self.ensure_branch(str(root.id))
        self.ctx.run_pre_untracked = self.git.untracked()
        self.ctx.trusted_base = os.environ.get("LUTE_TRUSTED_BASE") or self.git.branch_base()
        self.store.ensure_layout()
        self.store.ensure_capture_ignore()
        self.seal_ignore(str(root.id))
        freeze_config(self.ctx, self.git)
        self.events.emit("run_start", str(root.id), branch=f"lute/{root.id}", start=start)
        try:
            self.run_loop(root, agents_by_loop)
        finally:
            self.release_lock()
        self.events.emit("run_end", str(root.id), **({} if self.ctx.manifest_path else {"fileless": True}))

    def run_child(self, target: LoopSpec, agents_by_loop: dict[str, str | None]) -> None:
        self.store.ensure_layout()
        self.store.ensure_capture_ignore()
        self.git.clear_stale_locks()
        if self.git.status_porcelain("-uno").strip():
            self.git.text("reset", "-q", "--hard")
        self.ctx.run_pre_untracked = self.git.untracked()
        self.ctx.trusted_base = self.git.branch_base()
        freeze_config(self.ctx, self.git)
        self.run_loop(target, agents_by_loop)

    def run_loop(self, loop: LoopSpec, agents_by_loop: dict[str, str | None]) -> None:
        self.run_children(loop, agents_by_loop)
        passes = 0
        answer = self.cards.consume_answer(loop)
        approved = bool(answer) and loop.gate == Gate.HUMAN
        waited = 0.0
        baseline = self.protection.baseline(loop)
        while passes < loop.confirm:
            verdict, tail_text = self.administer_check(loop, baseline, passes)
            if verdict == Verdict.PASS:
                passes += 1
                continue
            passes = 0
            if verdict == Verdict.NOT_YET:
                waited = self.wait_on_not_yet(loop, tail_text, waited)
                continue
            if loop.gate == Gate.HUMAN:
                self.cards.supersede(str(loop.id), approved)
                approved = False
            if self.budget.spent(loop, waited):
                self.cards.escalate(loop, tail_text)
            self.run_agent_iteration(loop, agents_by_loop, tail_text, answer, baseline)
            answer = None
        self.close_loop(loop, approved)

    def run_children(self, loop: LoopSpec, agents_by_loop: dict[str, str | None]) -> None:
        if loop.parallel and loop.children:
            from .parallel import run_parallel

            run_parallel(self, loop, agents_by_loop)
            return
        for child in loop.children:
            self.run_loop(child, agents_by_loop)

    def administer_check(self, loop: LoopSpec, baseline, passes: int) -> tuple[Verdict, str]:
        loop_id = str(loop.id)
        self.enforce_quarantine(loop, "precheck", baseline)
        result = self.checks.run(loop)
        verdict, tail_text = result.verdict, result.output
        tampered = sorted(self.quarantine_paths.get(loop_id, set()))
        postcheck = self.enforce_quarantine(loop, "postcheck", baseline)
        if postcheck:
            tampered = sorted(self.quarantine_paths.get(loop_id, set()))
            verdict = Verdict.FAIL
            tail_text = self.quarantine_message(loop_id)
        elif verdict == Verdict.FAIL:
            note = self.pop_quarantine_note(loop_id)
            if note:
                tail_text = note + ("\n" + tail_text if tail_text else "")
        elif verdict == Verdict.PASS:
            self.pop_quarantine_note(loop_id)
            tampered = []
        self.events.emit(
            "check",
            loop_id,
            verdict=verdict.value,
            **({"next": loop.every_str} if verdict == Verdict.NOT_YET else {}),
            **({"streak": f"{passes + 1}/{loop.confirm}"} if verdict == Verdict.PASS and loop.confirm > 1 else {}),
            **({"tampered": tampered} if tampered else {}),
        )
        return verdict, tail_text

    def wait_on_not_yet(self, loop: LoopSpec, tail_text: str, waited: float) -> float:
        if self.budget.spent(loop, waited):
            self.cards.escalate(loop, tail_text, note=f"Still not-yet after {human(waited)} of waiting (check_every {loop.every_str}).")
        time.sleep(loop.every)
        return waited + loop.every

    def run_agent_iteration(self, loop: LoopSpec, agents_by_loop: dict[str, str | None], tail_text: str, answer: str | None, baseline) -> None:
        if self.budget.spent(loop):
            self.cards.escalate(loop, tail_text)
        if loop.task is None:
            self.cards.escalate(loop, tail_text)
        trusted = snapshot(self.ctx.paths.ledger)
        loop_id = str(loop.id)
        run_number = max(total_runs(trusted.entries, loop_id), self.git.run_commit_count(loop_id)) + 1
        rel_log = f".lute/logs/{loop_id}.run{run_number}.log"
        self.store.ensure_layout()
        self.events.emit("agent_start", loop_id, run=run_number, log=rel_log, cap=self.budget.runs_cap(loop))
        agent_command = agents_by_loop.get(loop_id)
        pre_agent_head = self.git.head()
        expected_branch = self.git.current_branch()
        duration, returncode = self.agents.spawn(
            loop,
            agent_command or "",
            self.agents.build_prompt(loop, agent_command or "", tail_text, answer),
            os.path.join(self.ctx.paths.logs, f"{loop_id}.run{run_number}.log"),
        )
        self.events.emit("agent_end", loop_id, run=run_number, exit=returncode, secs=duration)
        current_branch = self.git.current_branch()
        if current_branch != expected_branch:
            self.git.force_branch(expected_branch, pre_agent_head)
            self.git.text("checkout", "-q", "-f", expected_branch)
            restore_if_changed(self.ctx.paths.state, self.ctx.paths.ledger, trusted)
            self.cards.escalate(
                loop,
                f"agent left branch {expected_branch} on {current_branch}; runner restored {expected_branch}",
            )
        self.git.rewind_commits_keep_worktree(pre_agent_head)
        restore_if_changed(self.ctx.paths.state, self.ctx.paths.ledger, trusted)
        self.enforce_quarantine(loop, f"run{run_number}", baseline)
        self.ledger_append({"ts": now_iso(), "loop": loop_id, "run": run_number, "duration": duration, "exit": returncode})
        self.git.stage_run_work(self.ctx.run_pre_untracked, self.ctx.quarantined_paths)
        self.git.commit(f"lute({loop_id}): run {run_number}", allow_empty=True)

    def enforce_quarantine(self, loop: LoopSpec, run_id: str, baseline):
        result = self.protection.enforce(str(loop.id), run_id, baseline)
        if not result:
            return None
        patch = os.path.relpath(result.patch_path, self.ctx.repo_root)
        self.events.emit("quarantine", str(loop.id), run=run_id, id=result.qid, paths=list(result.paths), patch=patch, restored=True)
        self.ctx.quarantined_paths.update(result.paths)
        self.quarantine_paths.setdefault(str(loop.id), set()).update(result.paths)
        self.quarantine_notes.setdefault(str(loop.id), []).append(
            f"Trusted exam edits were quarantined and restored before this check: {result.qid} ("
            + ", ".join(result.paths)
            + f"). Inspect with: lute quarantine diff {result.qid}"
        )
        return result

    def quarantine_message(self, loop_id: str) -> str:
        return self.pop_quarantine_note(loop_id) or "exam materials modified and quarantined; trusted exam files were restored before checking"

    def pop_quarantine_note(self, loop_id: str) -> str:
        notes = self.quarantine_notes.pop(loop_id, [])
        self.quarantine_paths.pop(loop_id, None)
        return "\n".join(notes)

    def close_loop(self, loop: LoopSpec, approved: bool) -> None:
        if loop.gate == Gate.HUMAN and not approved:
            self.cards.gate_halt(loop)
        if self.git.stage_run_work(self.ctx.run_pre_untracked, self.ctx.quarantined_paths):
            self.git.commit(f"lute({loop.id}): close")
        self.events.emit("loop_closed", str(loop.id))

    def ensure_branch(self, root_id: str) -> None:
        branch = f"lute/{root_id}"
        if not self.git.has_head():
            raise PreconditionError("repo has no commits; make one first: git add -A && git commit -m init")
        current = self.git.current_branch()
        dirty = bool(self.git.status_porcelain("-uno").strip())
        if current == branch:
            self.git.clear_stale_locks()
            if dirty:
                self.git.text("reset", "-q", "--hard")
            return
        if dirty:
            files = ", ".join(line[3:] for line in self.git.status_porcelain("-uno").splitlines()[:8])
            raise PreconditionError(f"working tree has uncommitted changes ({files}); commit or stash before lute checks out {branch}")
        self.git.checkout_or_create_branch(branch)

    def seal_ignore(self, root_id: str) -> None:
        ignore = os.path.join(self.ctx.paths.state, ".gitignore")
        if self.git.ok("ls-files", "--error-unmatch", "--", ignore) and not self.git.ok("diff", "--quiet", "--", ignore):
            self.git.text("add", ignore)
            self.git.commit(f"lute({root_id}): capture")

    def run_origin(self, root_id: str) -> str | None:
        if not self.store.is_regular_file(self.ctx.paths.events):
            return None
        origin = None
        with open(self.ctx.paths.events, encoding="utf-8") as f:
            for line in f:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("ev") == "run_start" and event.get("loop") == root_id and event.get("start"):
                    origin = event["start"]
        return origin

    def acquire_lock(self) -> None:
        self.store.ensure_layout()
        lock = self.ctx.paths.lock
        tmp = f"{lock}.{os.getpid()}"
        self.store.safe_write_regular(tmp, json.dumps({"pid": os.getpid(), "start": now_iso()}))
        try:
            for _ in range(3):
                try:
                    os.link(tmp, lock)
                except FileExistsError:
                    if not self.store.is_regular_file(lock):
                        self.store.remove_runner_file(lock)
                        continue
                    try:
                        info = json.loads(open(lock).read())
                    except (ValueError, OSError):
                        info = {}
                    if processes.pid_alive(info.get("pid")):
                        raise UsageError(
                            f"another lute run is active in this repo (pid {info.get('pid')}, since "
                            f"{info.get('start', '?')}); wait for it, or remove {lock} if it is truly dead"
                        )
                    self.store.remove_runner_file(lock)
                    continue
                atexit.register(self.release_lock)
                self._lock_registered = True
                return
            raise UsageError(f"could not acquire the run lock at {lock}")
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    def release_lock(self) -> None:
        try:
            if json.loads(open(self.ctx.paths.lock).read()).get("pid") == os.getpid():
                os.remove(self.ctx.paths.lock)
        except (OSError, ValueError):
            pass

    def ledger_totals(self) -> tuple[int, float]:
        return ledger_totals(self.ledger_entries())


def resolved_loop(root: LoopSpec, loop_id: str | None, child_mode: bool) -> LoopSpec:
    if not loop_id or loop_id == str(root.id):
        return root
    found = root.find(loop_id)
    if child_mode and found:
        return found
    raise UsageError(f"'{loop_id}' is not the root loop ('{root.id}'); CLI runs select the root; children run through their parent")
