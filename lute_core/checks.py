"""Shell check execution and verdict classification."""

from __future__ import annotations

import subprocess

from .context import AppContext
from .domain import CheckResult, LoopSpec, Verdict
from .errors import InternalError, UsageError
from .git_repo import GitRepo

CHECK_TIMEOUT = 600
JUDGE_INSTRUCTION = ("Reply with exactly PASS or FAIL on the first line, then your "
                     "reasons, citing specific files and lines for every rubric item.")


class CheckTimedOut(TimeoutError):
    pass


def tail(text: str, n: int) -> str:
    return "\n".join(text.splitlines()[-n:])


def run_shell_check(command: str, timeout: int, *, lenient: bool = False, classify: bool = False) -> tuple[str, str]:
    if classify and subprocess.run(["sh", "-n", "-c", command], capture_output=True).returncode:
        return "error", ""
    try:
        result = subprocess.run(
            ["sh", "-c", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        if classify or lenient:
            return ("error" if classify else "fail"), "(check timed out)"
        raise CheckTimedOut(command) from exc
    if result.returncode == 0:
        verdict = "pass"
    elif result.returncode == 75:
        verdict = "not_yet"
    elif classify and result.returncode in (126, 127):
        verdict = "error"
    else:
        verdict = "fail"
    return verdict, tail(result.stdout or "", 50)


class CheckRunner:
    def __init__(self, ctx: AppContext, git: GitRepo, cage_wrap):
        self.ctx = ctx
        self.git = git
        self.cage_wrap = cage_wrap

    def run(self, loop: LoopSpec, *, lenient: bool = False, classify: bool = False) -> CheckResult:
        command = loop.done_when.command
        if command.startswith("judge:"):
            verdict, output = self.judge(loop, command[len("judge:"):].strip(), lenient)
        else:
            try:
                verdict, output = run_shell_check(command, CHECK_TIMEOUT, lenient=lenient, classify=classify)
            except CheckTimedOut as exc:
                raise InternalError(
                    f"check `{command}` for '{loop.id}' exceeded {CHECK_TIMEOUT // 60} minutes; "
                    "long-running checks are outside the initial release boundary (spec §5); split or speed up the exam"
                ) from exc
        return CheckResult(Verdict(verdict), output)

    def judge(self, loop: LoopSpec, rubric: str, lenient: bool = False) -> tuple[str, str]:
        judge = self.ctx.active_config().get("judge")
        if not judge:
            if lenient:
                return "fail", "(no judge configured)"
            raise UsageError(f"loop '{loop.id}' uses judge: but {self.ctx.paths.config} has no 'judge' command")
        diff = self.git.text("diff", self.git.branch_base() + "...HEAD")
        payload = f"Rubric: {rubric}\n\n{diff}\n\n{JUDGE_INSTRUCTION}\n"
        try:
            result = subprocess.run(
                ["sh", "-c", self.cage_wrap(judge)],
                input=payload,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=CHECK_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            if lenient:
                return "fail", "(judge timed out)"
            raise InternalError(
                f"judge for '{loop.id}' exceeded {CHECK_TIMEOUT // 60} minutes; "
                "long-running checks are outside the initial release boundary (spec §5); use a faster judge"
            ) from exc
        out = result.stdout or ""
        first = out.strip().splitlines()[0].strip() if out.strip() else ""
        return ("pass" if first == "PASS" else "fail"), tail(out, 50)
