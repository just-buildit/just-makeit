"""jm's OWN CI matrix agrees with the floor it advertises.

Not the matrix `jm ci` generates for a scaffolded project -- that is
`test_ci.py`. This is `.github/workflows/ci.yml` in this repo, which had no
gate at all.

It gained one because the matrix gained an `exclude`. Narrowing which OS runs
a Python version is a legitimate cost decision; **removing a version from the
matrix entirely while `requires-python` still promises it** is a silent lie,
and an `exclude` block is one line away from doing exactly that. The two
declarations -- `pyproject.toml`'s floor and this matrix -- have to agree in
both directions, and nothing was holding them.

Parsed by hand rather than with PyYAML, following `test_docs.py`: PyYAML is
not a declared dependency, only ever a transitive one, and the CI test legs
install just the package under test. A bare `import yaml` here is a collection
error that fails the whole matrix, and the alternative -- skip when
unavailable -- would mean this gate silently never runs in CI, which is the
class of bug it exists to stop.

Every parser below RAISES rather than returning empty. A parser that quietly
finds nothing makes every assertion in the file vacuously true, which is how a
gate ends up passing for months over a thing it stopped being able to see.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT = ROOT / "pyproject.toml"

_LIST = re.compile(
    r"^\s*(?P<key>os|python-version):\s*\[(?P<items>[^\]]*)\]\s*$"
)
_EXCLUDE = re.compile(r"^(?P<indent>\s*)exclude:\s*$")
_PAIR_OS = re.compile(r"^\s*-\s*os:\s*(?P<v>\S+)\s*$")
_PAIR_PY = re.compile(r"^\s*python-version:\s*(?P<v>\S+)\s*$")


def _unquote(s: str) -> str:
    return s.strip().strip('"').strip("'")


def _axes() -> tuple[list[str], list[str]]:
    """The `os` and `python-version` axis lists, as declared."""
    found: dict[str, list[str]] = {}
    for line in CI_YML.read_text(encoding="utf-8").splitlines():
        m = _LIST.match(line)
        if m and m.group("key") not in found:
            found[m.group("key")] = [
                _unquote(x) for x in m.group("items").split(",") if x.strip()
            ]
    missing = {"os", "python-version"} - set(found)
    if missing:
        raise AssertionError(
            f"could not find the {sorted(missing)} matrix axis in {CI_YML} -- "
            "the flow-sequence shape this parses moved, so every assertion "
            "in this file would have been vacuous"
        )
    return found["os"], found["python-version"]


def _excludes() -> set[tuple[str, str]]:
    """The `(os, python-version)` pairs the matrix excludes. May be empty --
    unlike the axes, an absent `exclude:` is a legitimate state, so this is
    the one parser allowed to return nothing."""
    out: set[tuple[str, str]] = set()
    lines = CI_YML.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if not _EXCLUDE.match(line):
            continue
        indent = len(_EXCLUDE.match(line).group("indent"))
        cur: str | None = None
        for nxt in lines[i + 1 :]:
            if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                break  # dedented out of the exclude block
            mo, mp = _PAIR_OS.match(nxt), _PAIR_PY.match(nxt)
            if mo:
                cur = _unquote(mo.group("v"))
            elif mp and cur:
                out.add((cur, _unquote(mp.group("v"))))
                cur = None
    return out


def _floor() -> str:
    """`requires-python = ">=3.9"` -> `"3.9"`."""
    for ln in PYPROJECT.read_text(encoding="utf-8").splitlines():
        if ln.startswith("requires-python"):
            return _unquote(ln.split(">=")[1])
    raise AssertionError(f"no `requires-python` line in {PYPROJECT}")


def _key(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def test_the_matrix_floor_is_the_declared_floor() -> None:
    """`requires-python` promises a version; the matrix has to test it.

    Both directions. Raising the floor without dropping the leg leaves CI
    testing an interpreter the package refuses to install on; dropping the leg
    without raising the floor ships an untested promise.
    """
    _, pys = _axes()
    assert min(pys, key=_key) == _floor(), (
        f'`requires-python = ">={_floor()}"` but the CI matrix\'s oldest '
        f"Python is {min(pys, key=_key)}"
    )


def test_every_supported_python_is_tested_on_some_os() -> None:
    """The gate the `exclude` block exists under.

    Narrowing a version to fewer OSes is a cost decision and allowed. Removing
    it from every OS is not -- that is an untested promise, and one more
    `exclude:` entry is all it takes.
    """
    oses, pys = _axes()
    excl = _excludes()
    orphaned = [p for p in pys if all((o, p) in excl for o in oses)]
    assert not orphaned, (
        f"these Python versions are excluded on EVERY os, so nothing tests "
        f"them: {orphaned}. Either restore a leg or raise `requires-python`."
    )


def test_every_os_still_runs_something() -> None:
    """The same argument along the other axis."""
    oses, pys = _axes()
    excl = _excludes()
    orphaned = [o for o in oses if all((o, p) in excl for p in pys)]
    assert not orphaned, f"these OSes run no leg at all: {orphaned}"


def test_an_exclude_names_a_real_cell() -> None:
    """An `exclude` for a pair the axes cannot produce is dead config -- it
    silently does nothing, and reads as coverage having been deliberately
    dropped when it never was."""
    oses, pys = _axes()
    bogus = [
        (o, p)
        for (o, p) in sorted(_excludes())
        if o not in oses or p not in pys
    ]
    assert not bogus, f"exclude names a cell not in the matrix: {bogus}"


class TestTheParsersAreArmed:
    """A scan that finds nothing must be proven able to find something."""

    def test_the_axes_are_actually_read(self) -> None:
        oses, pys = _axes()
        assert "ubuntu-latest" in oses and "3.9" in pys

    def test_the_exclude_parser_sees_the_live_one(self) -> None:
        """Pinned to the real entry: if the exclude is removed this fails, and
        the coverage tests above would otherwise go quietly vacuous the moment
        the block's shape changed."""
        assert ("macos-latest", "3.9") in _excludes()

    def test_a_moved_axis_raises_rather_than_passing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The vacuity guard itself. Point the parser at a file with no matrix
        and it must raise, not return empty and let every test above pass."""
        empty = tmp_path / "ci.yml"
        empty.write_text("name: CI\njobs: {}\n", encoding="utf-8")
        monkeypatch.setattr("test_own_ci_matrix.CI_YML", empty)
        with pytest.raises(AssertionError, match="matrix axis"):
            _axes()
