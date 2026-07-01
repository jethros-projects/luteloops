"""Git adapter for lute.

All production git subprocess usage lives here so higher-level modules talk in
repository operations rather than command strings.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass

from .errors import GitError, PreconditionError


@dataclass
class GitRepo:
    root: str

    def _safe_args(self, cwd: str | None = None) -> list[str]:
        root = cwd or self.root
        args = ["-c", "core.hooksPath=/dev/null", "-c", "core.quotePath=false"]
        try:
            result = subprocess.run(
                ["git", "-C", root, "config", "--local", "--name-only", "--get-regexp", r"^(filter|diff)\."],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            return args
        if result.returncode:
            return args
        for name in sorted(set(result.stdout.splitlines())):
            if re.fullmatch(r"filter\..+\.(clean|smudge|process)", name):
                args += ["-c", f"{name}="]
            elif re.fullmatch(r"diff\..+\.(command|textconv)", name):
                args += ["-c", f"{name}="]
        return args

    @classmethod
    def discover(cls) -> "GitRepo":
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode:
            raise PreconditionError("not a git repository; run: git init   (lute keeps all state in git)")
        return cls(result.stdout.strip())

    def run(self, *args: str, cwd: str | None = None, check: bool = True, text: bool = True) -> subprocess.CompletedProcess:
        root = cwd or self.root
        env = dict(os.environ)
        env.pop("GIT_EXTERNAL_DIFF", None)
        kwargs = {"encoding": "utf-8", "errors": "replace"} if text else {}
        result = subprocess.run(
            ["git", "-C", root, *self._safe_args(root), *args],
            capture_output=True,
            text=text,
            env=env,
            **kwargs,
        )
        if check and result.returncode:
            msg = (result.stderr or result.stdout or "").strip()
            raise GitError(f"git {' '.join(args)} failed: {msg}")
        return result

    def text(self, *args: str, cwd: str | None = None) -> str:
        return self.run(*args, cwd=cwd).stdout

    def ok(self, *args: str, cwd: str | None = None) -> bool:
        return self.run(*args, cwd=cwd, check=False).returncode == 0

    def shared_text(self, shared_root: str, *args: str) -> str:
        for _ in range(30):
            result = subprocess.run(
                ["git", "-C", shared_root, *self._safe_args(shared_root), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={k: v for k, v in os.environ.items() if k != "GIT_EXTERNAL_DIFF"},
            )
            if result.returncode == 0:
                return result.stdout
            if "File exists" in result.stderr:
                time.sleep(0.1)
                continue
            msg = (result.stderr or result.stdout or "").strip()
            raise GitError(f"git -C {shared_root} {' '.join(args)} failed: {msg}")
        raise GitError(f"git main-repo lock did not clear for: {' '.join(args)}")

    def current_branch(self, cwd: str | None = None) -> str:
        return self.text("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd).strip()

    def head(self, cwd: str | None = None) -> str:
        return self.text("rev-parse", "HEAD", cwd=cwd).strip()

    def git_path(self, name: str, cwd: str | None = None) -> str:
        return self.text("rev-parse", "--git-path", name, cwd=cwd).strip()

    def has_head(self, cwd: str | None = None) -> bool:
        return self.ok("rev-parse", "-q", "--verify", "HEAD", cwd=cwd)

    def branch_exists(self, branch: str, cwd: str | None = None) -> bool:
        return self.ok("rev-parse", "-q", "--verify", branch, cwd=cwd)

    def status_porcelain(self, *extra: str, cwd: str | None = None) -> str:
        return self.text("status", "--porcelain", *extra, cwd=cwd)

    def status_porcelain_z(self, *extra: str, cwd: str | None = None) -> list[str]:
        raw = self.run("status", "--porcelain=v1", "-z", *extra, cwd=cwd, text=False).stdout
        return [os.fsdecode(item) for item in raw.split(b"\0") if item]

    def untracked(self, cwd: str | None = None) -> set[str]:
        return {entry[3:] for entry in self.status_porcelain_z(cwd=cwd) if entry.startswith("?? ")}

    def reset_index(self, cwd: str | None = None) -> None:
        self.text("reset", "-q", cwd=cwd)

    def rewind_commits_keep_worktree(self, ref: str, cwd: str | None = None) -> bool:
        if self.head(cwd=cwd) == ref:
            self.reset_index(cwd=cwd)
            return False
        self.reset_mixed(ref, cwd=cwd)
        return True

    def run_commit_count(self, loop_id: str, cwd: str | None = None) -> int:
        return sum(
            1
            for subject in self.text("log", "--format=%s", cwd=cwd).splitlines()
            if subject.startswith(f"lute({loop_id}): run ")
        )

    def branch_base(self, cwd: str | None = None) -> str:
        head = "HEAD"
        for line in self.text("log", "--first-parent", "--format=%H %s", cwd=cwd).splitlines():
            head, _, subject = line.partition(" ")
            if not subject.startswith("lute("):
                return head
        return head

    def show_bytes(self, ref_path: str, cwd: str | None = None) -> bytes | None:
        result = self.run("show", ref_path, cwd=cwd, check=False, text=False)
        return result.stdout if result.returncode == 0 else None

    def object_bytes(self, object_id: str, cwd: str | None = None) -> bytes | None:
        result = self.run("cat-file", "-p", object_id, cwd=cwd, check=False, text=False)
        return result.stdout if result.returncode == 0 else None

    def clear_stale_locks(self, cwd: str | None = None) -> None:
        locks = ["index.lock", "HEAD.lock"]
        ref = self.text("rev-parse", "--symbolic-full-name", "HEAD", cwd=cwd).strip()
        if ref.startswith("refs/"):
            locks.append(ref + ".lock")
        for name in locks:
            try:
                os.remove(self.git_path(name, cwd=cwd))
            except OSError:
                pass

    def checkout_or_create_branch(self, branch: str) -> None:
        if self.branch_exists(branch):
            self.text("checkout", "-q", branch)
        else:
            self.text("checkout", "-q", "-b", branch)

    def stage_run_work(self, pre_untracked: set[str], exclude_paths: set[str] | None = None) -> bool:
        exclude_paths = {path.strip("/") for path in (exclude_paths or set()) if path}

        def excluded(path: str) -> bool:
            path = path.strip("/")
            return (
                path == "INBOX"
                or path.startswith("INBOX/")
                or path == ".lute/quarantine"
                or path.startswith(".lute/quarantine/")
                or any(path == ex or path.startswith(ex + "/") for ex in exclude_paths)
            )

        self.reset_index()
        excludes = [":(exclude)INBOX", ":(exclude).lute/quarantine"]
        excludes.extend(f":(exclude,literal){path}" for path in sorted(exclude_paths))
        self.text("add", "-u", "--", ".", *excludes)
        for path in sorted(self.untracked() - pre_untracked):
            if not excluded(path):
                self.text("add", "--", f":(literal){path}")
        if exclude_paths:
            self.ok("reset", "-q", "HEAD", "--", *(f":(literal){path}" for path in sorted(exclude_paths)))
        return bool(self.text("diff", "--cached", "--name-only").strip())

    def reset_mixed(self, ref: str, cwd: str | None = None) -> None:
        self.text("reset", "-q", "--mixed", ref, cwd=cwd)

    def force_branch(self, branch: str, ref: str, cwd: str | None = None) -> None:
        self.text("branch", "-f", branch, ref, cwd=cwd)

    def restore_path(self, ref: str, path: str, cwd: str | None = None) -> bool:
        return self.ok("checkout", "-q", ref, "--", f":(literal){path}", cwd=cwd)

    def commit(self, message: str, *, allow_empty: bool = False) -> None:
        args = ["commit", "-q", "--no-verify"]
        if allow_empty:
            args.append("--allow-empty")
        args += ["-m", message]
        self.text(*args)

    def merge(self, *args: str, cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
        return self.run("merge", *args, cwd=cwd, check=check)

    def worktree_add(self, worktree: str, *args: str) -> None:
        self.text("worktree", "add", "-q", worktree, *args)

    def worktree_prune(self) -> None:
        self.ok("worktree", "prune")

    def worktree_remove(self, worktree: str) -> None:
        self.run("worktree", "remove", "--force", worktree, check=False)

    def delete_branch(self, branch: str) -> None:
        self.ok("branch", "-D", branch)
