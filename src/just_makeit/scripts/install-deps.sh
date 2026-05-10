#!/usr/bin/env bash
# Set up build dependencies for just-makeit projects.
#
# Usage:
#   bash tools/install-deps.sh              # system deps + venv at /tmp/jm-venv
#   bash tools/install-deps.sh /my/venv    # custom venv path
#   source tools/install-deps.sh           # same, but also activates venv in current shell
#
# What it does:
#   1. Installs cmake and a C compiler via the system package manager
#   2. Creates a venv at VENV_DIR (default: /tmp/jm-venv)
#   3. Installs numpy and just-makeit into the venv
#   4. Prints (or executes) the activation command
set -euo pipefail

VENV_DIR="${1:-/tmp/jm-venv}"
SYSTEM_PYTHON="${SYSTEM_PYTHON:-python3}"

info()  { printf '\033[1;34m==> %s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32m    ok\033[0m  %s\n' "$*"; }
warn()  { printf '\033[1;33m warn\033[0m  %s\n' "$*"; }
die()   { printf '\033[1;31merror\033[0m  %s\n' "$*" >&2; exit 1; }

# ── 1. System deps (cmake + C compiler) ──────────────────────────────────────

install_system_deps() {
    if [[ "$(uname)" == "Darwin" ]]; then
        _install_macos
    elif [[ -f /etc/os-release ]]; then
        # shellcheck source=/dev/null
        . /etc/os-release
        case "${ID:-}" in
            ubuntu|debian|linuxmint|pop)              _install_apt ;;
            fedora|rhel|centos|rocky|almalinux)       _install_dnf ;;
            arch|manjaro|endeavouros)                 _install_pacman ;;
            opensuse*|sles)                           _install_zypper ;;
            alpine)                                   _install_apk ;;
            *)
                warn "Unknown distro '${ID:-}' — skipping system package install."
                warn "Install cmake and a C compiler manually, then re-run."
                return 1
                ;;
        esac
    else
        die "Cannot detect OS. Install cmake + gcc/clang manually."
    fi
}

_install_apt()    { info "apt"; sudo apt-get update -qq && sudo apt-get install -y cmake gcc pkg-config; }
_install_dnf()    { info "dnf"; local m=dnf; command -v dnf5 >/dev/null 2>&1 && m=dnf5; sudo "$m" install -y cmake gcc pkgconf-pkg-config; }
_install_pacman() { info "pacman"; sudo pacman -Sy --noconfirm cmake gcc pkgconf; }
_install_zypper() { info "zypper"; sudo zypper install -y cmake gcc pkgconfig; }
_install_apk()    { info "apk"; sudo apk add --no-cache cmake gcc musl-dev pkgconfig; }
_install_macos() {
    info "macOS"
    if command -v brew >/dev/null 2>&1; then
        brew install cmake
    else
        warn "Homebrew not found. Install cmake from https://cmake.org/download/"
        warn "or install Homebrew first: https://brew.sh"
        return 1
    fi
    if ! command -v cc >/dev/null 2>&1; then
        warn "No C compiler found. Run: xcode-select --install"
    fi
}

NEED_SYSTEM=0
command -v cmake >/dev/null 2>&1 && ok "cmake $(cmake --version | head -1 | awk '{print $3}')" || NEED_SYSTEM=1
command -v cc    >/dev/null 2>&1 && ok "C compiler found" || NEED_SYSTEM=1

if [[ $NEED_SYSTEM -eq 1 ]]; then
    install_system_deps
fi

# ── 2. Create venv ────────────────────────────────────────────────────────────

info "Creating venv at ${VENV_DIR}"
"$SYSTEM_PYTHON" -m venv "$VENV_DIR"
ok "venv created"

VENV_PYTHON="${VENV_DIR}/bin/python"
VENV_PIP="${VENV_DIR}/bin/pip"

# ── 3. Install Python deps ────────────────────────────────────────────────────

info "Installing numpy and just-makeit"
"$VENV_PIP" install --quiet --upgrade pip
"$VENV_PIP" install --quiet numpy just-makeit
ok "numpy $("$VENV_PYTHON" -c 'import numpy; print(numpy.__version__)')"
ok "just-makeit $("$VENV_PYTHON" -m just_makeit --version 2>/dev/null || echo installed)"

# ── 4. Activate ───────────────────────────────────────────────────────────────

ACTIVATE="${VENV_DIR}/bin/activate"

# If sourced, activate in the caller's shell; otherwise just print instructions.
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    # shellcheck source=/dev/null
    source "$ACTIVATE"
    info "venv activated  (${VENV_DIR})"
else
    printf '\n'
    info "Done. Activate the venv with:"
    printf '\n    source %s\n\n' "$ACTIVATE"
fi
