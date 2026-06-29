"""Detached run helpers and terminal detection."""

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
