"""Detached runs: `lute run --bg` and the auto-detach decision.

`terminal_ok` answers "is a human actually watching?" — both stdio ends a TTY
and a real TERM. `spawn_run` re-executes lute as a `--plain` child in its own
session, output to `.lute/logs/runner.log`, immune to hangup: the run
survives the terminal closing and is re-attached with `lute watch`.
"""

from __future__ import annotations

import os
import sys

from . import processes
from .runner import self_argv
from .state_store import StateStore


def terminal_ok() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    if os.environ.get("TERM", "dumb") in ("", "dumb", "unknown"):
        return False
    return True


def spawn_run(args: list[str], store: StateStore, runner_log: str):
    store.ensure_layout()
    cmd = [*self_argv(), "run", "--plain", *[arg for arg in args if arg != "--bg"]]
    return processes.spawn_detached(cmd, stdout_path=runner_log, ignore_hup=True)
