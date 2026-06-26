"""Protected-file glob matching and tamper snapshots."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable

from .context import AppContext
from .git_repo import GitRepo


def glob_re(pattern: str) -> re.Pattern[str]:
    i, n, out = 0, len(pattern), []
    while i < n:
        if pattern[i : i + 2] == "**":
            i += 2
            if pattern[i : i + 1] == "/":
                out.append("(?:.*/)?")
                i += 1
            else:
                out.append(".*")
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("(?s:" + "".join(out) + r")\Z")


def protected_files(globs: list[str]) -> list[str]:
    matchers = [glob_re(g) for g in globs]
    files: list[str] = []
    for root, dirs, names in os.walk("."):
        dirs[:] = [d for d in dirs if d not in (".git", ".lute")]
        for name in names:
            rel = os.path.relpath(os.path.join(root, name), ".")
            if any(m.match(rel) for m in matchers):
                files.append(rel)
    return files


def protected_snapshot(globs: list[str]) -> dict[str, str]:
    return {p: hashlib.sha256(open(p, "rb").read()).hexdigest() for p in protected_files(globs)}


def protected_snapshot_at(globs: list[str], ref: str, git_text: Callable[..., str]) -> dict[str, str]:
    matchers = [glob_re(g) for g in globs]
    snap: dict[str, str] = {}
    for path in git_text("ls-tree", "-r", "--name-only", ref).splitlines():
        if any(m.match(path) for m in matchers):
            if hasattr(git_text, "__self__") and isinstance(getattr(git_text, "__self__"), GitRepo):
                blob = git_text.__self__.show_bytes(f"{ref}:{path}")
                if blob is not None:
                    snap[path] = hashlib.sha256(blob).hexdigest()
            else:
                try:
                    snap[path] = hashlib.sha256(git_text("show", f"{ref}:{path}").encode()).hexdigest()
                except Exception:
                    pass
    return snap


def tampered_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    changed = set(before) ^ set(after)
    changed |= {p for p in before.keys() & after.keys() if before[p] != after[p]}
    return sorted(changed)


class Protection:
    def __init__(self, ctx: AppContext, git: GitRepo):
        self.ctx = ctx
        self.git = git

    def baseline(self, loop) -> tuple[tuple[str, ...], str, dict[str, str] | None]:
        protected = loop.protected or ()
        base_ref = self.git.branch_base()
        baseline = self.snapshot_at(list(protected), base_ref) if protected else None
        return protected, base_ref, baseline

    def snapshot_at(self, globs: list[str], ref: str) -> dict[str, str]:
        matchers = [glob_re(glob) for glob in globs]
        snap: dict[str, str] = {}
        for path in self.git.ls_tree_files(ref):
            if any(m.match(path) for m in matchers):
                blob = self.git.show_bytes(f"{ref}:{path}")
                if blob is not None:
                    snap[path] = hashlib.sha256(blob).hexdigest()
        return snap

    def grader_tampered(self, base_ref: str) -> list[str]:
        out: list[str] = []
        paths = [os.path.relpath(self.ctx.paths.config)]
        if self.ctx.manifest_path:
            paths.insert(0, os.path.relpath(self.ctx.manifest_path))
        for path in paths:
            if path.startswith(".."):
                continue
            committed = self.git.show_bytes(f"{base_ref}:{path}")
            if committed is None:
                continue
            current = open(path, "rb").read() if os.path.exists(path) else b""
            if hashlib.sha256(current).hexdigest() != hashlib.sha256(committed).hexdigest():
                out.append(path)
        return out

    def tampered(self, protected_globs, protected_base, grader_base) -> list[str]:
        changed = (
            tampered_paths(protected_base, protected_snapshot(list(protected_globs)))
            if protected_base is not None else []
        )
        return changed + self.grader_tampered(grader_base)
