#!/usr/bin/env bash
set -euo pipefail

package="${LUTE_PACKAGE:-luteloops}"
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
  say "${bold}Lute uninstaller${reset}"
  say "${dim}Removes the installed tool only; project repos and .lute state stay put.${reset}"
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

  log="$(mktemp "${TMPDIR:-/tmp}/lute-uninstall.XXXXXX.log")"
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

pipx_has_lute() {
  pipx list --short 2>/dev/null | awk '{ print $1 }' | grep -qx "${package}" && return 0
  pipx list 2>/dev/null | grep -Eq "(^|[[:space:]])package ${package}[[:space:]]" && return 0
  return 1
}

uninstall_pipx() {
  if ! have pipx; then
    ok "pipx not found"
    return 0
  fi
  if pipx_has_lute; then
    run_quiet "Uninstall ${package} from pipx" pipx uninstall "${package}"
  else
    ok "No pipx install named ${package}"
  fi
}

refuse_dangerous_dir() {
  dir="${1%/}"
  home_dir="${HOME%/}"
  data_home="${XDG_DATA_HOME:-${HOME}/.local/share}"
  data_home="${data_home%/}"

  case "${dir}" in
    ""|"/"|".")
      return 0
      ;;
    "${home_dir}"|"${data_home}"|"${HOME%/}/.local")
      return 0
      ;;
  esac

  return 1
}

remove_private_env() {
  data_home="${XDG_DATA_HOME:-${HOME}/.local/share}"
  bin_home="${LUTE_BIN_DIR:-${HOME}/.local/bin}"
  venv_dir="${LUTE_VENV:-${data_home}/lute/venv}"
  lute_link="${bin_home}/lute"
  expected_lute="${venv_dir}/bin/lute"

  if [ -L "${lute_link}" ]; then
    target="$(readlink "${lute_link}")"
    if [ "${target}" = "${expected_lute}" ]; then
      run_quiet "Remove installer-created lute symlink" rm -f "${lute_link}"
    else
      warn "Leaving ${lute_link}; it points to ${target}, not ${expected_lute}"
    fi
  elif [ -e "${lute_link}" ]; then
    warn "Leaving ${lute_link}; it is not an installer-created symlink"
  else
    ok "No installer-created lute symlink found"
  fi

  if [ -d "${venv_dir}" ]; then
    if refuse_dangerous_dir "${venv_dir}"; then
      fail "Refusing to remove suspicious LUTE_VENV=${venv_dir}"
      exit 1
    fi
    run_quiet "Remove private Lute environment" rm -rf "${venv_dir}"
    rmdir "$(dirname "${venv_dir}")" 2>/dev/null || true
  else
    ok "No private Lute environment found"
  fi
}

report_remaining_lute() {
  hash -r 2>/dev/null || true
  if lute_path="$(command -v lute 2>/dev/null)"; then
    warn "A lute command is still on PATH: ${lute_path}"
    say "       It was not removed because it was not installed by pipx or this installer."
  else
    ok "No lute command found on PATH"
  fi
}

title

say "${bold}1. pipx${reset}"
uninstall_pipx

say ""
say "${bold}2. Installer venv${reset}"
remove_private_env

say ""
say "${bold}3. Check PATH${reset}"
report_remaining_lute

say ""
say "${bold}Done.${reset}"
say "Kept your project repos, .lute directories, INBOX cards, branches, logs, and crontab entries."
