"""Every C accessor jm declares for a property gets a body (gh-1303).

gh-1303 asked for a `status` gate on "declared, called, never defined", and
withdrew it: it fired on every property accessor the author had not written
yet, which `jm property` treated as a legitimate state. Re-measured on 0.76.5,
that state is not legitimate -- it is broken. `jm property o level --type
double` left a project that BUILT (a shared object links with undefined
symbols) and then failed its own ``make test`` at import::

    ImportError: .../o.cpython-312-...so: undefined symbol: o_get_level

`jm method` has always appended a no-op stub; accessors were the member kind
that did not, on every path: plain getter/setter, container count/key/value,
and a header-only component. They now get one too. With that, "declared and
not defined" is never a state jm leaves a project in, so gh-1294's existing
machinery covers accessors with no new gate: the manifest's render defines
them, `apply` splices a missing one back, and `status --check` fails on it.

The compiled class is the one that settles it. Text assertions cannot see an
undefined symbol; `jm test` imports the module.
"""

from __future__ import annotations

import contextlib
import io
import re
import shutil
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _status  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402

from _jmrun import JmRun, run_cli

_HAVE_TOOLCHAIN = bool(shutil.which("cmake")) and any(
    shutil.which(c) for c in ("cc", "gcc", "clang")
)


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _cli(*args, cwd) -> JmRun:
    # gh-1374: in THIS process -- the child bought isolation only.
    return run_cli(*args, cwd=cwd)


def _project(tmp_path: Path, shape: str) -> Path:
    """An object `o` and one property of *shape*, exactly as the CLI makes."""
    root = tmp_path / "p"
    _quiet(new_run, "p", root)
    _quiet(
        object_run,
        root,
        "o",
        None,
        state_vars=[("n", "int", "0")],
        header_only=(shape == "header_only"),
    )
    if shape == "list":
        _quiet(
            property_run,
            root,
            "o",
            "taps",
            None,
            "list",
            False,
            value_type="double",
        )
    else:
        _quiet(property_run, root, "o", "level", None, "double", True)
    return root


def _body_file(root: Path, shape: str) -> Path:
    if shape == "header_only":
        return root / "native" / "inc" / "o" / "o_core.h"
    return root / "native" / "src" / "o" / "o_core.c"


_SYMBOLS = {
    "plain": ["o_get_level", "o_set_level"],
    "header_only": ["o_get_level", "o_set_level"],
    "list": ["o_num_taps", "o_taps_value"],
}
SHAPES = sorted(_SYMBOLS)


class TestTheBodyIsScaffolded:
    @pytest.mark.parametrize("shape", SHAPES)
    def test_every_accessor_has_a_marked_stub(self, tmp_path, shape):
        text = _body_file(_project(tmp_path, shape), shape).read_text()
        for sym in _SYMBOLS[shape]:
            assert f"/* <<IMPLEMENT: {sym}>> */" in text, sym
            assert re.search(
                rf"^(static inline )?\w[\w *]*\n{sym}\(", text, re.M
            ) or re.search(rf"^{sym}\(", text, re.M), sym

    def test_header_only_carries_no_prototype_ahead_of_it(self, tmp_path):
        """A non-static declaration before a `static inline` definition does
        not compile -- which is why bodies are written before declarations."""
        h = _body_file(_project(tmp_path, "header_only"), "header_only")
        text = h.read_text(encoding="utf-8")
        assert not re.search(r"^double o_get_level\(.*\);$", text, re.M)

    def test_an_accessor_the_author_already_wrote_is_left_alone(
        self, tmp_path
    ):
        """gh-1328: a definition may live in a sibling source; never add a
        second one."""
        root = tmp_path / "p"
        _quiet(new_run, "p", root)
        _quiet(object_run, root, "o", None, state_vars=[("n", "int", "0")])
        (root / "native" / "src" / "o" / "o_level.c").write_text(
            '#include "o/o_core.h"\n'
            "double\no_get_level(const o_state_t *state)\n"
            "{\n    return (double)state->n;\n}\n",
            encoding="utf-8",
        )
        _quiet(property_run, root, "o", "level", None, "double", False)
        core = (root / "native" / "src" / "o" / "o_core.c").read_text()
        assert "o_get_level" not in core


class TestTheExistingGateNowCoversIt:
    """No new gate: gh-1294's splice and status check see accessors."""

    def _strip_getter(self, root: Path) -> None:
        path = _body_file(root, "plain")
        text = path.read_text(encoding="utf-8")
        stripped = re.sub(
            r"/\* <<IMPLEMENT: o_get_level>> \*/\ndouble\no_get_level\(.*?"
            r"\n\}\n",
            "",
            text,
            flags=re.S,
        )
        assert "o_get_level" not in stripped
        path.write_text(stripped, encoding="utf-8")

    def test_status_check_fails_on_a_missing_accessor(self, tmp_path):
        root = _project(tmp_path, "plain")
        assert _quiet(_status.run, root, check=True) == 0
        self._strip_getter(root)
        assert _quiet(_status.run, root, check=True) != 0

    def test_apply_puts_it_back(self, tmp_path):
        root = _project(tmp_path, "plain")
        self._strip_getter(root)
        _quiet(apply_run, root)
        assert "o_get_level(" in _body_file(root, "plain").read_text()
        assert _quiet(_status.run, root, check=True) == 0


@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="needs cmake and a C compiler")
class TestTheScaffoldPassesItsOwnSuite:
    """The claim that matters, and the only kind of test that can see it."""

    @pytest.mark.parametrize("shape", SHAPES)
    def test_it_builds_imports_and_passes(self, tmp_path, shape):
        root = _project(tmp_path, shape)
        r = _cli("test", cwd=root)
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]

    def test_without_the_stub_it_does_not(self, tmp_path):
        """The sabotage as a test: the stub is what the import was missing."""
        root = _project(tmp_path, "plain")
        TestTheExistingGateNowCoversIt()._strip_getter(root)
        r = _cli("test", cwd=root)
        assert r.returncode != 0
        assert "undefined symbol" in r.stdout + r.stderr or (
            "o_get_level" in r.stdout + r.stderr
        )
