#!/usr/bin/env bash
# Bootstrap installer for just-makeit — no uv, no pre-existing tools required.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/just-buildit/just-makeit/main/install.sh | sh
#   source /tmp/jm-venv/bin/activate
#
#   # Custom venv path:
#   curl -fsSL https://raw.githubusercontent.com/just-buildit/just-makeit/main/install.sh | sh -s -- ~/my-venv
#
#   # Check what would be installed without changing anything:
#   curl -fsSL https://raw.githubusercontent.com/just-buildit/just-makeit/main/install.sh | sh -s -- --check
#
# What it does:
#   1. Verifies Python >= 3.11 is available
#   2. Creates a venv at VENV_DIR (default: /tmp/jm-venv)
#   3. pip-installs just-makeit + numpy into the venv
#   4. Installs cmake and a C compiler via the system package manager
set -euo pipefail

# ── Argument parsing ──────────────────────────────────────────────────────────

CHECK=0
VENV_DIR="/tmp/jm-venv"
PYTHON="${PYTHON:-python3}"

for arg in "$@"; do
    case "$arg" in
        --check) CHECK=1 ;;
        -*)      printf 'Unknown flag: %s\n' "$arg" >&2; exit 1 ;;
        *)       VENV_DIR="$arg" ;;
    esac
done

# ── Formatting helpers ────────────────────────────────────────────────────────

info()  { printf '\033[1;34m==> %s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32m    ok\033[0m  %s\n' "$*"; }
skip()  { printf '\033[1;32m    ok\033[0m  %s  \033[2m(already installed)\033[0m\n' "$*"; }
will()  { printf '\033[1;33m  --> \033[0m  %s\n' "$*"; }
warn()  { printf '\033[1;33m warn\033[0m  %s\n' "$*"; }
die()   { printf '\033[1;31merror\033[0m  %s\n' "$*" >&2; exit 1; }

# ── 1. Python version check ───────────────────────────────────────────────────

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    die "Python not found. Install Python 3.11+ and re-run."
fi

PY_VERSION=$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
PY_MAJOR=$("$PYTHON" -c 'import sys; print(sys.version_info[0])')
PY_MINOR=$("$PYTHON" -c 'import sys; print(sys.version_info[1])')

if [[ "$PY_MAJOR" -lt 3 || ("$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 11) ]]; then
    die "Python $PY_VERSION found, but 3.11+ is required."
fi

ok "Python $PY_VERSION"

# ── 2. Detect system package manager ─────────────────────────────────────────

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

MGR="$(_detect_mgr)"
NEED_CMAKE=0
NEED_CC=0

if command -v cmake >/dev/null 2>&1; then
    skip "cmake $(cmake --version | head -1 | awk '{print $3}')"
else
    NEED_CMAKE=1
    will "cmake  (will install via ${MGR})"
fi

if command -v cc >/dev/null 2>&1 || command -v gcc >/dev/null 2>&1 \
        || command -v clang >/dev/null 2>&1; then
    skip "C compiler ($(command -v gcc 2>/dev/null || command -v clang 2>/dev/null || command -v cc 2>/dev/null))"
else
    NEED_CC=1
    will "C compiler  (will install via ${MGR})"
fi

will "venv at ${VENV_DIR}  (just-makeit + numpy)"

if [[ $CHECK -eq 1 ]]; then
    if [[ $NEED_CMAKE -eq 1 || $NEED_CC -eq 1 ]]; then
        printf '\nRun without --check to install.\n'
        exit 1
    else
        printf '\nAll build dependencies are already installed.\n'
        exit 0
    fi
fi

# ── 3. Install missing system deps ────────────────────────────────────────────

_install_apt()    { info "apt"; sudo apt-get update -qq && sudo apt-get install -y cmake gcc pkg-config; }
_install_dnf()    { info "${MGR}"; sudo "$MGR" install -y cmake gcc pkgconf-pkg-config; }
_install_pacman() { info "pacman"; sudo pacman -Sy --noconfirm cmake gcc pkgconf; }
_install_zypper() { info "zypper"; sudo zypper install -y cmake gcc pkgconfig; }
_install_apk()    { info "apk"; sudo apk add --no-cache cmake gcc musl-dev pkgconfig; }
_install_brew() {
    if command -v brew >/dev/null 2>&1; then
        [[ $NEED_CMAKE -eq 1 ]] && brew install cmake
        if ! command -v cc >/dev/null 2>&1; then
            warn "No C compiler found. Run: xcode-select --install"
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
        *)             warn "Unknown package manager — install cmake + gcc manually." ;;
    esac
fi

# ── 4. Create venv + install just-makeit ─────────────────────────────────────

info "Creating venv at ${VENV_DIR}"
"$PYTHON" -m venv "$VENV_DIR"
ok "venv created"

VENV_PIP="${VENV_DIR}/bin/pip"
VENV_PYTHON="${VENV_DIR}/bin/python"

info "Installing just-makeit + numpy"
"$VENV_PIP" install --quiet --upgrade pip
"$VENV_PIP" install --quiet numpy just-makeit
ok "numpy $("$VENV_PYTHON" -c 'import numpy; print(numpy.__version__)')"
ok "just-makeit $("$VENV_PYTHON" -c 'from importlib.metadata import version; print(version("just-makeit"))')"

# ── 5. Done ───────────────────────────────────────────────────────────────────

printf '\n'
info "Done. Activate the venv and start building:"
printf '\n'
printf '    source %s/bin/activate\n' "$VENV_DIR"
printf '    just-makeit new my_project --object my_object\n'
printf '\n'
