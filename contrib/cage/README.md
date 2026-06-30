# The cage - a sample image

`cage: docker` runs every command lute spawns **on behalf of a model** - the
per-loop agents and any `judge:` command - inside a container. `done_when`
checks never enter the cage; they are yours and run on the host. The container
sees only your repo (read-write at `/work`) and the host paths you name in
`cage_mounts` (read-only). Everything else - `~/.ssh`, your shell environment,
the rest of your disk - simply isn't there. Isolation is **by absence**.

## The one rule

**Your cage image must contain your agent CLI.** lute does not install anything
into the container; it only mounts your repo and runs your `agent:` command
inside the image you name. The `Dockerfile` here is a worked sample -
`node:20-slim` + [`@openai/codex`](https://www.npmjs.com/package/@openai/codex)
+ `git`.

```sh
docker build -t lute-codex-cage contrib/cage
```

## Configure it

```yaml
# .lute/config.yaml
cage: docker
cage_image: lute-codex-cage
cage_mounts:
  - "~/.codex"        # agent auth - read-only, by name, never implicit
```

`cage:` also accepts a **custom template** instead of the keyword `docker`, for
podman or bespoke flags - any string with the placeholders `{repo}`, `{image}`,
`{cmd}`, and `{mounts}` (it must contain `{cmd}`). Only those four braces are
substituted, so a shell `${VAR}` in your template survives untouched. Quote
`{repo}` yourself, as the built-in template does, so a repo path with spaces
stays one argument:

```yaml
cage: 'podman run --rm -i -v "{repo}:/work" -w /work {mounts} {image} sh -lc {cmd}'
```

## Auth crosses by name, read-only

`cage_mounts` mounts each host path **read-only at its own path** inside the
container. So `~/.codex` appears at exactly that absolute path inside. The Codex
CLI reads auth from `$CODEX_HOME`, so point it at that path in your `agent:`
command (use the absolute path from your machine):

```yaml
# .lute/config.yaml (continued)
agent: "CODEX_HOME=/absolute/path/to/.codex codex exec --sandbox workspace-write"
```

Nothing enters the cage implicitly: if it isn't the repo and isn't in
`cage_mounts`, the agent cannot see it. That is the whole point - a leaked
secret can't be read if it was never mounted.

## The Initial Release Boundary

The built-in `cage: docker` template isolates **filesystem and secrets** and
sets Docker `--network none` by default. Custom templates are your policy
surface: include the equivalent no-network flag yourself if egress isolation
matters. Mount only what the agent needs, and only read-only.
