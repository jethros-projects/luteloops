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

def looks_like_container_runtime(cage: Any) -> bool:
    if cage == "docker":
        return True
    if not isinstance(cage, str):
        return False
    return any(_command_invokes_container(command) for command in _simple_commands(cage))


def _simple_commands(template: str) -> list[list[str]]:
    try:
        lexer = shlex.shlex(template, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []
    commands, current = [], []
    for token in tokens:
        if token in {";", "&", "&&", "|", "||", "(", ")"}:
            if current:
                commands.append(current)
                current = []
        else:
            current.append(token)
    if current:
        commands.append(current)
    return commands


def _command_invokes_container(command: list[str]) -> bool:
    i = 0
    while i < len(command) and re.match(r"[A-Za-z_][A-Za-z0-9_]*=", command[i]):
        i += 1
    if i >= len(command):
        return False
    name = os.path.basename(command[i])
    if name in {"env", "sudo", "command", "exec"}:
        return _command_invokes_container(command[i + 1 :])
    if name in {"sh", "bash", "dash", "zsh"}:
        for j, token in enumerate(command[i + 1 :], i + 1):
            if "c" in token.lstrip("-") and j + 1 < len(command):
                return looks_like_container_runtime(command[j + 1])
    return name in {"docker", "podman"} and (
        (i + 1 < len(command) and command[i + 1] == "run")
        or (i + 2 < len(command) and command[i + 1] == "container" and command[i + 2] == "run")
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
