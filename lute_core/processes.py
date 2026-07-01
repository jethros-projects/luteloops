"""Best-effort process identity and stopping helpers."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import stat
import time
from typing import Sequence

O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


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


def command_line(pid: int | None) -> str:
    if not pid:
        return ""
    return subprocess.run(
        ["ps", "-ww", "-o", "command=", "-p", str(pid)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


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
        result = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
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


def owns(pid: int | None, repo_root: str, marker: str) -> bool | None:
    """Is pid the runner a lock/pid file names? The file is just bytes — a crash
    leaves it stale, the pid gets reused, and anything in the repo can write it —
    so identity comes from live host facts the file cannot fake: the pid's cwd
    is inside repo_root AND its command line matches marker (a regex for how
    that runner was invoked). True = confirmed ours. False = provably not
    (dead, or cwd known and elsewhere). None = unconfirmed: cwd unknown, or a
    repo-cwd process whose argv does not match — ps evidence can confirm
    identity, but its absence cannot refute it (wrappers, renames)."""
    if not pid_alive(pid):
        return False
    serves = serves_repo(pid, repo_root)  # type: ignore[arg-type]
    if serves is False:
        return False
    if serves and re.search(marker, command_line(pid)):
        return True
    return None


def descendants(pid: int) -> list[int]:
    seen: set[int] = set()
    frontier = [pid]
    while frontier:
        parent = frontier.pop()
        try:
            result = subprocess.run(
                ["pgrep", "-P", str(parent)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            return sorted(seen, reverse=True)
        for line in result.stdout.splitlines():
            try:
                child = int(line)
            except ValueError:
                continue
            if child not in seen:
                seen.add(child)
                frontier.append(child)
    return sorted(seen, reverse=True)


def stop_group(pid: int) -> bool:
    """SIGINT/SIGKILL a process group, falling back to the pid, and report whether it is gone."""
    def sig(sig_no: int) -> None:
        for child in descendants(pid):
            try:
                os.kill(child, sig_no)
            except OSError:
                pass
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


def open_output(path: str, *, append: bool):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
    if O_NOFOLLOW:
        flags |= O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o644)
    except OSError:
        try:
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                os.remove(path)
        except OSError:
            pass
        fd = os.open(path, flags, 0o644)
    return os.fdopen(fd, "w", encoding="utf-8")


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

    with open_output(stdout_path, append=True) as output, open(os.devnull) as devin:
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
