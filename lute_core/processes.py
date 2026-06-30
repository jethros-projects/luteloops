"""Best-effort process identity and stopping helpers."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from typing import Sequence


def pid_alive(pid: int | None) -> bool:
    try:
        os.kill(pid, 0)  # type: ignore[arg-type]
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (TypeError, ValueError):
        return False
    return True


def group_alive(pgid: int | None) -> bool:
    try:
        os.killpg(pgid, 0)  # type: ignore[arg-type]
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (TypeError, ValueError):
        return False
    return True


def command_contains(pid: int | None, needle: str) -> bool:
    if not pid or not pid_alive(pid):
        return False
    cmd = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
    return needle in cmd


def command_line(pid: int | None) -> str:
    if not pid:
        return ""
    return subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout


def proc_cwd(pid: int) -> str | None:
    """Return pid's cwd, or None when this host cannot determine it."""
    link = f"/proc/{pid}/cwd"
    if os.path.exists(link):
        try:
            return os.path.realpath(link)
        except OSError:
            return None
    if not shutil.which("lsof"):
        return None
    try:
        result = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], capture_output=True, text=True)
    except FileNotFoundError:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("n"):
            return os.path.realpath(line[1:])
    return None


def serves_repo(pid: int, repo_root: str) -> bool | None:
    """True/False when pid's cwd is known; None when this host cannot determine it."""
    cwd = proc_cwd(pid)
    if cwd is None:
        return None
    cwd, root = os.path.realpath(cwd), os.path.realpath(repo_root)
    try:
        return cwd == root or os.path.commonpath([cwd, root]) == root
    except ValueError:
        return False


def stop_group(pid: int) -> bool:
    """SIGINT/SIGKILL a process group, falling back to the pid, and report whether it is gone."""
    def sig(sig_no: int) -> None:
        try:
            os.killpg(pid, sig_no)
        except OSError:
            try:
                os.kill(pid, sig_no)
            except OSError:
                pass

    sig(signal.SIGINT)
    for _ in range(20):
        if not pid_alive(pid) and not group_alive(pid):
            return True
        time.sleep(0.1)
    sig(signal.SIGKILL)
    for _ in range(10):
        if not pid_alive(pid) and not group_alive(pid):
            return True
        time.sleep(0.1)
    return not pid_alive(pid) and not group_alive(pid)


def stop_run(pid: int, grace: int = 120) -> bool:
    """Ask a runner to stop, then confirm it is gone.

    A SIGINT lets the runner reap the children it owns (its agent, an in-flight
    check or judge, or its parallel child runners) through its own teardown, then
    exit and release the lock — no pid files, no ancestry walks. A wedged runner
    that never returns is forced through its group as a last resort.
    """
    try:
        os.kill(pid, signal.SIGINT)
    except OSError:
        pass
    for _ in range(grace):
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return stop_group(pid)


def spawn_detached(
    cmd: Sequence[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    stdout_path: str,
    ignore_hup: bool = False,
) -> subprocess.Popen:
    def preexec() -> None:
        if ignore_hup:
            signal.signal(signal.SIGHUP, signal.SIG_IGN)

    with open(stdout_path, "a") as output, open(os.devnull) as devin:
        return subprocess.Popen(
            list(cmd),
            cwd=cwd,
            env=env,
            stdin=devin,
            stdout=output,
            stderr=output,
            start_new_session=True,
            preexec_fn=preexec if ignore_hup else None,
        )
