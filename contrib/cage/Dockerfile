# contrib/cage/Dockerfile - a sample cage image for `cage: docker`.
#
# The cage isolates by absence: lute mounts only your repo (read-write at
# /work) and whatever `cage_mounts` names (read-only). Nothing else of the host
# - not ~/.ssh, not your environment - is visible. So the one rule is:
#
#     your cage image MUST contain your agent CLI.
#
# This sample carries the OpenAI Codex CLI as one example agent.
# Build it, point .lute/config.yaml at it, and the agent runs blind to the host:
#
#     docker build -t lute-codex-cage contrib/cage
#
#     # .lute/config.yaml
#     cage: docker
#     cage_image: lute-codex-cage
#     cage_mounts:
#       - "~/.codex"        # auth - read-only, by name, never implicit
#     # codex reads auth from $CODEX_HOME; cage_mounts lands ~/.codex at its own
#     # host path inside the cage, so point CODEX_HOME there:
#     agent: "CODEX_HOME=/absolute/path/to/.codex codex exec --sandbox workspace-write"
#
# See README.md in this directory for the full walkthrough and the initial release boundary.

FROM node:20-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN npm install -g @openai/codex

WORKDIR /work
