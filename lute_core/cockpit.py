"""Curses/TUI boundary.

The runner writes state; this module only decides whether a terminal can render
and, for now, falls back to the replay snapshot when curses is unavailable.
"""

from __future__ import annotations

import os
import signal
import sys

from . import processes
from .domain import LoopSpec
from .events import plain_line
from .runner import self_argv
from .state_store import StateStore
from .watch import render_snapshot


def tui_ok() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    if os.environ.get("TERM", "dumb") in ("", "dumb", "unknown"):
        return False
    try:
        import curses  # noqa: F401
        return True
    except Exception:
        print("lute: cockpit unavailable (no curses); falling back to plain output", file=sys.stderr)
        return False


def spawn_run(args: list[str], store: StateStore, runner_log: str):
    store.ensure_layout()
    cmd = [*self_argv(), "run", "--plain", *[arg for arg in args if arg != "--bg"]]
    return processes.spawn_detached(cmd, stdout_path=runner_log, ignore_hup=True)


def run_in_cockpit(args: list[str], root: LoopSpec, store: StateStore, runner_log: str, events_path: str) -> int | str:
    child = spawn_run(args, store, runner_log)
    print(f"detached: run continues (pid {child.pid}) · re-attach: lute watch · stop: lute stop")
    return 0


def print_plain_run_end(root: LoopSpec) -> None:
    line = plain_line({"ev": "run_end", "loop": str(root.id)})
    if line:
        print(line)
