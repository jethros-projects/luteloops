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
                fd, tmp = tempfile.mkstemp(dir=directory)
                try:
                    os.write(fd, os.urandom(16).hex().encode())
                    os.fsync(fd)
                finally:
                    os.close(fd)
                try:
                    os.link(tmp, path)
                except FileExistsError:
                    pass
                finally:
                    os.remove(tmp)
            try:
                fd = os.open(path, os.O_RDONLY | (getattr(os, "O_NOFOLLOW", 0)))
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
