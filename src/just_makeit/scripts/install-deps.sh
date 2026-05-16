#!/usr/bin/env bash
set -euo pipefail

_HELP=$(cat <<'EOF'
Usage: just-makeit install-deps [OPTIONS] [VENV_DIR]
       jm-install-deps [OPTIONS] [VENV_DIR]

Install cmake, a C compiler, and numpy into a Python venv.

Arguments:
  VENV_DIR   Path for the Python virtual environment.
             Default: /tmp/jm-venv  (Linux/macOS)

Options:
  --check    Report what is installed/missing; exit 1 if anything is
             missing, 0 if everything is present. No changes are made.
  -h, --help Show this message and exit.

What it installs:
  System:  cmake + C compiler
             Linux:   apt · dnf · pacman · zypper · apk (auto-detected)
             macOS:   Homebrew
  Python:  numpy + just-makeit  (inside the venv)

Examples:
  just-makeit install-deps                  # default venv at /tmp/jm-venv
  just-makeit install-deps ~/my-venv        # custom venv path
  just-makeit install-deps --check          # dry-run status report
  source $(which jm-install-deps)           # install + activate in current shell
EOF
)

CHECK=0
VENV_DIR="/tmp/jm-venv"
SYSTEM_PYTHON="${SYSTEM_PYTHON:-python3}"

for arg in "$@"; do
    case "$arg" in
        -h|--help)  printf '%s\n' "$_HELP"; exit 0 ;;
        --check)    CHECK=1 ;;
        *)          VENV_DIR="$arg" ;;
    esac
done

info()  { printf '\033[1;34m==> %s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32m    ok\033[0m  %s\n' "$*"; }
skip()  { printf '\033[1;32m    ok\033[0m  %s  \033[2m(already installed)\033[0m\n' "$*"; }
will()  { printf '\033[1;33m  --> \033[0m  %s\n' "$*"; }
warn()  { printf '\033[1;33m warn\033[0m  %s\n' "$*"; }
die()   { printf '\033[1;31merror\033[0m  %s\n' "$*" >&2; exit 1; }

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

_install_apt()    { info "apt"; $SUDO apt-get update -qq && $SUDO apt-get install -y cmake gcc pkg-config; }
_install_dnf()    { info "${MGR}"; $SUDO "$MGR" install -y cmake gcc pkgconf-pkg-config; }
_install_pacman() { info "pacman"; $SUDO pacman -Sy --noconfirm cmake gcc pkgconf; }
_install_zypper() { info "zypper"; $SUDO zypper install -y cmake gcc pkgconfig; }
_install_apk()    { info "apk"; $SUDO apk add --no-cache cmake gcc musl-dev pkgconfig; }
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
