"""Cage command template expansion."""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from typing import Any

DEFAULT_CAGE_TEMPLATE = (
    "docker run --rm -i -v {repo}:/work -w /work --pids-limit 256 "
    "--memory 2g {mounts} {image} sh -lc {cmd}"
)


@dataclass(frozen=True)
class CageTemplate:
    template: str

    def expand(self, repo: str, image: str, mounts: list[str], cmd: str) -> str:
        if "{cmd}" not in self.template:
            raise ValueError(f"cage template must contain {{cmd}}: {self.template!r}")
        mount_args = " ".join("-v " + quote_volume(p, p, "ro") for p in mounts)
        substitutions = {
            "repo": shlex.quote(repo),
            "image": shlex.quote(image),
            "mounts": mount_args,
            "cmd": shlex.quote(cmd),
        }
        return re.sub(r"\{(repo|image|mounts|cmd)\}", lambda m: substitutions[m.group(1)], self.template)


def quote_volume(source: str, target: str, mode: str) -> str:
    return f"{shlex.quote(source)}:{shlex.quote(target)}:{mode}"


def normalize_mounts(raw: Any) -> list[str]:
    mounts = raw or []
    if not isinstance(mounts, list):
        mounts = [mounts]
    return [os.path.abspath(os.path.expanduser(str(m))) for m in mounts]


def expand_cage_template(
    template: str,
    repo: str,
    image: str,
    mounts: Any,
    cmd: str,
) -> str:
    return CageTemplate(template).expand(repo, image, normalize_mounts(mounts), cmd)

