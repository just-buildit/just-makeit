"""gh-2582: sourcing install.sh leaves the caller's shell as it found it.

The README's one-liner is ``. <(curl -fsSL .../install.sh)``: it SOURCES the
installer, so the script runs in the caller's own shell. install.sh opened
with ``set -euo pipefail`` at the top level, so:

- every failure path closed the terminal: ``die`` and ``--check``'s verdict
  ``return 1``, and the errexit the script had just turned on made that the
  shell's exit;
- a successful install left ``set -euo pipefail`` behind, so the next failing
  command, or a prompt hook reading an unset variable, closed it later;
- ``info``, ``ok``, ``warn``, ``VENV_DIR``, ``MGR``... stayed defined
  (``info`` shadowing GNU info).

The fix runs everything that can stop inside ``_jm_install``, a function
whose body is a subshell; the top level only activates the venv and unsets
every name it defined. This file sources the installer from a parent bash,
on each way out, and compares the parent's options, shopts, functions and
variables before and after.

The machine is `test_gh1972_installers_patchelf.Box`: stub tools and a stub
venv that already holds the current just-makeit, so even the path that
installs runs offline and writes nothing outside ``tmp_path``.

GATE: sourcing install.sh -- failing, refusing a flag, ``--check`` and
      installing -- returns to the caller's shell with a status, never exits
      it, and leaves its options and namespace unchanged but for the venv's
      own activation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from _installers import INSTALL_SH
from test_gh1972_installers_patchelf import BASH, Box

pytestmark = pytest.mark.skipif(not BASH, reason="install.sh is a bash script")

#: What the stub venv's ``activate`` sets, the one change a successful
#: source may make to the caller's shell.
ACTIVATED = "JM_TEST_ACTIVATED"

#: The caller's shell. ``_snap`` prints its options, shopts, functions and
#: variable names with builtins alone (the Box PATH has no grep or diff),
#: skipping the ones bash itself changes on every command. ``set +o``
#: includes pipefail and nounset, so the original bug fails here on the
#: options as well as on the exit.
_PARENT = r"""
_snap() {
    set +o
    shopt -p
    declare -F
    local v
    for v in $(compgen -v); do
        case "$v" in
            _|v|BASH*|PIPESTATUS|FUNCNAME|LINENO|RANDOM|SRANDOM|SECONDS) ;;
            EPOCH*|COLUMNS|LINES|_snap_*) ;;
            *) echo "var $v" ;;
        esac
    done
}
_snap_before="$(_snap)"
. "$1" "${@:2}"
_snap_status=$?
_snap_after="$(_snap)"
printf '%s\n' "@@status $_snap_status" "@@before" "$_snap_before" \
    "@@after" "$_snap_after" "@@end"
"""


def _source(box: Box, *args: str, python: "str | None" = None):
    """Source install.sh with *args* from a parent bash on *box*."""
    env = box.env()
    if python is not None:
        env["PYTHON"] = python
    return subprocess.run(
        [BASH, "-c", _PARENT, "parent", str(INSTALL_SH), *args],
        cwd=box.root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _parse(res) -> "tuple[int, set, set]":
    """``(status, before, after)`` from the parent's report.

    No report means the parent shell exited inside the source: the bug.
    """
    out = res.stdout
    assert "@@end" in out, (
        "sourcing install.sh exited the caller's shell, which is the "
        f"terminal when the README's one-liner runs it (gh-2582):\n"
        f"exit={res.returncode}\n{out}{res.stderr}"
    )
    status = int(out.split("@@status ", 1)[1].split("\n", 1)[0])
    before = out.split("@@before\n", 1)[1].split("@@after\n", 1)[0]
    after = out.split("@@after\n", 1)[1].split("@@end", 1)[0]
    return status, set(before.splitlines()), set(after.splitlines())


def _assert_unchanged(before: set, after: set, allowed: set = frozenset()):
    added, removed = after - before - allowed, before - after
    assert not added and not removed, (
        "sourcing install.sh changed the caller's shell (gh-2582):\n"
        f"  added:   {sorted(added)}\n  removed: {sorted(removed)}"
    )


def test_a_failure_returns_to_the_shell(tmp_path: Path):
    box = Box(tmp_path, "Linux")
    res = _source(box, str(box.root / "venv"), python="/nonexistent/python3")
    status, before, after = _parse(res)
    assert status != 0, res.stdout
    assert "Python not found" in res.stderr, res.stderr
    _assert_unchanged(before, after)


def test_an_unknown_flag_returns_to_the_shell(tmp_path: Path):
    box = Box(tmp_path, "Linux")
    res = _source(box, "--bogus")
    status, before, after = _parse(res)
    assert status != 0, res.stdout
    assert "Unknown flag: --bogus" in res.stderr, res.stderr
    _assert_unchanged(before, after)


@pytest.mark.parametrize(
    "without", [(), ("patchelf",)], ids=["current", "missing"]
)
def test_check_returns_its_verdict_to_the_shell(tmp_path: Path, without):
    box = Box(tmp_path, "Linux", without=without)
    res = _source(box, "--check", str(box.root / "venv"))
    status, before, after = _parse(res)
    assert status == (1 if without else 0), (res.stdout, res.stderr)
    _assert_unchanged(before, after)
    assert box.calls() == [], "--check must install nothing"


def test_installing_activates_and_changes_nothing_else(tmp_path: Path):
    box = Box(tmp_path, "Linux")
    activate = box.root / "venv" / "bin" / "activate"
    activate.write_text(f"{ACTIVATED}=1\n", encoding="utf-8")
    res = _source(box, str(box.root / "venv"))
    status, before, after = _parse(res)
    assert status == 0, (res.stdout, res.stderr)
    assert "Venv activated" in res.stdout, res.stdout
    assert f"var {ACTIVATED}" in after, "the venv was not activated"
    _assert_unchanged(before, after, allowed={f"var {ACTIVATED}"})
