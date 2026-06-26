#!/usr/bin/env bash
set -euo pipefail

repo_url="${LUTE_REPO_URL:-https://github.com/jethros-projects/luteloops.git}"
ref="${LUTE_REF:-v0.1.0}"
install_spec="git+${repo_url}@${ref}"
verbose="${LUTE_INSTALL_VERBOSE:-0}"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  bold="$(printf '\033[1m')"
  dim="$(printf '\033[2m')"
  red="$(printf '\033[31m')"
  green="$(printf '\033[32m')"
  cyan="$(printf '\033[36m')"
  yellow="$(printf '\033[33m')"
  reset="$(printf '\033[0m')"
else
  bold=""
  dim=""
  red=""
  green=""
  cyan=""
  yellow=""
  reset=""
fi

say() {
  printf '%s\n' "$*"
}

say_err() {
  printf '%s\n' "$*" >&2
}

have() {
  command -v "$1" >/dev/null 2>&1
}

title() {
  say "${bold}Lute installer${reset}"
  say "${dim}Source: ${repo_url}@${ref}${reset}"
  say ""
}

ok() {
  say "  ${green}[ok]${reset} $*"
}

warn() {
  say "  ${yellow}[warn]${reset} $*"
}

fail() {
  say_err "  ${red}[failed]${reset} $*"
}

spinner_line() {
  label="$1"
  log="$2"
  shift 2

  "$@" >"${log}" 2>&1 &
  pid=$!
  frames='|/-\'
  index=0

  if [ -t 1 ]; then
    while kill -0 "${pid}" 2>/dev/null; do
      frame="$(printf '%s' "${frames}" | cut -c $((index % 4 + 1)))"
      printf '\r  %b[%s]%b %s' "${cyan}" "${frame}" "${reset}" "${label}"
      index=$((index + 1))
      sleep 0.12
    done
    printf '\r'
  else
    say "  ... ${label}"
  fi

  wait "${pid}"
}

run_quiet() {
  label="$1"
  shift
  if [ "${verbose}" = "1" ]; then
    say "  ${cyan}[run]${reset} ${label}"
    "$@"
    ok "${label}"
    return 0
  fi

  log="$(mktemp "${TMPDIR:-/tmp}/lute-install.XXXXXX.log")"
  if spinner_line "${label}" "${log}" "$@"; then
    if [ -t 1 ] && [ "${verbose}" != "1" ]; then
      printf '  %b[ok]%b %s\n' "${green}" "${reset}" "${label}"
    else
      ok "${label}"
    fi
    rm -f "${log}"
    return 0
  fi

  fail "${label}"
  say_err "  Log: ${log}"
  say_err "  Last output:"
  tail -n 40 "${log}" >&2 || true
  exit 1
}

is_python_310() {
  "$1" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
}

pick_python() {
  if [ -n "${PYTHON:-}" ]; then
    if is_python_310 "${PYTHON}"; then
      printf '%s\n' "${PYTHON}"
      return 0
    fi
    say_err "PYTHON=${PYTHON} is older than Python 3.10."
    return 1
  fi

  for candidate in python3.12 python3.11 python3.10 python3; do
    if have "${candidate}" && is_python_310 "${candidate}"; then
      command -v "${candidate}"
      return 0
    fi
  done

  say_err "Lute needs Python 3.10 or newer."
  say_err "Install Python 3.10+ first, then rerun this installer."
  return 1
}

install_runtime() {
  if have pipx; then
    run_quiet "Install Lute runtime with pipx" pipx install --python "${python_bin}" --force "${install_spec}"
    return 0
  fi

  data_home="${XDG_DATA_HOME:-${HOME}/.local/share}"
  bin_home="${LUTE_BIN_DIR:-${HOME}/.local/bin}"
  venv_dir="${LUTE_VENV:-${data_home}/lute/venv}"

  run_quiet "Create private Python environment" "${python_bin}" -m venv "${venv_dir}"
  run_quiet "Update Python packaging tools" "${venv_dir}/bin/python" -m pip install --upgrade pip
  run_quiet "Install Lute runtime" "${venv_dir}/bin/python" -m pip install --upgrade "${install_spec}"
  mkdir -p "${bin_home}"
  ln -sf "${venv_dir}/bin/lute" "${bin_home}/lute"
}

lute_path() {
  if have lute; then
    command -v lute
    return 0
  fi

  bin_home="${LUTE_BIN_DIR:-${HOME}/.local/bin}"
  if [ -x "${bin_home}/lute" ]; then
    printf '%s\n' "${bin_home}/lute"
    return 0
  fi

  return 1
}

title

say "${bold}1. Runtime${reset}"
python_bin="$(pick_python)"
ok "Python: ${python_bin}"
install_runtime

if lute_bin="$(lute_path)"; then
  ok "Lute: ${lute_bin}"
else
  bin_home="${LUTE_BIN_DIR:-${HOME}/.local/bin}"
  ok "Lute runtime installed"
  warn "If your shell cannot find 'lute', add this to PATH:"
  say "       export PATH=\"${bin_home}:\$PATH\""
fi

say ""
say "${bold}Done.${reset}"
say "Try:"
say "  lute --help"
