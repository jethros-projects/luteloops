"""Running the model-facing commands: agents and the halt hook.

`run_agent_command` feeds the prompt on stdin, streams all output to the
per-run log, and reaps the agent's whole process group on exit or interrupt —
the runner owns what it spawns. `AgentRunner` adds the policy around that
call: wrapping commands in the configured cage, building the iteration prompt
(failing-check tail, journal ritual, any human answer), nagging when the
journal went untouched, and firing the fire-and-forget `on_halt` notifier
with `LUTE_LOOP`/`LUTE_REASON`/`LUTE_CARD` in its environment.
"""

from __future__ import annotations

import os
import subprocess
import time

from .cage import DEFAULT_CAGE_TEMPLATE, expand_cage_template
from .context import AppContext
from .domain import LoopSpec
from .errors import UsageError
from . import processes
from .state_store import StateStore


def run_agent_command(command: str, prompt: str, log_path: str) -> tuple[float, int | None]:
    started = time.time()
    with processes.open_output(log_path, append=False) as log:
        proc = subprocess.Popen(
            ["sh", "-c", command],
            stdin=subprocess.PIPE,
            encoding="utf-8",
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            proc.communicate(prompt)
        finally:
            # The runner owns this child: reap its whole group on normal exit and
            # on interrupt (e.g. `lute stop` signalling the runner mid-iteration).
            processes.stop_group(proc.pid)
    return round(time.time() - started, 3), proc.returncode


def fire_halt(command: str, *, loop_id: str, reason: str, card_path: str, environ: dict[str, str] | None = None) -> None:
    env = dict(os.environ if environ is None else environ, LUTE_LOOP=loop_id, LUTE_REASON=reason, LUTE_CARD=card_path)
    try:
        subprocess.run(["sh", "-c", command], env=env, timeout=30)
    except Exception:
        pass


class AgentRunner:
    def __init__(self, ctx: AppContext, store: StateStore, repo_root, self_cmd):
        self.ctx = ctx
        self.store = store
        self.repo_root = repo_root
        self.self_cmd = self_cmd

    def cage_wrap(self, command: str, repo_root: str | None = None) -> str:
        cfg = self.ctx.active_config()
        cage = cfg.get("cage")
        if not cage:
            return command
        template = DEFAULT_CAGE_TEMPLATE if cage == "docker" else cage
        try:
            return expand_cage_template(
                template,
                repo_root or self.repo_root,
                cfg.get("cage_image", "alpine:3"),
                cfg.get("cage_mounts"),
                command,
            )
        except ValueError as exc:
            raise UsageError(str(exc)) from exc

    def build_prompt(self, loop: LoopSpec, agent_command: str, tail_text: str, answer: str | None) -> str:
        # Frame the failure honestly: a silent (boolean) check that printed nothing
        # names its exit-only signal (no empty box); a shown-in-full output claims
        # no elision; only a genuinely-elided excerpt says so.
        cmd = loop.done_when.command
        if not tail_text.strip():
            check_seg = (
                f"The check `{cmd}` is failing — it printed nothing, so it signals only "
                "via its exit status; inspect the files it names, or re-run it yourself."
            )
        elif "lines elided" in tail_text:
            check_seg = (
                f"The check `{cmd}` is failing. An excerpt of its output (first and last "
                f"lines; the middle is elided — re-run the check for the full log):\n"
                f"<check_output>\n{tail_text}\n</check_output>"
            )
        else:
            check_seg = (
                f"The check `{cmd}` is failing. Its output:\n"
                f"<check_output>\n{tail_text}\n</check_output>"
            )
        segs = [f"You are one iteration of a loop. Goal: {loop.task}", check_seg]
        if answer:
            segs.append(f"A human reviewed the last escalation and said: {answer}")
        journal = f".lute/journal/{loop.id}.md"
        segs.append(
            f"Start by reading {journal} — your past attempts live there. You may re-run "
            f"`{loop.done_when.command}` to check your work; the runner re-runs it and its "
            "exit code is the only verdict, so don't stop on a fix you haven't seen pass. "
            f"Finish by appending 1–3 lines to {journal}: what you tried, what you learned, "
            "what the next run must not retry (and, if a human gave direction this run, its "
            "substance so the next run still honours it). Compact it if it passes ~100 lines."
        )
        prompt = "\n\n".join(segs)
        if self.ctx.nagged.get(str(loop.id)):
            prompt = (
                f"Your journal {journal} was unchanged last run — begin by reading it and "
                "end by appending what you tried and what not to retry.\n\n"
            ) + prompt
        return prompt

    def spawn(self, loop: LoopSpec, agent_command: str, prompt: str, log_path: str) -> tuple[float, int | None]:
        self.store.ensure_layout()
        journal_path = os.path.join(self.ctx.paths.journal, f"{loop.id}.md")
        before = os.path.getmtime(journal_path) if os.path.exists(journal_path) else None
        duration, returncode = run_agent_command(self.cage_wrap(agent_command), prompt, log_path)
        after = os.path.getmtime(journal_path) if os.path.exists(journal_path) else None
        self.ctx.nagged[str(loop.id)] = before == after
        return duration, returncode

    def fire_halt(self, loop_id: str, reason: str, card_path: str) -> None:
        hook = self.ctx.active_config().get("on_halt")
        if hook:
            fire_halt(hook, loop_id=loop_id, reason=reason, card_path=card_path)
