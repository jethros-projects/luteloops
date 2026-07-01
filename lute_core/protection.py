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


def reaches_below(pattern: str, rel: str) -> bool:
    """Could the glob match somewhere BENEATH rel/? A symlinked directory hides
    its subtree from both the worktree walk and ls-tree name-matching, so a
    symlink the glob can reach below can conceal protected content and must be
    watched like protected content itself."""
    def rec(pseg: list[str], rseg: list[str]) -> bool:
        if not rseg:
            return bool(pseg)  # pattern has segments left to match beneath rel
        if not pseg:
            return False
        if "**" in pseg[0]:
            return True  # ** crosses directories: anything beneath is reachable
        return bool(glob_re(pseg[0]).match(rseg[0])) and rec(pseg[1:], rseg[1:])

    return rec(pattern.split("/"), rel.split("/"))


def protected_files(globs: list[str], boundaries: frozenset[str] | set[str] = frozenset()) -> list[str]:
    matchers = [glob_re(g) for g in globs]
    files: list[str] = []
    for root, dirs, names in os.walk("."):
        kept_dirs: list[str] = []
        for name in dirs:
            if name in (".git", ".lute"):
                continue
            rel = os.path.relpath(os.path.join(root, name), ".")
            if rel in boundaries:
                # A recorded submodule: a separate repository, policed at its
                # gitlink. Only baseline-recorded paths are boundaries — a dir
                # the agent `git init`s itself is still walked and watched.
                continue
            if os.path.islink(os.path.join(root, name)):
                if any(m.match(rel) for m in matchers) or any(reaches_below(g, rel) for g in globs):
                    files.append(rel)
                continue
            kept_dirs.append(name)
        dirs[:] = kept_dirs
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


def _lstree_entry(meta_b: bytes) -> tuple[str, int, str]:
    mode_s, git_type, object_id = meta_b.decode("ascii", "replace").split()[:3]
    full_mode = int(mode_s, 8)
    return _kind_from_mode(full_mode, git_type), full_mode, object_id


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


def _symlink_ancestor(path: str) -> str | None:
    cur = ""
    for part in os.path.normpath(path).split(os.sep)[:-1]:
        if part in ("", "."):
            continue
        cur = os.path.join(cur, part) if cur else part
        if os.path.islink(cur):
            return cur
    return None


def _submodule_git(path: str, *args: str) -> subprocess.CompletedProcess | None:
    # We read git metadata inside a directory the agent controls, so we neutralize
    # every knob that could run agent code (hooks, fsmonitor, external diff/pager)
    # and scrub the GIT_* env that could redirect git at another repo.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    cmd = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=", "-c", "core.pager=cat",
           "-C", path, *args]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    except OSError:
        return None


def _submodule_head(path: str) -> str | None:
    """The commit a checked-out submodule points at — but only when its working
    tree is clean at that commit (no modified or deleted TRACKED files), so a
    gitdir-pointer forgery that keeps HEAD at the recorded commit while
    overwriting an exam file is a change, not a pristine boundary. None means
    'not a clean submodule here' — flag it. (An added UNTRACKED file the check
    reads is the residual noted in THREAT_MODEL.md; untracked is ignored so a
    legitimate checkout's build artifacts do not false-flag.)"""
    head = _submodule_git(path, "rev-parse", "HEAD")
    if head is None or head.returncode:
        return None
    clean = _submodule_git(path, "--no-optional-locks", "diff", "--no-ext-diff", "--no-textconv", "--quiet", "HEAD")
    if clean is None or clean.returncode:
        return None
    return head.stdout.strip()


def _dir_is_empty(path: str) -> bool:
    try:
        return not os.listdir(path)
    except OSError:
        return False  # unreadable: treat as non-empty, i.e. a change (fail closed)


def _current_record(path: str) -> FileRecord | None:
    if ancestor := _symlink_ancestor(path):
        raw = f"{ancestor}->{os.readlink(ancestor)}".encode()
        return FileRecord(path, "symlink-ancestor", 0o777, _sha(raw), "worktree", raw)
    try:
        st = os.lstat(path)
    except OSError:
        return None
    mode = stat.S_IMODE(st.st_mode)
    if stat.S_ISLNK(st.st_mode):
        raw = os.readlink(path).encode()
        return FileRecord(path, "symlink", 0o777, _sha(raw), "worktree", raw)
    if stat.S_ISREG(st.st_mode):
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError:
            # An unreadable watched file (chmod 000) is a change, not a crash:
            # the mismatch quarantines it and restores a readable trusted copy.
            raw = b"<unreadable>"
            return FileRecord(path, "unreadable", mode, _sha(raw), "worktree", raw)
        return FileRecord(path, "file", mode, _sha(raw), "worktree", raw)
    if stat.S_ISDIR(st.st_mode):
        # A submodule mounts as a directory. If it is a checkout, its identity is
        # the commit it points at; an empty directory is an uninitialized one.
        # Anything else is a plain directory — content, not a boundary.
        if os.path.lexists(os.path.join(path, ".git")) and (head := _submodule_head(path)):
            return FileRecord(path, "gitlink", 0o160000, head, "worktree")
        if _dir_is_empty(path):
            return FileRecord(path, "empty-dir", mode, _sha(b"<empty-directory>"), "worktree")
        raw = b"<directory>"
        return FileRecord(path, "dir", mode, _sha(raw), "worktree", raw)
    raw = f"<mode:{st.st_mode}>".encode()
    return FileRecord(path, "other", mode, _sha(raw), "worktree", raw)


def _same(a: FileRecord | None, b: FileRecord | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if a.kind == "gitlink":
        # A submodule is a boundary, not content. It is unchanged only when the
        # worktree still presents that submodule at the recorded commit (or an
        # uninitialized, empty mount). A plain directory of agent-authored files
        # in its place is a change — restored, so content smuggled behind the
        # boundary cannot buy a pass. (A real checkout at the recorded commit
        # with a dirty tree is the residual noted in THREAT_MODEL.md.)
        return (b.kind == "gitlink" and a.digest == b.digest) or b.kind == "empty-dir"
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
            real_manifest = self.repo_relative_control_path(os.path.realpath(self.ctx.manifest_path))
            if real_manifest:
                paths.append(real_manifest)
        config = self.repo_relative_control_path(self.ctx.paths.config)
        if config:
            paths.append(config)
        real_config = self.repo_relative_control_path(os.path.realpath(self.ctx.paths.config))
        if real_config:
            paths.append(real_config)
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
        for entry in self.git.run("ls-tree", "-rz", ref, text=False).stdout.split(b"\0"):
            if not entry:
                continue
            meta_b, sep, path_b = entry.partition(b"\t")
            path = os.fsdecode(path_b)
            if not path:
                continue
            kind, full_mode, object_id = _lstree_entry(meta_b)
            # A symlink or submodule the glob can reach BELOW is watched even
            # when its own name doesn't match: the walk cannot see past it (a
            # symlink hides its target, a submodule its repository), so recording
            # it keeps walk and baseline symmetric and marks it a boundary.
            watched = any(m.match(path) for m in matchers) or (
                kind in ("symlink", "gitlink") and any(reaches_below(g, path) for g in globs)
            )
            if watched:
                out.append((path, self._git_record(path, kind, full_mode, object_id)))
        return out

    def git_record_at(self, ref: str, path: str) -> FileRecord | None:
        raw_entry = self.git.run("ls-tree", "-z", ref, "--", f":(literal){path}", text=False).stdout.rstrip(b"\0")
        if not raw_entry:
            return None
        meta_b, _, _ = raw_entry.partition(b"\t")
        return self._git_record(path, *_lstree_entry(meta_b))

    def _git_record(self, path: str, kind: str, full_mode: int, object_id: str) -> FileRecord:
        raw = self.git.object_bytes(object_id)
        digest = object_id if raw is None else _sha(raw)
        return FileRecord(path, kind, _git_record_mode(full_mode, kind), digest, "git", raw)

    def changed_paths(self, baseline: ProtectionBaseline) -> list[str]:
        watched = set(baseline.records) | set(baseline.watched_absent) | set(baseline.control_paths)
        if baseline.protected_globs:
            boundaries = {path for path, record in baseline.records.items() if record.kind == "gitlink"}
            watched |= set(protected_files(list(baseline.protected_globs), boundaries))
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
        safe_paths = [path for path in paths if not _symlink_ancestor(path)]
        safe_specs = [f":(literal){path}" for path in safe_paths]
        unsafe_paths = sorted(set(paths) - set(safe_paths))
        if safe_paths:
            result = self.git.run(
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--binary",
                baseline.base_ref,
                "--",
                *safe_specs,
                check=False,
                text=False,
            )
            chunks.append(result.stdout)
        for path in safe_paths:
            if path in baseline.records or self.git.ok("ls-files", "--error-unmatch", "--", f":(literal){path}") or not os.path.lexists(path):
                continue
            result = self.git.run(
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--binary",
                "--no-index",
                "--",
                "/dev/null",
                path,
                check=False,
                text=False,
            )
            if result.stdout:
                chunks.append(result.stdout)
        if unsafe_paths:
            chunks.append(("diff omitted for symlink-ancestor path(s): " + ", ".join(unsafe_paths) + "\n").encode())
        return b"\n".join(chunk for chunk in chunks if chunk)

    def copy_current(self, paths: list[str], files_dir: str) -> list[str]:
        saved: list[str] = []
        for path in paths:
            if not os.path.lexists(path):
                continue
            if ancestor := _symlink_ancestor(path):
                dest = os.path.join(files_dir, ancestor)
                self.store.ensure_dir(os.path.dirname(dest))
                try:
                    os.symlink(os.readlink(ancestor), dest)
                    saved.append(ancestor)
                except OSError:
                    pass
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
                if record.kind == "gitlink":
                    os.makedirs(path, exist_ok=True)  # an empty dir = an uninitialized submodule
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
        if ancestor := _symlink_ancestor(path):
            path = ancestor
        if not os.path.lexists(path):
            return
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
            return
        try:
            os.remove(path)
        except OSError:
            pass
