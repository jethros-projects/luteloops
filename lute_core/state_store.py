"""Runner-owned state safety.

State under `.lute/` and `INBOX/` is owned by the runner, not by an agent.
Every write here first repairs the path shape so a worker cannot redirect a
ledger/event/card/log write through a symlink or crash the runner by deleting
the state tree.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import fcntl

from .context import Paths

O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


@dataclass(frozen=True)
class FileSnapshot:
    raw: bytes | None


class StateStore:
    def __init__(self, paths: Paths):
        self.paths = paths

    def ensure_layout(self) -> None:
        self.ensure_dir(self.paths.state)
        self.ensure_dir(self.paths.logs)
        self.ensure_dir(self.paths.journal)
        self.ensure_dir(self.paths.inbox)
        self.ensure_dir(self.paths.worktrees)
        self.ensure_dir(self.paths.quarantine)
        self.ensure_parent(self.paths.ledger)
        self.ensure_parent(self.paths.events)
        self.ensure_parent(self.paths.config)
        self.ensure_parent(self.paths.lock)

    @contextmanager
    def locked(self):
        self.ensure_dir(self.paths.state)
        path = os.path.join(self.paths.state, "state.lock")
        self.ensure_regular_file(path)
        flags = os.O_RDWR | os.O_CREAT
        if O_NOFOLLOW:
            flags |= O_NOFOLLOW
        try:
            fd = os.open(path, flags, 0o644)
        except OSError:
            self.replace_non_regular_file(path)
            fd = os.open(path, flags, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def ensure_dir(self, path: str) -> None:
        if os.path.lexists(path):
            try:
                st = os.lstat(path)
            except OSError:
                st = None
            if st is not None and stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode):
                return
            self._remove_path(path)
        os.makedirs(path, exist_ok=True)

    def ensure_parent(self, path: str) -> None:
        self.ensure_dir(os.path.dirname(path))

    def child_path(self, base: str, *parts: str) -> str:
        root = os.path.abspath(base)
        path = os.path.abspath(os.path.join(root, *parts))
        try:
            inside = os.path.commonpath([root, path]) == root
        except ValueError:
            inside = False
        if not inside:
            raise ValueError(f"path escapes {base}: {os.path.join(*parts)}")
        return path

    def snapshot(self, path: str) -> FileSnapshot:
        if not self.is_regular_file(path):
            return FileSnapshot(None)
        with open(path, "rb") as f:
            return FileSnapshot(f.read())

    def restore_if_changed(self, path: str, snapshot: FileSnapshot) -> bool:
        current = self._read_regular_bytes(path)
        if current == snapshot.raw:
            return False
        if snapshot.raw is None:
            self.remove_runner_file(path)
        else:
            self.safe_write_regular(path, snapshot.raw)
        return True

    def safe_write_regular(self, path: str, data: bytes | str) -> None:
        payload = data.encode() if isinstance(data, str) else data
        parent = os.path.dirname(path)
        self.ensure_dir(parent)
        fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", dir=parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            if os.path.lexists(path) and not self.is_regular_file(path):
                self.remove_runner_file(path)
            os.replace(tmp, path)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    def append_jsonl(self, path: str, obj: dict[str, Any]) -> None:
        line = json.dumps(obj) + "\n"
        self.append_text(path, line)

    def append_text(self, path: str, text: str) -> None:
        self.ensure_regular_file(path)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if O_NOFOLLOW:
            flags |= O_NOFOLLOW
        try:
            fd = os.open(path, flags, 0o644)
        except OSError:
            self.replace_non_regular_file(path, b"")
            fd = os.open(path, flags, 0o644)
        try:
            os.write(fd, text.encode())
        finally:
            os.close(fd)

    def read_text(self, path: str, default: str = "") -> str:
        if not self.is_regular_file(path):
            return default
        try:
            with open(path, encoding="utf-8", errors="replace", newline="") as f:
                return f.read()
        except OSError:
            return default

    def ensure_regular_file(self, path: str, default: bytes = b"") -> None:
        self.ensure_parent(path)
        if not os.path.lexists(path):
            self.safe_write_regular(path, default)
            return
        if not self.is_regular_file(path):
            self.replace_non_regular_file(path, default)

    def replace_non_regular_file(self, path: str, default: bytes = b"") -> None:
        if os.path.lexists(path) and self.is_regular_file(path):
            return
        if os.path.lexists(path):
            self.remove_runner_file(path)
        self.safe_write_regular(path, default)

    def remove_runner_file(self, path: str) -> None:
        self.ensure_parent(path)
        try:
            st = os.lstat(path)
        except OSError:
            return
        if stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode):
            self._remove_path(path)
        else:
            try:
                os.remove(path)
            except OSError:
                self._remove_path(path)

    def is_regular_file(self, path: str) -> bool:
        try:
            st = os.lstat(path)
        except OSError:
            return False
        return stat.S_ISREG(st.st_mode) and not stat.S_ISLNK(st.st_mode)

    def is_dir(self, path: str) -> bool:
        try:
            st = os.lstat(path)
        except OSError:
            return False
        return stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode)

    def _read_regular_bytes(self, path: str) -> bytes | None:
        if not self.is_regular_file(path):
            return None
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            return None

    def ensure_capture_ignore(self) -> None:
        ignore = os.path.join(self.paths.state, ".gitignore")
        need = ("logs/", "events.jsonl", "wt/", "quarantine/", "lock*")
        have = []
        if self.is_regular_file(ignore):
            with open(ignore, encoding="utf-8") as f:
                have = f.read().splitlines()
        missing = [entry for entry in need if entry not in have]
        if missing:
            self.safe_write_regular(ignore, "\n".join([*have, *missing]) + "\n")

    def _remove_path(self, path: str) -> None:
        if not os.path.lexists(path):
            return
        try:
            st = os.lstat(path)
        except OSError:
            return
        if stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode):
            for root, dirs, files in os.walk(path, topdown=False, followlinks=False):
                for name in files:
                    try:
                        os.remove(os.path.join(root, name))
                    except OSError:
                        pass
                for name in dirs:
                    self._remove_path(os.path.join(root, name))
            try:
                os.rmdir(path)
            except OSError:
                pass
        else:
            try:
                os.remove(path)
            except OSError:
                pass
