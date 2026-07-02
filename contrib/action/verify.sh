#!/usr/bin/env bash
# verify.sh - the lute-verify action's whole brain, kept out of action.yml so
# the notch suite can administer it without GitHub (t50). Contract: install
# lute unless told it is present, lint first (an exam that cannot be
# administered fails fast), run the manifest in the foreground, then map
# lute's exit contract onto the job verdict — 0 closed, 3 blocked (cards
# printed), 4 gated (CI cannot approve a human gate, on purpose). The
# canonical word lands in $GITHUB_OUTPUT as outcome=... for later steps.
set -euo pipefail

manifest="${INPUT_MANIFEST:-lute.yaml}"
agent="${INPUT_AGENT:-}"
install="${INPUT_INSTALL:-}"

if [ -n "$install" ] && [ "$install" != "none" ]; then
  python3 -m pip install --quiet "$install"
fi
command -v lute >/dev/null 2>&1 \
  || { echo "lute-verify: no lute on PATH; set the install: input or install it in a prior step"; exit 1; }

run_args=(--plain --file "$manifest")
lint_args=("$manifest")
if [ -n "$agent" ]; then
  run_args+=(--agent "$agent")
  lint_args+=(--agent "$agent")
fi

lute lint "${lint_args[@]}"

rc=0
lute run "${run_args[@]}" || rc=$?

case "$rc" in
  0) outcome="closed"
     echo "lute-verify: all loops closed; every attempt is a commit on the lute/<root> branch"
     lute attest --file "$manifest" > lute-attestation.json
     echo "lute-verify: attestation written (sign it with your CI identity to make it portable)" ;;
  3) outcome="blocked"
     echo "lute-verify: blocked - a loop exhausted its budget or hit a conflict; the cards:"
     lute inbox || true ;;
  4) outcome="gated"
     echo "lute-verify: gated - a human gate cannot be approved inside CI, on purpose."
     echo "Approve locally (lute answer <loop> approve) or keep gated loops out of CI manifests." ;;
  *) outcome="error"
     echo "lute-verify: run failed (exit $rc)" ;;
esac

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "outcome=$outcome" >> "$GITHUB_OUTPUT"
fi
exit "$rc"
