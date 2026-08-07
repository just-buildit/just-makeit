"""gh-823 Ask B: constructor drift in OPTIONALITY, and it now fails the gate.

`init_kwargs_drift` compared two axes — which keyword names the constructor
accepts, and in what order. Neither reads the `PyArg_ParseTupleAndKeywords`
format string, so the `|` was invisible to it. A parameter that gains a default
in the manifest without moving position produces identical names in identical
order and a moved `|`: no drift by that comparison, while the regenerated
`.pyi` grows a `= ...` the fragment's binding does not honour.

That is a published constructor raising when called as documented, with a type
checker endorsing the failing call. This adds the third axis to the same
comparison — one implementation, three axes, the two presentations it already
has — rather than a second checker that would have to be taught about every
future kwlist spelling.

It also makes the finding *gate*. gh-612 deliberately did not, on the reasoning
that jm cannot regenerate a kwlist on its own. But delete-and-reapply is a real
remedy (the same one gh-815 prescribes), and while this was merely reported the
warning printed correctly on every apply for months inside a block of a dozen
warnings about fragments that were fine, and a broken signature shipped anyway.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._docsync import (
    _fmt_param_count,
    _init_kwarg_optionality,
    init_kwargs_drift,
)

_ROWS = "static PyMethodDef X_methods[] = {\n  {NULL}\n};\n"


def _frag(kwlist: str, fmt: str) -> str:
    """A minimal fragment carrying one constructor with a kwlist and format."""
    return (
        "static int\n"
        "Thing_init(ThingObject *self, PyObject *args, PyObject *kwds)\n"
        "{\n"
        f"    static char *kwlist[] = {{{kwlist}, NULL}};\n"
        f'    if (!PyArg_ParseTupleAndKeywords(args, kwds, "{fmt}", kwlist,\n'
        "            &a))\n"
        "        return -1;\n"
        "    return 0;\n"
        "}\n"
    ) + _ROWS


class TestTheParamCount:
    """A format char is not one-to-one with a parameter."""

    def test_plain_chars(self):
        assert _fmt_param_count("dKs") == 3

    def test_a_path_converter_is_one_param(self):
        """`O&` — two characters, one parameter. Counting characters would
        put the `|` boundary one place too far right."""
        assert _fmt_param_count("O&d") == 2

    def test_bytes_is_one_param(self):
        assert _fmt_param_count("y#d") == 2

    def test_partition_markers_are_not_params(self):
        assert _fmt_param_count("d|K") == 2
        assert _fmt_param_count("d$K") == 2


class TestTheOptionalitySplit:
    def test_all_required(self):
        got = _init_kwarg_optionality(_frag('"fs", "n"', "dK"))
        assert got == (("fs", "n"), ())

    def test_split_at_the_bar(self):
        got = _init_kwarg_optionality(_frag('"fs", "n"', "d|K"))
        assert got == (("fs",), ("n",))

    def test_the_bar_lands_right_after_a_path_param(self):
        """The regression `_fmt_param_count` exists for: with `O&` counted as
        two, `p` would be read as optional and `fs` as required."""
        got = _init_kwarg_optionality(_frag('"p", "fs", "n"', "O&d|K"))
        assert got == (("p", "fs"), ("n",))

    def test_no_constructor_is_nothing_to_compare(self):
        assert _init_kwarg_optionality(_ROWS) is None


class TestTheThirdAxis:
    """The case the two name axes cannot see."""

    def test_a_param_that_became_optional_is_reported(self):
        existing = _frag('"fs", "n"', "dK")
        reference = _frag('"fs", "n"', "d|K")
        *_, detail = init_kwargs_drift(existing, reference)
        assert detail, "identical names in identical order — and still drift"
        assert "n" in detail
        assert "omittable" in detail

    def test_a_param_that_became_required_is_reported(self):
        existing = _frag('"fs", "n"', "d|K")
        reference = _frag('"fs", "n"', "dK")
        *_, detail = init_kwargs_drift(existing, reference)
        assert "n" in detail
        assert "required" in detail

    def test_it_names_the_parameter_that_moved_not_its_neighbour(self):
        """Compared by name, not position. On this class the reordering *is*
        the drift, so a positional comparison names the wrong parameter."""
        existing = _frag('"fs", "n"', "dK")
        reference = _frag('"fs", "n"', "d|K")
        *_, detail = init_kwargs_drift(existing, reference)
        assert "n" in detail
        assert "fs" not in detail.split("omittable")[-1]

    def test_agreement_is_still_silent(self):
        same = _frag('"fs", "n"', "d|K")
        assert init_kwargs_drift(same, same)[3] == ""

    def test_the_name_axes_still_work(self):
        """Extending the comparison must not cost the two it already had."""
        *_, detail = init_kwargs_drift(
            _frag('"a", "b"', "dK"), _frag('"b", "a"', "dK")
        )
        assert "new positional order" in detail


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
                init_params=[
                    ("fs", "double", "", "", "", "", False, "", True),
                    ("n", "size_t", "", "", "", "", False, "", True),
                ],
            )
        return root

    def _status(self, root: Path, check: bool = True) -> tuple:
        from just_makeit._status import run as status_run

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = status_run(root, check=check)
        return rc, buf.getvalue()

    def _give_n_a_default(self, root: Path) -> None:
        from just_makeit._apply import run as apply_run

        old = 'name = "n"\ntype = "size_t"\nrequired = true'
        new = 'name = "n"\ntype = "size_t"\ndefault = "1024"'
        # Split-layout puts the section in whichever fragment owns it; find
        # it rather than assuming, so the fixture does not silently no-op.
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

    def test_optionality_drift_fails_the_gate(self, project):
        self._give_n_a_default(project)
        rc, out = self._status(project)
        assert rc != 0, "a drifted constructor must fail --check"
        assert "kwargs-drift (!)" in out

    def test_it_does_not_claim_to_be_up_to_date(self, project):
        """The headline is what a reader takes away, and it said fine."""
        self._give_n_a_default(project)
        _rc, out = self._status(project)
        assert "OK — up to date" not in out

    def _allowlist(self, project: Path) -> None:
        manifest = project / "just-makeit.toml"
        manifest.write_text(
            manifest.read_text().replace(
                "[project]",
                '[project]\nstatus_allow = ["native/src/m/m_ext_thing.c"]',
                1,
            )
        )

    def test_status_allow_exempts_it(self, project):
        """The escape hatch has to be real, or the gate is an opt-out that
        does not exist. Same matcher as every other allowed path."""
        self._give_n_a_default(project)
        assert self._status(project)[0] != 0
        self._allowlist(project)
        assert self._status(project)[0] == 0

    def test_an_allowed_entry_is_still_listed(self, project):
        """Exempt is not invisible. Dropping the entry on the allow verdict
        removed it from the report entirely: nothing named the file, so the
        exemptions could not be audited and one that had stopped diverging
        would sit there forever — the failure mode of this issue, one level
        up."""
        self._give_n_a_default(project)
        self._allowlist(project)
        _rc, out = self._status(project)
        assert "native/src/m/m_ext_thing.c" in out, "the file must be named"
        assert "allowed by status_allow" in out
        assert "~ native" in out, "marked as not-counted, like ALLOWED does"

    def test_an_allowed_entry_still_qualifies_the_summary(self, project):
        """A bare "OK — up to date" over a constructor the generator
        disagrees with is the claim gh-767 established jm must not make.
        Exempt from the gate is not the same as in sync."""
        self._give_n_a_default(project)
        self._allowlist(project)
        _rc, out = self._status(project)
        assert "kwargs-drift (allowed)" in out
        assert "OK — up to date" in out, "it is still OK — just qualified"

    def test_the_allowed_one_does_not_gate(self, project):
        """...and the marker distinguishes the two: `!` gates, `~` does not."""
        self._give_n_a_default(project)
        self._allowlist(project)
        rc, out = self._status(project)
        assert rc == 0
        assert "! native" not in out
