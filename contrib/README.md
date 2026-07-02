# contrib — worked material around the runner

Nothing in here is required by lute, and nothing in lute depends on it. Each
piece is a worked example you can copy, adapt, or delete.

| what | why |
|---|---|
| [`demo-catch-cheat.sh`](demo-catch-cheat.sh) | hermetic showcase: a scripted agent deletes the failing test, lute quarantines the edit and refuses the pass, then an honest fix closes the same exam |
| [`demo.sh`](demo.sh) | live showcase: a real codex agent fixes a rigged calculator while nested exams close bottom-up |
| [`recipes/`](recipes/) | ready-to-adapt manifests for verified upgrades — React major, a bot's dependency bump, Django major, Node LTS ([recipes/README.md](recipes/README.md)) |
| [`action/`](action/) | the `lute-verify` composite GitHub Action: run a manifest in CI, map lute's exit contract onto the job, upload logs + events + attestation |
| [`cage/`](cage/) | a worked agent-cage Dockerfile and its README (the cage contract lives in the main README and [THREAT_MODEL.md](../THREAT_MODEL.md)) |
| [`lute.sh`](lute.sh) | the thirty-line bash kernel — passes the suite's first two notches, and is the honest answer to "isn't this just a bash loop?" ([docs/COMPARED.md](../docs/COMPARED.md)) |
| [`loop-architecture-visualization.svg`](loop-architecture-visualization.svg) | one picture of the loop: check → work → verify → commit → repeat → escalate |
