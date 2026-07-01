"""Shell check execution and verdict classification."""

from __future__ import annotations

import contextlib
import io
import os
import subprocess

from .context import AppContext
from .domain import CheckResult, LoopSpec, Verdict
from .formatting import tail
from .git_repo import GitRepo
from . import processes

CHECK_TIMEOUT = 600


def check_timeout() -> float:
    raw = os.environ.get("LUTE_CHECK_TIMEOUT")
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = CHECK_TIMEOUT
        if value > 0:
            return value
    return CHECK_TIMEOUT


def timeout_label(seconds: float) -> str:
    return f"{int(seconds)}s" if float(seconds).is_integer() else f"{seconds:g}s"


def run_command(
    command: str,
    timeout: float,
    *,
    cwd: str | None = None,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    combine_stderr: bool = True,
) -> tuple[int | None, str, str, bool]:
    proc = subprocess.Popen(
        ["sh", "-c", command],
        cwd=cwd,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if combine_stderr else subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(input_text, timeout=timeout)
        return proc.returncode, stdout or "", "" if combine_stderr else (stderr or ""), False
    except subprocess.TimeoutExpired:
        processes.stop_group(proc.pid)
        try:
            stdout, stderr = proc.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        return None, stdout or "", "" if combine_stderr else (stderr or ""), True
    except BaseException:
        # An interrupt (e.g. `lute stop` signalling the runner) must not orphan
        # an in-flight check or LLM judge that lives in its own session.
        processes.stop_group(proc.pid)
        raise


def run_shell_check(command: str, timeout: float, *, classify: bool = False, env: dict[str, str] | None = None) -> tuple[str, str]:
    if classify and subprocess.run(["sh", "-n", "-c", command], capture_output=True).returncode:
        return "error", ""
    returncode, stdout, _, timed_out = run_command(command, timeout, env=env)
    if timed_out:
        return "fail", f"(check timed out after {timeout_label(timeout)})"
    if returncode == 0:
        verdict = "pass"
    elif returncode == 75:
        verdict = "not_yet"
    elif classify and returncode in (126, 127):
        verdict = "error"
    else:
        verdict = "fail"
    return verdict, tail(stdout, 50)


class CheckRunner:
    def __init__(self, ctx: AppContext, git: GitRepo, cage_wrap, self_cmd):
        self.ctx = ctx
        self.git = git
        self.cage_wrap = cage_wrap
        self.self_cmd = self_cmd

    def run(self, loop: LoopSpec, *, classify: bool = False, env: dict[str, str] | None = None) -> CheckResult:
        command = loop.done_when.command
        if command.startswith("judge:"):
            rubric = command[len("judge:"):].strip()
            return self.run_judge(rubric)
        verdict, output = run_shell_check(command, check_timeout(), classify=classify, env=env)
        return CheckResult(Verdict(verdict), output)

    def run_judge(self, rubric: str) -> CheckResult:
        from . import judge

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = judge.grade(rubric, self.ctx, self.git, self.cage_wrap)
        output = tail(out.getvalue() + err.getvalue(), 50)
        return CheckResult(Verdict.PASS if rc == 0 else Verdict.FAIL, output)
