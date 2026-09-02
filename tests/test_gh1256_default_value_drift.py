"""gh-1256: a retuned init-param default never reached the sacred fragment.

`init_kwargs_drift` already compared the constructor's keyword NAMES, their
ORDER, and (gh-823) which side of the `PyArg_ParseTupleAndKeywords` `|` each
one sits on. None of those three axes reads the *value* the local variable is
seeded with — so a parameter that stays optional, at the same position, with
its manifest `default` simply retuned (`50.0` -> `0.0`) produced identical
names in identical order and an unmoved `|`: no drift by any existing
comparison, while `double cn0 = 50.0;` in the fragment kept the stale value
forever. `jm apply` regenerated the `.pyi` (which advertised the new
default) and even transplanted the runtime docstring's own "default 0.0"
text — so every other signal said the fragment was current while the actual
constructor still built with `cn0 = 50.0` whenever the caller omitted it.

This adds the fourth axis to the one `init_kwargs_drift` implementation (the
`|` axis's own precedent, gh-823) rather than a second, separately-drifting
checker.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._docsync import _init_kwarg_defaults, init_kwargs_drift

_ROWS = "static PyMethodDef X_methods[] = {\n  {NULL}\n};\n"


def _frag(kwlist: str, fmt: str, locals_: str) -> str:
    """A minimal fragment carrying one constructor with seeded locals."""
    return (
        "static int\n"
        "Thing_init(ThingObject *self, PyObject *args, PyObject *kwds)\n"
        "{\n"
        f"    static char *kwlist[] = {{{kwlist}, NULL}};\n"
        f"{locals_}"
        f'    if (!PyArg_ParseTupleAndKeywords(args, kwds, "{fmt}", kwlist,\n'
        "            &fs, &cn0))\n"
        "        return -1;\n"
        "    return 0;\n"
        "}\n"
    ) + _ROWS


class TestTheFourthAxis:
    """The case the three existing axes cannot see."""

    def test_a_retuned_default_is_reported(self):
        existing = _frag(
            '"fs", "cn0"',
            "d|d",
            "    double fs = 1.0;\n    double cn0 = 50.0;\n",
        )
        reference = _frag(
            '"fs", "cn0"',
            "d|d",
            "    double fs = 1.0;\n    double cn0 = 0.0;\n",
        )
        *_, detail = init_kwargs_drift(existing, reference)
        assert detail, "identical names, identical order — and still drift"
        assert "cn0" in detail
        assert "50.0" in detail
        assert "0.0" in detail

    def test_agreement_is_still_silent(self):
        same = _frag(
            '"fs", "cn0"',
            "d|d",
            "    double fs = 1.0;\n    double cn0 = 50.0;\n",
        )
        assert init_kwargs_drift(same, same)[3] == ""

    def test_a_numerically_equal_spelling_is_not_drift(self):
        """`50` and `50.0` are the same C double; a format-only difference
        is not a value the author retuned."""
        existing = _frag(
            '"fs", "cn0"',
            "d|d",
            "    double fs = 1.0;\n    double cn0 = 50;\n",
        )
        reference = _frag(
            '"fs", "cn0"',
            "d|d",
            "    double fs = 1.0;\n    double cn0 = 50.0;\n",
        )
        assert init_kwargs_drift(existing, reference)[3] == ""

    def test_an_unrelated_required_param_is_not_compared(self):
        """`fs` has no default (required, no `=` before `|`) — nothing to
        compare, and it must not be reported as one."""
        existing = _frag(
            '"fs", "cn0"',
            "d|d",
            "    double fs;\n    double cn0 = 50.0;\n",
        )
        reference = _frag(
            '"fs", "cn0"',
            "d|d",
            "    double fs;\n    double cn0 = 0.0;\n",
        )
        *_, detail = init_kwargs_drift(existing, reference)
        assert "fs" not in detail

    def test_the_other_three_axes_still_work(self):
        """Extending the comparison must not cost the axes it already had."""
        *_, detail = init_kwargs_drift(
            _frag('"a", "b"', "dd", "    double a;\n    double b;\n"),
            _frag('"b", "a"', "dd", "    double b;\n    double a;\n"),
        )
        assert "new positional order" in detail

    def test_no_constructor_is_nothing_to_compare(self):
        assert _init_kwarg_defaults(_ROWS) is None


class TestItGates:
    @pytest.fixture()
    def project(self, tmp_path) -> Path:
        from just_makeit._module import run as module_run
        from just_makeit._new import run as new_run
        from just_makeit._object import run as object_run

        root = tmp_path / "proj"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("proj", root)
            module_run(root, "m")
            object_run(
                root,
                "thing",
                "m",
                no_state=True,
                no_step=True,
                init_params=[("cn0", "double", "50.0")],
            )
        return root

    def _status(self, root: Path, check: bool = True) -> tuple:
        from just_makeit._status import run as status_run

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = status_run(root, check=check)
        return rc, buf.getvalue()

    def _retune_the_default(self, root: Path) -> None:
        from just_makeit._apply import run as apply_run

        old = 'name = "cn0"\ntype = "double"\ndefault = "50.0"'
        new = 'name = "cn0"\ntype = "double"\ndefault = "0.0"'
        for toml in root.rglob("*.toml"):
            text = toml.read_text()
            if old in text:
                toml.write_text(text.replace(old, new))
                break
        else:
            raise AssertionError("the init_param block was not found")
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(io.StringIO()):
                apply_run(root)

    def test_a_clean_project_passes(self, project):
        rc, out = self._status(project)
        assert rc == 0
        assert "OK — up to date" in out

    def test_a_retuned_default_fails_the_gate(self, project):
        self._retune_the_default(project)
        rc, out = self._status(project)
        assert rc != 0, "a stale seeded default must fail --check"
        assert "kwargs-drift (!)" in out

    def test_the_report_names_the_old_and_new_value(self, project):
        self._retune_the_default(project)
        _rc, out = self._status(project)
        assert "50.0" in out
        assert "0.0" in out

    def test_the_fragment_itself_was_left_stale(self, project):
        """This is the bug, made concrete: `apply` ran, the .pyi moved, and
        the constructor's own seeded default did not."""
        self._retune_the_default(project)
        frag = project / "native" / "src" / "m" / "m_ext_thing.c"
        assert "cn0 = 50.0" in frag.read_text()
