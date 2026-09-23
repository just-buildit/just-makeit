"""gh-1514: every state type either scaffolds green on both faces or is refused.

``--state name:'const char *':NULL`` was accepted and produced a project that
failed three ways: the C test assigned an integer to a pointer
(``-Wint-conversion``, an error on GCC 14+), the getter passed the ``NULL``
default to ``PyUnicode_FromString`` and segfaulted the Python suite, and the
setter stored a pointer into the caller's ``str`` -- freed memory once that
string is collected. ``docs/types.md`` has always said a string is not a state
type; now ``_types.state_type_error`` says so, for the CLI and ``apply`` alike.

Measuring the class found a second member: a fixed state array ``T[N]`` read
its NumPy enum from a hand-kept table that had drifted from the array-param
one, so ``size_t[2]`` -- a documented array element -- crashed the scaffold
with ``KeyError: 'size_t'``, and ``bool[4]`` crashed the same way instead of
being refused.

The matrix is REGISTRATION-FREE: it walks ``_CTYPE_META``, each key as a
scalar and as the element of ``T[2]``, so a type added to the registry is held
to this without anyone remembering to list it. For each type and each object
shape (standalone, module object, header-only):

- if ``state_type_error`` refuses it, the CLI exits non-zero with that exact
  message and no traceback, and so does ``apply`` from a manifest entry;
- if it accepts it, the object is in ONE project that is built and tested
  once: CTest, then the generated Python suite, with no failure, error or
  skip, and exactly as many passes as the generated test files define.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from _jmrun import run_cli
from test_gh1109_seeded_construction_is_attempted import _pytest_counts

from just_makeit._types import _CTYPE_META, state_type_error

#: Every registered type as a scalar, then as a fixed-array element.
_TYPES = list(_CTYPE_META) + [f"{t}[2]" for t in _CTYPE_META]

#: (flags, shape tag). The tag prefixes the object name.
_SHAPES = (((), "s"), (("--module", "m"), "m"), (("--header-only",), "h"))

_NO_TOOLCHAIN = shutil.which("cmake") is None or (
    shutil.which("cc") is None and shutil.which("gcc") is None
)


@pytest.fixture(scope="module")
def matrix(tmp_path_factory) -> "tuple[Path, list[tuple[str, str, object]]]":
    """One project holding one object per (accepted type, shape).

    Returns the project root and ``(type, object name, JmRun)`` for every
    scaffold attempted, accepted or refused.
    """
    tmp = tmp_path_factory.mktemp("gh1514")
    assert run_cli("new", "mx", cwd=tmp).returncode == 0
    root = tmp / "mx"
    assert run_cli("module", "m", cwd=root).returncode == 0
    runs = []
    for i, ctype in enumerate(_TYPES):
        for flags, tag in _SHAPES:
            name = f"{tag}{i}"
            out = run_cli(
                "object",
                name,
                "--no-step",
                "--state",
                f"v:{ctype}",
                *flags,
                cwd=root,
            )
            runs.append((ctype, name, out))
    return root, runs


def test_the_matrix_refuses_something_and_accepts_the_rest(matrix) -> None:
    """Armed: the registry has refused types and accepted ones.

    A matrix whose every row took the same branch would pass the two checks
    below while testing only half the rule.
    """
    verdicts = {state_type_error("v", t) is None for t in _TYPES}
    assert verdicts == {True, False}
    assert state_type_error("v", "const char *") is not None
    assert state_type_error("v", "size_t[2]") is None


def test_a_refused_type_is_refused_cleanly_by_the_cli(matrix) -> None:
    """Non-zero exit, the predicate's own message, never a traceback."""
    _, runs = matrix
    for ctype, name, out in runs:
        why = state_type_error("v", ctype)
        if why is None:
            assert out.returncode == 0, (ctype, name, out.stderr)
            continue
        assert out.returncode != 0, (ctype, name)
        assert why.splitlines()[0] in out.stderr, (ctype, out.stderr)
        assert "Traceback" not in out.stderr, (ctype, out.stderr)


@pytest.mark.parametrize(
    "ctype", [t for t in _TYPES if state_type_error("v", t) is not None]
)
def test_a_refused_type_is_refused_cleanly_by_apply(
    tmp_path: Path, ctype: str
) -> None:
    """The manifest path gives the same answer as the flag.

    ``apply`` reaches ``make_state_ctx`` without ever parsing ``--state``, so
    a refusal living only in the CLI parser would let the same field in
    through a hand-written ``[[obj.state]]`` entry.
    """
    assert run_cli("new", "pp", "--object", "a", cwd=tmp_path).returncode == 0
    root = tmp_path / "pp"
    fragment = root / "objects" / "a.toml"
    fragment.write_text(
        fragment.read_text(encoding="utf-8")
        + f'\n[[a.state]]\nname = "v"\ntype = "{ctype}"\n',
        encoding="utf-8",
    )
    out = run_cli("apply", cwd=root)
    text = out.stdout + out.stderr
    assert out.returncode != 0, text
    assert state_type_error("v", ctype).splitlines()[0] in text
    assert "Traceback" not in text


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
@pytest.mark.slow
def test_every_accepted_type_builds_and_passes_both_faces(matrix) -> None:
    """One build, one CTest run, one generated Python suite, all green."""
    root, runs = matrix
    accepted = [name for ctype, name, out in runs if out.returncode == 0]
    assert accepted
    out = run_cli("test", cwd=root)
    assert out.returncode == 0, out.stdout[-4000:] + out.stderr[-2000:]
    counts = _pytest_counts(out.stdout)
    # Derived from what jm wrote, not restated: every generated test
    # function must have run and passed, so a file that silently collected
    # nothing (or an object whose tests were skipped) cannot hide here.
    defined = sum(
        len(
            re.findall(
                r"^\s+def test_\w+\(", p.read_text(encoding="utf-8"), re.M
            )
        )
        for name in accepted
        for p in root.glob(f"src/mx/**/tests/test_{name}.py")
    )
    assert defined >= 3 * len(accepted), defined
    assert counts.get("failed", 0) == 0, counts
    assert counts.get("error", 0) + counts.get("errors", 0) == 0, counts
    assert counts.get("skipped", 0) == 0, counts
    assert counts.get("passed") == defined, (counts, defined)
