"""Git adapter for lute.

All production git subprocess usage lives here so higher-level modules talk in
repository operations rather than command strings.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass

from .errors import GitError, PreconditionError


@dataclass
class GitRepo:
    root: str

    @classmethod
    def discover(cls) -> "GitRepo":
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        if result.returncode:
            raise PreconditionError("not a git repository; run: git init   (lute keeps all state in git)")
        return cls(result.stdout.strip())

    def run(self, *args: str, cwd: str | None = None, check: bool = True, text: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(["git", "-C", cwd or self.root, *args], capture_output=True, text=text)
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
            result = subprocess.run(["git", "-C", shared_root, *args], capture_output=True, text=True)
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

    def untracked(self, cwd: str | None = None) -> set[str]:
        return {line[3:] for line in self.status_porcelain(cwd=cwd).splitlines() if line.startswith("?? ")}

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
        result = subprocess.run(["git", "-C", cwd or self.root, "show", ref_path], capture_output=True)
        return result.stdout if result.returncode == 0 else None

    def show_text(self, ref_path: str, cwd: str | None = None) -> str | None:
        raw = self.show_bytes(ref_path, cwd=cwd)
        return raw.decode("utf-8", "replace") if raw is not None else None

    def ls_tree_files(self, ref: str, cwd: str | None = None) -> list[str]:
        out = self.text("ls-tree", "-r", "--name-only", ref, cwd=cwd)
        return out.splitlines()

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
        excludes.extend(f":(exclude){path}" for path in sorted(exclude_paths))
        self.text("add", "-u", "--", ".", *excludes)
        for path in sorted(self.untracked() - pre_untracked):
            if not excluded(path):
                self.text("add", "--", path)
        if exclude_paths:
            self.ok("reset", "-q", "HEAD", "--", *sorted(exclude_paths))
        return bool(self.text("diff", "--cached", "--name-only").strip())

    def reset_mixed(self, ref: str, cwd: str | None = None) -> None:
        self.text("reset", "-q", "--mixed", ref, cwd=cwd)

    def force_branch(self, branch: str, ref: str, cwd: str | None = None) -> None:
        self.text("branch", "-f", branch, ref, cwd=cwd)

    def restore_path(self, ref: str, path: str, cwd: str | None = None) -> bool:
        return self.ok("checkout", "-q", ref, "--", path, cwd=cwd)

    def restore_paths_from_ref(self, ref: str, paths: list[str], cwd: str | None = None) -> None:
        if paths:
            self.text("checkout", "-q", ref, "--", *paths, cwd=cwd)

    def commit(self, message: str, *, allow_empty: bool = False) -> None:
        args = ["commit", "-q"]
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
        subprocess.run(["git", "-C", self.root, "worktree", "remove", "--force", worktree], capture_output=True)

    def delete_branch(self, branch: str) -> None:
        self.ok("branch", "-D", branch)
