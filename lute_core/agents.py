"""Agent and notification command execution."""

from __future__ import annotations

import os
import subprocess
import time

from .cage import DEFAULT_CAGE_TEMPLATE, expand_cage_template
from .context import AppContext
from .domain import LoopSpec
from .errors import UsageError
from .state_store import StateStore


def run_agent_command(command: str, prompt: str, log_path: str) -> tuple[float, int]:
    started = time.time()
    with open(log_path, "w") as log:
        result = subprocess.run(
            ["sh", "-c", command],
            input=prompt,
            encoding="utf-8",
            stdout=log,
            stderr=log,
        )
    return round(time.time() - started, 3), result.returncode


def fire_halt(command: str, *, loop_id: str, reason: str, card_path: str, environ: dict[str, str] | None = None) -> None:
    env = dict(os.environ if environ is None else environ, LUTE_LOOP=loop_id, LUTE_REASON=reason, LUTE_CARD=card_path)
    try:
        subprocess.run(["sh", "-c", command], env=env, timeout=30)
    except Exception:
        pass


def human(secs: float) -> str:
    minutes, seconds = divmod(int(secs), 60)
    return f"{minutes}m{seconds:02d}s" if minutes else f"{seconds}s"


class AgentRunner:
    def __init__(self, ctx: AppContext, store: StateStore, repo_root, self_cmd):
        self.ctx = ctx
        self.store = store
        self.repo_root = repo_root
        self.self_cmd = self_cmd

    def cage_wrap(self, command: str) -> str:
        cfg = self.ctx.active_config()
        cage = cfg.get("cage")
        if not cage:
            return command
        template = (self.ctx.cage_template or DEFAULT_CAGE_TEMPLATE) if cage == "docker" else cage
        try:
            return expand_cage_template(
                template,
                self.repo_root,
                cfg.get("cage_image", "alpine:3"),
                cfg.get("cage_mounts"),
                command,
            )
        except ValueError as exc:
            raise UsageError(str(exc)) from exc

    def build_prompt(self, loop: LoopSpec, agent_command: str, tail_text: str, answer: str | None) -> str:
        segs = [
            f"You are one iteration of a loop. Goal: {loop.task}",
            f"The check `{loop.done_when.command}` is failing. Last 50 lines of its output:\n{tail_text}",
        ]
        if answer:
            segs.append(f"A human reviewed the last escalation and said: {answer}")
        journal = f".lute/journal/{loop.id}.md"
        segs.append(
            f"FIRST: read {journal}; your past attempts live there.\n"
            "LAST: append 1–3 lines to it: what you tried, what you learned,\n"
            "what the next run must NOT retry. If it exceeds ~100 lines, compact it."
        )
        prompt = "\n\n".join(segs)
        if self.ctx.nagged.get(str(loop.id)):
            prompt = (
                f"NAG: your journal {journal} did not change last "
                "run; do not skip the FIRST and LAST steps below.\n\n"
            ) + prompt
        return prompt

    def spawn(self, loop: LoopSpec, agent_command: str, prompt: str, log_path: str) -> tuple[float, int]:
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
