"""Configuration loading, freezing, and answer authority."""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Any

import yaml

from .context import AppContext
from .git_repo import GitRepo


def load_config(path: str) -> dict[str, Any]:
    if os.path.exists(path):
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
            if not os.path.exists(path):
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    os.write(fd, os.urandom(16).hex().encode())
                finally:
                    os.close(fd)
            with open(path, encoding="utf-8") as f:
                self._key = f.read().strip()
        return self._key

    def token(self, loop_id: str, basis: str) -> str:
        return hmac.new(self.key().encode(), f"{loop_id}\n{basis}".encode(), hashlib.sha256).hexdigest()[:24]

    def valid(self, loop_id: str, basis: str, token: str | None) -> bool:
        return bool(token) and hmac.compare_digest(token, self.token(loop_id, basis))
