#!/usr/bin/env bash
# check-links.sh - every relative link in the committed docs must resolve to a
# real file or directory. Web URLs and pure anchors are out of scope (this is
# a dead-end detector for the tree, not a crawler). Exits 1 listing every
# broken link; silent 0 when the docs have no dead ends.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
broken=0

while IFS= read -r file; do
  dir="$(dirname "$file")"
  while IFS= read -r target; do
    case "$target" in
      http://*|https://*|mailto:*|"#"*) continue ;;
    esac
    path="${target%%#*}"
    [ -n "$path" ] || continue
    if [ ! -e "$dir/$path" ] && [ ! -e "$ROOT/$path" ]; then
      echo "broken link in $file: $target"
      broken=$((broken + 1))
    fi
  done < <(grep -o '\[[^]]*\]([^)]*)' "$file" | sed 's/.*(\(.*\))/\1/')
done < <(git -C "$ROOT" ls-files '*.md')

[ "$broken" -eq 0 ] || { echo "$broken broken link(s)"; exit 1; }
echo "links: all relative links resolve"
