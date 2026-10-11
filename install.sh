#!/usr/bin/env bash
# Bootstrap installer for just-makeit — no uv, no pre-existing tools required.
#
# ── Recommended: source into the current shell so the venv activates immediately
#
#   . <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
#
# ── Piped form (venv path printed at the end; activate manually):
#
#   curl -fsSL https://just-buildit.github.io/just-makeit/install.sh | sh
#   source /tmp/jm-venv/bin/activate
#
# ── Options (append to the command):
#
#   . <(curl -fsSL ...) ~/my-venv     # custom venv path
#   . <(curl -fsSL ...) --check       # report what would change, no writes
#   . <(curl -fsSL ...) --force       # reinstall even if up to date
#
# A leading `--` end-of-options separator is accepted and ignored.
#
set -euo pipefail

# ── Argument parsing ──────────────────────────────────────────────────────────

CHECK=0
FORCE=0
VENV_DIR="/tmp/jm-venv"
PYTHON="${PYTHON:-python3}"

for arg in "$@"; do
    case "$arg" in
        --check) CHECK=1 ;;
        --force) FORCE=1 ;;
        --)      ;;  # end-of-options separator: ignore (path follows)
        -*)      printf 'Unknown flag: %s\n' "$arg" >&2; return 1 2>/dev/null || exit 1 ;;
        *)       VENV_DIR="$arg" ;;
    esac
done

# ── Formatting helpers ────────────────────────────────────────────────────────

_tty() { [[ -t 1 ]]; }

info()  { printf '\033[1;34m==> %s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32m    ok\033[0m  %s\n' "$*"; }
skip()  { printf '\033[1;32m    ok\033[0m  %s  \033[2m(already installed)\033[0m\n' "$*"; }
will()  { printf '\033[1;33m  --> \033[0m  %s\n' "$*"; }
warn()  { printf '\033[1;33m warn\033[0m  %s\n' "$*"; }
die()   {
    printf '\033[1;31merror\033[0m  %s\n' "$*" >&2
    return 1 2>/dev/null || exit 1
}

# Spinner: run a command in the background, show dots until it finishes.
# Falls back to plain output when stdout is not a tty (CI, pipe).
_spin() {
    local label="$1"; shift
    if ! _tty; then
        info "$label"
        "$@"
        return
    fi
    "$@" &>/tmp/_jm_spin_out &
    local pid=$!
    local frames='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    local i=0
    printf '\033[1;34m==> \033[0m%s ' "$label"
    while kill -0 "$pid" 2>/dev/null; do
        printf '\033[1;34m%s\033[0m\b' "${frames:$((i % ${#frames})):1}"
        sleep 0.1
        i=$((i + 1))
    done
    wait "$pid" && printf '\033[1;32m✓\033[0m\n' || {
        printf '\033[1;31m✗\033[0m\n'
        cat /tmp/_jm_spin_out >&2
        die "$label failed"
    }
}

# ── 1. Python version check ───────────────────────────────────────────────────

# The oldest Python just-makeit supports: pyproject.toml's `requires-python`.
# This script usually runs from a curl pipe, with no pyproject.toml to read,
# so the floor is written here once and every check and message below reads
# it. tests/test_gh1916_install_sh_floor.py runs this script at the floor and
# just below it, so the two cannot drift apart (gh-1916).
PY_FLOOR="3.9"
PY_FLOOR_MAJOR="${PY_FLOOR%%.*}"
PY_FLOOR_MINOR="${PY_FLOOR#*.}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    die "Python not found. Install Python ${PY_FLOOR}+ and re-run."
fi

PY_VERSION=$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
PY_MINOR=$("$PYTHON"   -c 'import sys; print(sys.version_info[1])')
PY_MAJOR=$("$PYTHON"   -c 'import sys; print(sys.version_info[0])')

if [[ "$PY_MAJOR" -lt "$PY_FLOOR_MAJOR" \
        || ("$PY_MAJOR" -eq "$PY_FLOOR_MAJOR" \
            && "$PY_MINOR" -lt "$PY_FLOOR_MINOR") ]]; then
    die "Python $PY_VERSION found, but ${PY_FLOOR}+ is required."
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
NEED_PKG_CONFIG=0

# Every system dependency found missing, as the report names it. The install
# step, --check's verdict and the unknown-manager hint all read this one
# list, so a dependency checked below cannot be left out of any of them
# (gh-1972). jm's install-deps.sh keeps the same list.
MISSING=""
_need() {
    MISSING="${MISSING:+${MISSING}, }$1"
    will "$1  (via ${MGR})"
}

if command -v cmake >/dev/null 2>&1; then
    skip "cmake $(cmake --version | head -1 | awk '{print $3}')"
else
    NEED_CMAKE=1
    _need cmake
fi

if command -v cc >/dev/null 2>&1 || command -v gcc >/dev/null 2>&1 \
        || command -v clang >/dev/null 2>&1; then
    skip "C compiler ($(command -v gcc 2>/dev/null || command -v clang 2>/dev/null || command -v cc 2>/dev/null))"
else
    _need "C compiler"
fi

# CMake's FindPkgConfig runs pkg-config to configure a project that declares
# `[project] pkg_modules`, and a C consumer reads an installed library's .pc
# through it (gh-1994). Probed by that name alone, not as pkgconf: CMake 3.16,
# a generated project's floor, searches for no other, and every manager's
# package below provides it.
if command -v pkg-config >/dev/null 2>&1; then
    skip "pkg-config ($(command -v pkg-config))"
else
    NEED_PKG_CONFIG=1
    _need pkg-config
fi

# auditwheel needs patchelf to repair a Linux wheel (`just-makeit build`,
# `pip wheel .`); delocate repairs a macOS one without it (gh-1972).
if [[ "$(uname)" == "Linux" ]]; then
    if command -v patchelf >/dev/null 2>&1; then
        skip "patchelf ($(command -v patchelf))"
    else
        _need patchelf
    fi
fi

# ── 3. Check if just-makeit is already current in the venv ───────────────────

JM_CURRENT=0
if [[ $FORCE -eq 0 && -x "${VENV_DIR}/bin/python" ]]; then
    _installed=$("${VENV_DIR}/bin/python" -c \
        'from importlib.metadata import version; print(version("just-makeit"))' 2>/dev/null || true)
    # `pip index versions` opens with `just-makeit (X.Y.Z)`. POSIX sed, not
    # `grep -oP`: BSD grep (macOS) and busybox grep (Alpine) have no -P, so
    # the version read back empty there and just-makeit was never current
    # (gh-1993).
    _latest=$(pip index versions just-makeit 2>/dev/null \
        | sed -n 's/^just-makeit (\([0-9.]*\)).*/\1/p' | head -1 || true)
    if [[ -n "$_installed" && "$_installed" == "$_latest" ]]; then
        JM_CURRENT=1
        skip "just-makeit ${_installed} in ${VENV_DIR}"
    else
        will "just-makeit ${_installed:-not installed} → ${_latest:-latest}  (${VENV_DIR})"
    fi
else
    will "just-makeit  (${VENV_DIR})"
fi

# ── 4. --check: report and exit ──────────────────────────────────────────────

if [[ $CHECK -eq 1 ]]; then
    if [[ -n "$MISSING" || $JM_CURRENT -eq 0 ]]; then
        printf '\nRun without --check to install.\n'
        return 1 2>/dev/null || exit 1
    else
        printf '\nEverything is up to date.\n'
        return 0 2>/dev/null || exit 0
    fi
fi

# ── 5. Install missing system deps ───────────────────────────────────────────

SUDO=""
[[ "$(id -u)" -ne 0 ]] && SUDO="sudo"

# A copy of install-deps.sh's, which this script is fetched without;
# tests/test_gh1972_installers_patchelf.py runs both copies against stub
# package managers and requires the same commands per manager.
#
# Retries cover only a request that ERRORS; a stalled mirror never errors, so
# without a per-request timeout it hangs the caller's job (gh-1792).
_install_apt()    {
    $SUDO apt-get -o Acquire::Retries=3 \
        -o Acquire::http::Timeout=30 \
        -o Acquire::https::Timeout=30 \
        update -qq \
        && $SUDO apt-get -o Acquire::Retries=3 \
        -o Acquire::http::Timeout=30 \
        -o Acquire::https::Timeout=30 \
        install -y cmake gcc pkg-config patchelf
}
_install_dnf()    { $SUDO "$MGR" install -y cmake gcc pkgconf-pkg-config patchelf; }
_install_pacman() { $SUDO pacman -Sy --noconfirm cmake gcc pkgconf patchelf; }
_install_zypper() { $SUDO zypper install -y cmake gcc pkgconfig patchelf; }
_install_apk()    { $SUDO apk add --no-cache cmake gcc musl-dev pkgconfig patchelf; }
_install_brew() {
    if command -v brew >/dev/null 2>&1; then
        [[ $NEED_CMAKE -eq 1 ]] && brew install cmake
        [[ $NEED_PKG_CONFIG -eq 1 ]] && brew install pkg-config
        command -v cc >/dev/null 2>&1 || warn "No C compiler. Run: xcode-select --install"
    else
        warn "Homebrew not found — install from https://brew.sh"
    fi
}

if [[ -n "$MISSING" ]]; then
    case "$MGR" in
        apt)      _spin "apt" _install_apt ;;
        dnf|dnf5) _spin "${MGR}" _install_dnf ;;
        pacman)   _spin "pacman" _install_pacman ;;
        zypper)   _spin "zypper" _install_zypper ;;
        apk)      _spin "apk" _install_apk ;;
        brew)     _install_brew ;;
        *)        warn "Unknown package manager — install these manually: ${MISSING}." ;;
    esac
fi

# ── 6. Create venv + install just-makeit ─────────────────────────────────────

if [[ $JM_CURRENT -eq 0 ]]; then
    _setup_venv() {
        # ensurepip (bundled pip) is split into a separate package on
        # Debian/Ubuntu; if the default venv fails, create it without pip and
        # bootstrap from get-pip.py — no sudo or distro package needed.
        # --clear so a reused dir from a different Python is rebuilt clean,
        # not layered into an inconsistent venv (mismatched bin/python vs libs).
        if ! "$PYTHON" -m venv --clear "$VENV_DIR" >/dev/null 2>&1; then
            "$PYTHON" -m venv --clear --without-pip "$VENV_DIR"
            curl -fsSL https://bootstrap.pypa.io/get-pip.py \
                | "${VENV_DIR}/bin/python"
        fi
        "${VENV_DIR}/bin/pip" install --quiet --upgrade pip
        "${VENV_DIR}/bin/pip" install --quiet numpy
        "${VENV_DIR}/bin/pip" install --quiet --upgrade just-makeit
    }
    _spin "Setting up venv at ${VENV_DIR}" _setup_venv

    VENV_PYTHON="${VENV_DIR}/bin/python"
    # Verify the installs actually import — a pip that "succeeds" into a
    # mismatched tree would otherwise be reported ok with an empty version.
    _np=$("$VENV_PYTHON" -c 'import numpy; print(numpy.__version__)' 2>/dev/null) \
        || die "numpy did not install into ${VENV_DIR}"
    ok "numpy ${_np}"
    _jm=$("$VENV_PYTHON" -c \
        'from importlib.metadata import version; print(version("just-makeit"))' \
        2>/dev/null) || die "just-makeit did not install into ${VENV_DIR}"
    ok "just-makeit ${_jm}"
fi

# ── 7. Activate (only works when sourced; print hint otherwise) ───────────────

_ACTIVATE="${VENV_DIR}/bin/activate"

# BASH_SOURCE[0] == "" when piped; differs from $0 when sourced via . <(...)
if [[ "${BASH_SOURCE[0]:-}" != "${0}" ]]; then
    # shellcheck source=/dev/null
    source "$_ACTIVATE"
    printf '\n'
    info "Venv activated — just-makeit is ready:"
    printf '\n    just-makeit new my_project --object my_object\n\n'
else
    printf '\n'
    info "Done. Activate the venv:"
    printf '\n    source %s\n\n' "$_ACTIVATE"
fi
