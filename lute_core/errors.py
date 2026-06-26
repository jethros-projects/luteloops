"""Typed errors for lute's executable boundary."""

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
