#!/usr/bin/env bash
set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
# Honor NO_COLOR (https://no-color.org) and fall back to plain text when stdout
# is not a terminal, so piped/CI output stays free of escape codes.
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    C_RESET=$'\033[0m'; C_BOLD=$'\033[1m';   C_DIM=$'\033[2m'
    C_BLUE=$'\033[1;34m'; C_GREEN=$'\033[1;32m'; C_YELLOW=$'\033[1;33m'
    C_RED=$'\033[1;31m';  C_CYAN=$'\033[1;36m'
else
    C_RESET=''; C_BOLD=''; C_DIM=''
    C_BLUE=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_CYAN=''
fi

# ── Help ──────────────────────────────────────────────────────────────────────

print_help() {
    cat <<EOF
${C_BOLD}just-makeit install-deps${C_RESET} — install build dependencies into a Python venv

${C_YELLOW}USAGE${C_RESET}
  ${C_CYAN}just-makeit install-deps${C_RESET} [OPTIONS] [VENV_DIR]
  ${C_CYAN}jm-install-deps${C_RESET}          [OPTIONS] [VENV_DIR]

${C_YELLOW}ARGUMENTS${C_RESET}
  ${C_BOLD}VENV_DIR${C_RESET}    Path for the Python virtual environment.
              Default: ${C_DIM}/tmp/jm-venv${C_RESET}

${C_YELLOW}OPTIONS${C_RESET}
  ${C_BOLD}--check${C_RESET}     Report what is installed/missing and make no changes.
              Exit 1 if anything is missing, 0 if everything is present.
  ${C_BOLD}-h, --help${C_RESET}  Show this message and exit.

${C_YELLOW}WHAT IT INSTALLS${C_RESET}
  ${C_BOLD}System${C_RESET} (auto-detected per platform):
    cmake, a C compiler (gcc/clang), pkg-config, and patchelf (Linux).
    ${C_DIM}Uses your distro's package manager on Linux, Homebrew on macOS.${C_RESET}
  ${C_BOLD}Python${C_RESET} (inside VENV_DIR):
    numpy and just-makeit.

${C_YELLOW}ENVIRONMENT${C_RESET}
  ${C_BOLD}SYSTEM_PYTHON${C_RESET}   Interpreter used to create the venv. Default: ${C_DIM}python3${C_RESET}
  ${C_BOLD}NO_COLOR${C_RESET}        Set to any value to disable colored output.

${C_YELLOW}EXIT STATUS${C_RESET}
  ${C_BOLD}0${C_RESET}   Success, or --check found everything present.
  ${C_BOLD}1${C_RESET}   --check found missing dependencies, or an install step failed.

${C_YELLOW}EXAMPLES${C_RESET}
  ${C_DIM}# default venv at /tmp/jm-venv${C_RESET}
  just-makeit install-deps
  ${C_DIM}# custom venv path${C_RESET}
  just-makeit install-deps ~/my-venv
  ${C_DIM}# dry-run status report, no changes${C_RESET}
  just-makeit install-deps --check
  ${C_DIM}# pick the Python interpreter${C_RESET}
  SYSTEM_PYTHON=python3.12 jm-install-deps
  ${C_DIM}# install and activate the venv in the current shell${C_RESET}
  source \$(which jm-install-deps)
EOF
}

CHECK=0
VENV_DIR="/tmp/jm-venv"
SYSTEM_PYTHON="${SYSTEM_PYTHON:-python3}"

for arg in "$@"; do
    case "$arg" in
        -h|--help)  print_help; exit 0 ;;
        --check)    CHECK=1 ;;
        --)         ;;  # end-of-options separator: ignore (path follows)
        *)          VENV_DIR="$arg" ;;
    esac
done

info()  { printf '%s==> %s%s\n' "$C_BLUE" "$*" "$C_RESET"; }
ok()    { printf '%s    ok%s  %s\n' "$C_GREEN" "$C_RESET" "$*"; }
skip()  { printf '%s    ok%s  %s  %s(already installed)%s\n' "$C_GREEN" "$C_RESET" "$*" "$C_DIM" "$C_RESET"; }
will()  { printf '%s  --> %s  %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
warn()  { printf '%s warn%s  %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
die()   { printf '%serror%s  %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

# ── Detect package manager ────────────────────────────────────────────────────

_detect_mgr() {
    if [[ "$(uname)" == "Darwin" ]]; then
        echo "brew"
    elif [[ -f /etc/os-release ]]; then
        . /etc/os-release
        case "${ID:-}" in
            ubuntu|debian|linuxmint|pop)         echo "apt" ;;
            fedora|rhel|centos|rocky|almalinux)
                command -v dnf5 >/dev/null 2>&1 && echo "dnf5" || echo "dnf" ;;
            arch|manjaro|endeavouros)            echo "pacman" ;;
            opensuse*|sles)                      echo "zypper" ;;
            alpine)                              echo "apk" ;;
            *)                                   echo "unknown" ;;
        esac
    else
        echo "unknown"
    fi
}

# ── 1. Check / install cmake + C compiler ────────────────────────────────────

MGR="$(_detect_mgr)"
NEED_CMAKE=0; NEED_CC=0

if command -v cmake >/dev/null 2>&1; then
    skip "cmake $(cmake --version | head -1 | awk '{print $3}')"
else
    NEED_CMAKE=1
    will "cmake  (will install via ${MGR})"
fi

if command -v cc >/dev/null 2>&1 || command -v gcc >/dev/null 2>&1 \
        || command -v clang >/dev/null 2>&1; then
    skip "C compiler ($(command -v gcc || command -v clang || command -v cc))"
else
    NEED_CC=1
    will "C compiler  (will install via ${MGR})"
fi

if [[ $NEED_CMAKE -eq 1 || $NEED_CC -eq 1 ]]; then
    will "venv at ${VENV_DIR}"
else
    skip "no system packages needed"
fi

if [[ $CHECK -eq 1 ]]; then
    if [[ $NEED_CMAKE -eq 1 || $NEED_CC -eq 1 ]]; then
        printf '\nRun without --check to install.\n'
        exit 1
    else
        printf '\nAll build dependencies are already installed.\n'
        exit 0
    fi
fi

# ── Install missing system deps ───────────────────────────────────────────────

SUDO=""
[[ "$(id -u)" -ne 0 ]] && SUDO="sudo"

_install_apt()    { info "apt"; $SUDO apt-get update -qq && $SUDO apt-get install -y cmake gcc pkg-config patchelf; }
_install_dnf()    { info "${MGR}"; $SUDO "$MGR" install -y cmake gcc pkgconf-pkg-config patchelf; }
_install_pacman() { info "pacman"; $SUDO pacman -Sy --noconfirm cmake gcc pkgconf patchelf; }
_install_zypper() { info "zypper"; $SUDO zypper install -y cmake gcc pkgconfig patchelf; }
_install_apk()    { info "apk"; $SUDO apk add --no-cache cmake gcc musl-dev pkgconfig patchelf; }
_install_brew() {
    if command -v brew >/dev/null 2>&1; then
        [[ $NEED_CMAKE -eq 1 ]] && brew install cmake
        if ! command -v cc >/dev/null 2>&1; then
            warn "No C compiler. Run: xcode-select --install"
        fi
    else
        warn "Homebrew not found. Install from https://brew.sh"
    fi
}

if [[ $NEED_CMAKE -eq 1 || $NEED_CC -eq 1 ]]; then
    case "$MGR" in
        apt)           _install_apt ;;
        dnf|dnf5)      _install_dnf ;;
        pacman)        _install_pacman ;;
        zypper)        _install_zypper ;;
        apk)           _install_apk ;;
        brew)          _install_brew ;;
        *)
            warn "Unknown package manager — install cmake + gcc manually."
            ;;
    esac
fi

# ── 2. Create venv ────────────────────────────────────────────────────────────
#
# `python -m venv` normally bundles pip via ensurepip. Debian/Ubuntu ship
# ensurepip in a separate package, so if the default path fails, create the
# venv without pip and bootstrap it from get-pip.py — no sudo or distro
# package required (the script already needs the network to fetch numpy).

_fetch() {
    if   command -v curl >/dev/null 2>&1; then curl -fsSL "$1"
    elif command -v wget >/dev/null 2>&1; then wget -qO- "$1"
    else die "curl or wget is required to bootstrap pip into the venv."; fi
}

info "Creating venv at ${VENV_DIR}"
if "$SYSTEM_PYTHON" -m venv "$VENV_DIR" >/dev/null 2>&1; then
    ok "venv created"
else
    warn "ensurepip unavailable — bootstrapping pip via get-pip.py"
    "$SYSTEM_PYTHON" -m venv --without-pip "$VENV_DIR"
    _fetch https://bootstrap.pypa.io/get-pip.py | "${VENV_DIR}/bin/python"
    ok "venv created (pip bootstrapped)"
fi

VENV_PYTHON="${VENV_DIR}/bin/python"
VENV_PIP="${VENV_DIR}/bin/pip"

# ── 3. Install Python deps ────────────────────────────────────────────────────

info "Installing numpy and just-makeit"
"$VENV_PIP" install --quiet --upgrade pip
"$VENV_PIP" install --quiet numpy just-makeit
ok "numpy $("$VENV_PYTHON" -c 'import numpy; print(numpy.__version__)')"
ok "just-makeit installed"

# ── 4. Activate ───────────────────────────────────────────────────────────────

ACTIVATE="${VENV_DIR}/bin/activate"

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    # shellcheck source=/dev/null
    source "$ACTIVATE"
    info "venv activated  (${VENV_DIR})"
else
    printf '\n'
    info "Done. Activate the venv with:"
    printf '\n    source %s\n\n' "$ACTIVATE"
fi
