"""Typed errors for lute's executable boundary.

Every failure the CLI can exit with is one of these classes, and each carries
its `ExitCode` — usage/precondition 1, git/internal 2, blocked 3, gated 4 —
so `main` turns any raised `LuteError` into the right shell contract without
a mapping table at the call sites.
"""

from __future__ import annotations

from dataclasses import dataclass

from .domain import ExitCode


@dataclass
class LuteError(Exception):
    message: str
    exit_code: ExitCode = ExitCode.INTERNAL

    def __str__(self) -> str:
        return self.message


class UsageError(LuteError):
    def __init__(self, message: str):
        super().__init__(message, ExitCode.USAGE)


class PreconditionError(LuteError):
    def __init__(self, message: str):
        super().__init__(message, ExitCode.USAGE)


class GitError(LuteError):
    def __init__(self, message: str):
        super().__init__(message, ExitCode.INTERNAL)


class Blocked(LuteError):
    def __init__(self, message: str = "blocked"):
        super().__init__(message, ExitCode.BLOCKED)


class Gated(LuteError):
    def __init__(self, message: str = "gated"):
        super().__init__(message, ExitCode.GATED)


class InternalError(LuteError):
    def __init__(self, message: str):
        super().__init__(message, ExitCode.INTERNAL)
