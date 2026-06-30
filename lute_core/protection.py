"""Trusted exam protection and quarantine.

Lute lets agents edit freely, but protected exam material and runner control
files must not redefine what "done" means.  This module snapshots trusted
material, saves attempted edits for inspection, restores trusted copies, and
lets checks run against the restored exam.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass, asdict
from typing import Any

from .context import AppContext
from .git_repo import GitRepo
from .state_store import StateStore


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


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _kind_from_mode(mode: int, git_type: str = "blob") -> str:
    if mode == 0o120000:
        return "symlink"
    if mode == 0o160000 or git_type == "commit":
        return "gitlink"
    return "file"


def _git_record_mode(mode: int, kind: str) -> int:
    if kind == "file":
        return 0o755 if mode & 0o111 else 0o644
    if kind == "symlink":
        return 0o777
    return mode


@dataclass(frozen=True)
class FileRecord:
    path: str
    kind: str
    mode: int
    digest: str
    source: str
    raw: bytes | None = None

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw", None)
        return data


@dataclass(frozen=True)
class ProtectionBaseline:
    base_ref: str
    protected_globs: tuple[str, ...]
    control_paths: tuple[str, ...]
    records: dict[str, FileRecord]
    watched_absent: tuple[str, ...]


@dataclass(frozen=True)
class QuarantineResult:
    qid: str
    paths: tuple[str, ...]
    patch_path: str
    meta_path: str


def _rel(path: str) -> str | None:
    try:
        rel = os.path.relpath(path)
    except ValueError:
        return None
    return None if rel == ".." or rel.startswith(".." + os.sep) else rel


def _current_record(path: str) -> FileRecord | None:
    try:
        st = os.lstat(path)
    except OSError:
        return None
    mode = stat.S_IMODE(st.st_mode)
    if stat.S_ISLNK(st.st_mode):
        raw = os.readlink(path).encode()
        return FileRecord(path, "symlink", 0o777, _sha(raw), "worktree", raw)
    if stat.S_ISREG(st.st_mode):
        with open(path, "rb") as f:
            raw = f.read()
        return FileRecord(path, "file", mode, _sha(raw), "worktree", raw)
    if stat.S_ISDIR(st.st_mode):
        raw = b"<directory>"
        return FileRecord(path, "dir", mode, _sha(raw), "worktree", raw)
    raw = f"<mode:{st.st_mode}>".encode()
    return FileRecord(path, "other", mode, _sha(raw), "worktree", raw)


def _same(a: FileRecord | None, b: FileRecord | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return (a.kind, a.mode, a.digest) == (b.kind, b.mode, b.digest)


class Protection:
    def __init__(self, ctx: AppContext, git: GitRepo):
        self.ctx = ctx
        self.git = git
        self.store = StateStore(ctx.paths)

    @property
    def quarantine_root(self) -> str:
        return getattr(self.ctx.paths, "quarantine", os.path.join(self.ctx.paths.state, "quarantine"))

    def baseline(self, loop) -> ProtectionBaseline:
        base_ref = self.ctx.trusted_base or self.git.branch_base()
        control_paths = self.control_paths()
        records: dict[str, FileRecord] = {}
        watched_absent: set[str] = set(control_paths)

        for path, rec in self.git_records_at(base_ref, list(loop.protected or ())):
            records[path] = rec

        for path in control_paths:
            git_rec = self.git_record_at(base_ref, path)
            if git_rec is not None:
                records[path] = git_rec
                watched_absent.discard(path)
                continue
            live = _current_record(path)
            if live is not None:
                records[path] = FileRecord(path, live.kind, live.mode, live.digest, "live", live.raw)
                watched_absent.discard(path)

        return ProtectionBaseline(
            base_ref=base_ref,
            protected_globs=tuple(loop.protected or ()),
            control_paths=tuple(control_paths),
            records=records,
            watched_absent=tuple(sorted(watched_absent)),
        )

    def control_paths(self) -> list[str]:
        paths: list[str] = []
        if self.ctx.manifest_path:
            manifest = self.repo_relative_control_path(self.ctx.manifest_path)
            if manifest:
                paths.append(manifest)
        config = self.repo_relative_control_path(self.ctx.paths.config)
        if config:
            paths.append(config)
        return sorted(set(paths))

    def repo_relative_control_path(self, path: str) -> str | None:
        rel = _rel(path)
        if rel:
            return rel
        try:
            shared_rel = os.path.relpath(path, self.ctx.shared_root)
        except ValueError:
            return None
        if shared_rel.startswith(".."):
            return None
        candidate = os.path.join(self.git.root, shared_rel)
        return os.path.relpath(candidate, self.git.root)

    def git_records_at(self, ref: str, globs: list[str]) -> list[tuple[str, FileRecord]]:
        if not globs:
            return []
        matchers = [glob_re(glob) for glob in globs]
        out: list[tuple[str, FileRecord]] = []
        for line in self.git.text("ls-tree", "-r", ref).splitlines():
            meta, _, path = line.partition("\t")
            if not path or not any(m.match(path) for m in matchers):
                continue
            mode_s, git_type, object_id = meta.split()[:3]
            full_mode = int(mode_s, 8)
            kind = _kind_from_mode(full_mode, git_type)
            mode = _git_record_mode(full_mode, kind)
            raw = self.git.show_bytes(f"{ref}:{path}")
            digest = object_id if raw is None else _sha(raw)
            out.append((path, FileRecord(path, kind, mode, digest, "git", raw)))
        return out

    def git_record_at(self, ref: str, path: str) -> FileRecord | None:
        line = self.git.text("ls-tree", ref, "--", path).strip()
        if not line:
            return None
        meta, _, _ = line.partition("\t")
        mode_s, git_type, object_id = meta.split()[:3]
        full_mode = int(mode_s, 8)
        kind = _kind_from_mode(full_mode, git_type)
        mode = _git_record_mode(full_mode, kind)
        raw = self.git.show_bytes(f"{ref}:{path}")
        digest = object_id if raw is None else _sha(raw)
        return FileRecord(path, kind, mode, digest, "git", raw)

    def changed_paths(self, baseline: ProtectionBaseline) -> list[str]:
        watched = set(baseline.records) | set(baseline.watched_absent) | set(baseline.control_paths)
        if baseline.protected_globs:
            watched |= set(protected_files(list(baseline.protected_globs)))
        changed = [path for path in watched if not _same(baseline.records.get(path), _current_record(path))]
        return sorted(changed)

    def changed_paths_at_ref(self, baseline: ProtectionBaseline, ref: str) -> list[str]:
        records = dict(self.git_records_at(ref, list(baseline.protected_globs)))
        for path in baseline.control_paths:
            rec = self.git_record_at(ref, path)
            if rec is not None:
                records[path] = rec
        watched = set(baseline.records) | set(baseline.watched_absent) | set(baseline.control_paths) | set(records)
        return sorted(path for path in watched if not _same(baseline.records.get(path), records.get(path)))

    def enforce(self, loop_id: str, run_id: str, baseline: ProtectionBaseline) -> QuarantineResult | None:
        self.git.reset_index()
        paths = self.changed_paths(baseline)
        if not paths:
            return None
        qid = self.unique_qid(f"{loop_id}.{run_id}")
        self.store.ensure_dir(self.quarantine_root)
        qdir = os.path.join(self.quarantine_root, qid)
        self.store.ensure_dir(qdir)
        patch_path = os.path.join(qdir, "changes.patch")
        meta_path = os.path.join(qdir, "meta.json")
        files_dir = os.path.join(qdir, "files")
        self.store.ensure_dir(files_dir)

        patch = self.patch_for(paths, baseline)
        self.store.safe_write_regular(patch_path, patch)
        saved_files = self.copy_current(paths, files_dir)
        meta = {
            "id": qid,
            "loop": loop_id,
            "run": run_id,
            "base": baseline.base_ref,
            "paths": paths,
            "records": {p: baseline.records[p].public() for p in sorted(baseline.records) if p in paths},
            "watched_absent": [p for p in baseline.watched_absent if p in paths],
            "saved_files": saved_files,
            "patch": patch_path,
        }
        self.store.safe_write_regular(meta_path, json.dumps(meta, indent=2, sort_keys=True) + "\n")

        self.restore(paths, baseline)
        return QuarantineResult(qid, tuple(paths), patch_path, meta_path)

    def unique_qid(self, stem: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", stem).strip("-") or "quarantine"
        qid = safe
        i = 2
        while os.path.exists(os.path.join(self.quarantine_root, qid)):
            qid = f"{safe}.{i}"
            i += 1
        return qid

    def patch_for(self, paths: list[str], baseline: ProtectionBaseline) -> bytes:
        chunks: list[bytes] = []
        if paths:
            result = subprocess.run(
                ["git", "-C", self.git.root, "diff", "--binary", baseline.base_ref, "--", *paths],
                capture_output=True,
            )
            chunks.append(result.stdout)
        for path in paths:
            if path in baseline.records or self.git.ok("ls-files", "--error-unmatch", "--", path) or not os.path.lexists(path):
                continue
            result = subprocess.run(
                ["git", "-C", self.git.root, "diff", "--binary", "--no-index", "--", "/dev/null", path],
                capture_output=True,
            )
            if result.stdout:
                chunks.append(result.stdout)
        return b"\n".join(chunk for chunk in chunks if chunk)

    def copy_current(self, paths: list[str], files_dir: str) -> list[str]:
        saved: list[str] = []
        for path in paths:
            if not os.path.lexists(path):
                continue
            dest = os.path.join(files_dir, path)
            self.store.ensure_dir(os.path.dirname(dest))
            try:
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.copytree(path, dest, symlinks=True, dirs_exist_ok=True)
                elif os.path.islink(path):
                    os.symlink(os.readlink(path), dest)
                else:
                    shutil.copy2(path, dest, follow_symlinks=False)
                saved.append(path)
            except OSError:
                pass
        return saved

    def restore(self, paths: list[str], baseline: ProtectionBaseline) -> None:
        for path in paths:
            self.remove_path(path)
            record = baseline.records.get(path)
            if record is None:
                continue
            if record.source == "git":
                self.git.restore_path(baseline.base_ref, path)
            elif record.kind == "symlink" and record.raw is not None:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                os.symlink(record.raw.decode(), path)
            elif record.kind == "file" and record.raw is not None:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "wb") as f:
                    f.write(record.raw)
                try:
                    os.chmod(path, record.mode)
                except OSError:
                    pass
        self.git.reset_index()

    def remove_path(self, path: str) -> None:
        if not os.path.lexists(path):
            return
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
            return
        try:
            os.remove(path)
        except OSError:
            pass
