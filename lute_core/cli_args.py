"""Small CLI argument helpers used by the executable boundary."""

from __future__ import annotations


class UsageError(ValueError):
    pass


def parse_args(args: list[str], flags: set[str], bools: tuple[str, ...] = ()) -> tuple[list[str], dict[str, str | bool]]:
    pos: list[str] = []
    opts: dict[str, str | bool] = {}
    it = iter(args)
    for arg in it:
        if arg == "--":
            pos.extend(it)
            break
        if arg in flags:
            value = next(it, None)
            if value is None:
                raise UsageError(f"{arg} needs a value")
            opts[arg.lstrip("-")] = value
        elif arg in bools:
            opts[arg.lstrip("-")] = True
        elif arg.startswith("--"):
            raise UsageError(f"unknown flag {arg}")
        else:
            pos.append(arg)
    return pos, opts


def require_positionals(pos: list[str], usage: str, min_count: int = 0, max_count: int | None = None) -> None:
    if len(pos) < min_count or (max_count is not None and len(pos) > max_count):
        raise UsageError(usage)

