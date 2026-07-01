"""Configuration loading, freezing, and answer authority."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from typing import Any

import yaml

from .context import AppContext
from .errors import PreconditionError
from .git_repo import GitRepo


def load_config(path: str) -> dict[str, Any]:
    try:
        st = os.lstat(path)
    except OSError:
        return {}
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise PreconditionError(f"{path} must be a regular file, not a symlink or directory")
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if isinstance(cfg, dict):
        return cfg
    return {}


def freeze_config(ctx: AppContext, git: GitRepo) -> dict[str, Any]:
    rel = os.path.relpath(os.path.realpath(ctx.paths.config), os.path.realpath(ctx.shared_root))
    raw = None if rel.startswith("..") else git.show_bytes(f"{ctx.trusted_base or git.branch_base()}:{rel}")
    if raw is not None:
        try:
            cfg = yaml.safe_load(raw)
            ctx.frozen_config = cfg if isinstance(cfg, dict) else {}
        except (yaml.YAMLError, ValueError):
            ctx.frozen_config = {}
    else:
        ctx.frozen_config = dict(ctx.config)
    return ctx.frozen_config


@dataclass
class AnswerAuthority:
    ctx: AppContext
    _key: str | None = None

    def key(self) -> str:
        if self._key is None:
            directory = os.environ.get("LUTE_KEY_DIR") or os.path.join(os.path.expanduser("~"), ".lute", "keys")
            os.makedirs(directory, exist_ok=True)
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass
            ident = os.path.realpath(self.ctx.shared_root)
            path = os.path.join(directory, hashlib.sha256(ident.encode()).hexdigest()[:16] + ".key")
            if not os.path.lexists(path):
                # Publish by hard link, not replace: the link is atomic (no reader
                # ever sees a partial key) and exclusive (the first creator wins,
                # so every process converges on one key and cached copies never
                # diverge from the file).
                secret = os.urandom(16).hex().encode()
                fd, tmp = tempfile.mkstemp(dir=directory)
                try:
                    os.write(fd, secret)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                try:
                    os.link(tmp, path)
                except FileExistsError:
                    pass
                except OSError:
                    # A filesystem without hard links (FAT/exFAT, some SMB/NFS):
                    # claim the path with O_EXCL instead of a clobbering replace.
                    # O_EXCL is the exclusive primitive here too, so exactly one
                    # creator wins and every other process reads its key — no
                    # divergent keys under a concurrent first run.
                    try:
                        kfd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    except FileExistsError:
                        pass
                    else:
                        try:
                            os.write(kfd, secret)
                            os.fsync(kfd)
                        finally:
                            os.close(kfd)
                finally:
                    if os.path.lexists(tmp):
                        os.remove(tmp)
            # O_NONBLOCK so a non-regular node (a planted FIFO) cannot make this
            # open() block waiting for a writer; fstat below then rejects it.
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | (getattr(os, "O_NOFOLLOW", 0)))
            except OSError as exc:
                raise PreconditionError(f"{path} must be a regular key file") from exc
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise PreconditionError(f"{path} must be a regular key file")
                key = os.read(fd, 64).decode("ascii", "replace").strip()
            finally:
                os.close(fd)
            if not re.fullmatch(r"[0-9a-f]{32}", key):
                raise PreconditionError(f"answer-auth key at {path} is malformed; delete it to regenerate")
            self._key = key
        return self._key

    def token(self, loop_id: str, basis: str) -> str:
        return hmac.new(self.key().encode(), f"{loop_id}\n{basis}".encode(), hashlib.sha256).hexdigest()[:24]

    def valid(self, loop_id: str, basis: str, token: str | None) -> bool:
        return bool(token) and hmac.compare_digest(token, self.token(loop_id, basis))
