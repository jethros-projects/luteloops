#!/bin/sh
# contrib/lute.sh - the hand-rolled bash sanity kernel (spec §3), kept for
# posterity. Single loop only (no nesting), runs-budget only, run verb only.
# It exists to prove the kernel semantics fit in ~30 lines; passes T1 and T2.
set -eu
[ "${1:-}" = "run" ] || { echo "usage: lute.sh run" >&2; exit 1; }
eval "$(python3 -c '
import shlex, yaml
d = yaml.safe_load(open("lute.yaml"))
for k, v in [("ID", d["loop"]), ("TASK", d.get("task", "")),
             ("AGENT", d.get("agent", "")), ("CHECK", d["done_when"]),
             ("RUNS", str(d.get("budget", "10 runs")).split()[0])]:
    print("%s=%s" % (k, shlex.quote(str(v))))')"
mkdir -p .lute/journal
if git rev-parse -q --verify "lute/$ID" >/dev/null; then
  git checkout -q "lute/$ID" && git reset -q --hard
else
  git checkout -q -b "lute/$ID"
fi
n=0
while :; do
  rc=0; out=$(sh -c "$CHECK" 2>&1) || rc=$?
  if [ "$rc" -eq 0 ]; then echo "closed: $ID"; exit 0; fi
  if [ "$n" -ge "$RUNS" ]; then echo "blocked: $ID after $n runs" >&2; exit 3; fi
  if [ -z "$TASK" ]; then echo "blocked: $ID (no task)" >&2; exit 3; fi
  n=$((n + 1)); t0=$(date +%s)
  printf 'You are one iteration of a loop. Goal: %s\n\nThe check `%s` is failing. Last 50 lines of its output:\n%s\n\nFIRST: read .lute/journal/%s.md - your past attempts live there.\nLAST: append 1–3 lines to it - what you tried, what you learned,\nwhat the next run must NOT retry. If it exceeds ~100 lines, compact it.\n' \
    "$TASK" "$CHECK" "$(printf %s "$out" | tail -50)" "$ID" | sh -c "$AGENT"
  git add -A && git commit -q --allow-empty -m "lute($ID): run $n"
  printf '{"ts": "%s", "loop": "%s", "run": %d, "duration": %d, "exit": 0}\n' \
    "$(date -u +%FT%TZ)" "$ID" "$n" "$(($(date +%s) - t0))" >> .lute/ledger.jsonl
done
