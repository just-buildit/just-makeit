"""gh-761: ``*_max_out``'s arity comes from the C prototype, not the method.

gh-607 gave every ``*_max_out`` accessor a trailing count parameter, mirroring
the count the binding passes to the kernel. For most kernels that is wrong:
the generated binding requires ``capacity >= max(max_out(state), L)``, so
``max_out()`` answers exactly one question — *can this method emit more than
it is given?* When it cannot, ``0`` is the exact and complete bound and there
is nothing about the caller's block for the function to know.

doppler has 65 such implementations and they split cleanly: 26 return ``0``
because output is bounded by the caller's length (one symbol per ``sps``
inputs, one output per ``decim`` inputs, one sample per sample); 39 return a
real bound because it is not (``fft`` zero-pads to ``state->n``, ``corr``
returns ``n_out`` longer than either input, generators have no ``L`` at all).
The manifest records the *method's* shape, which says nothing about what its
``_max_out`` sibling needs — the header does.

**The mechanism in the issue as filed was backwards, and worth recording.**
It reported that the stub ignored the prototype while the binding respected
it. jm was in fact internally consistent: regenerate a binding from scratch
and it emits ``METH_VARARGS`` with ``x_len``, exactly matching the stub. What
diverged was doppler's *committed* ``<mod>_ext_<obj>.c``, frozen pre-gh-607 at
``METH_NOARGS`` — `apply` only materializes those files when missing, so the
stub moved with jm and the binding beside it did not (gh-767). Diagnosing from
a committed tree instead of a regenerated one is what hid it from both sides.

Measured after this change, on doppler: 71 prototypes (67 state-only, 4
length-bearing), 61 stub accessors, **0 disagreeing with their prototype**.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._docstring import (  # noqa: E402
    max_out_arity_key,
    max_out_is_state_only,
    scan_max_out_arity,
)
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._pyfmt import flatten_signatures  # noqa: E402


class TestScanMaxOutArity:
    """The parser, against the shapes a real header actually contains."""

    def test_state_only_prototype_is_detected(self):
        assert scan_max_out_arity(
            "size_t ddcr_execute_max_out(ddcr_state_t *s);"
        ) == frozenset({"ddcr_execute_max_out"})

    def test_length_bearing_prototype_is_not(self):
        assert (
            scan_max_out_arity(
                "size_t ddc_execute_max_out(ddc_state_t *state, size_t x_len);"
            )
            == frozenset()
        )

    def test_tolerates_a_projects_own_formatting(self):
        """doppler's headers are GNU-formatted; jm's are not."""
        assert scan_max_out_arity(
            "  size_t  ddcr_execute_max_out (ddcr_state_t *s) ;"
        ) == frozenset({"ddcr_execute_max_out"})

    def test_const_qualified_state_is_still_state_only(self):
        assert scan_max_out_arity(
            "size_t fft_execute_max_out(const fft_state_t *s);"
        ) == frozenset({"fft_execute_max_out"})

    def test_both_forms_in_one_header(self):
        text = (
            "size_t ddc_execute_max_out(ddc_state_t *s, size_t x_len);\n"
            "size_t ddcr_execute_max_out(ddcr_state_t *s);\n"
            "size_t delay_ptr_max_out(delay_state_t *s, size_t n);\n"
        )
        assert scan_max_out_arity(text) == frozenset({"ddcr_execute_max_out"})

    def test_an_undeclared_max_out_is_not_state_only(self):
        """No prototype means no opinion — gh-607's default must survive.

        A method jm is scaffolding for the first time has nothing to read, and
        silently switching it to the zero-arg form would change the contract
        for every new project.
        """
        assert not max_out_is_state_only({}, "widget_run_max_out")
        assert not max_out_is_state_only(None, "widget_run_max_out")

    def test_lookup_uses_the_reserved_key(self):
        blocks = {max_out_arity_key(): frozenset({"w_run_max_out"})}
        assert max_out_is_state_only(blocks, "w_run_max_out")
        assert not max_out_is_state_only(blocks, "w_other_max_out")


def _project_with_variable_output(root: Path, module: str | None = None):
    """A component whose method has an array param and variable output."""
    new_run("proj", root, fragments=True)
    if module:
        module_run(root, module)
    object_run(root, "widget", module, state_vars=[("gain", "double", "1.0")])
    method_run(
        root,
        "widget",
        "run",
        module,
        "void",
        "float _Complex",
        True,
        [],
        params=[("x", "float _Complex[]")],
    )


def _make_state_only(root: Path, comp: str, meth: str) -> None:
    """Rewrite the header's ``_max_out`` prototype to take only the state."""
    h = root / "native" / "inc" / comp / f"{comp}_core.h"
    text = h.read_text(encoding="utf-8")
    import re

    text = re.sub(
        rf"size_t {comp}_{meth}_max_out\s*\([^)]*\)",
        f"size_t {comp}_{meth}_max_out({comp}_state_t *state)",
        text,
    )
    h.write_text(text, encoding="utf-8")


class TestStandaloneFacesAgree:
    """Binding, header decl and stub all follow the one prototype."""

    def test_default_keeps_the_count_parameter(self, tmp_path):
        root = tmp_path / "proj"
        _project_with_variable_output(root)
        pyi = flatten_signatures(
            (root / "src" / "proj" / "widget.pyi").read_text()
        )
        assert "def run_max_out(self, x_len: int) -> int:" in pyi

    def test_a_state_only_prototype_drops_it_everywhere(self, tmp_path):
        from just_makeit._apply import run as apply_run

        root = tmp_path / "proj"
        _project_with_variable_output(root)
        _make_state_only(root, "widget", "run")
        apply_run(root)

        pyi = flatten_signatures(
            (root / "src" / "proj" / "widget.pyi").read_text()
        )
        ext = (root / "native" / "src" / "widget" / "widget_ext.c").read_text()

        # The stub agrees with the prototype...
        assert "def run_max_out(self) -> int:" in pyi
        assert "def run_max_out(self, x_len: int)" not in pyi
        # ...and so does the binding it ships beside.
        assert "METH_NOARGS" in ext.split('"run_max_out"')[1][:120]

    def test_apply_does_not_rewrite_the_authors_prototype(self, tmp_path):
        """jm splices these decls back into the sacred header (gh-632's
        neighbourhood). Re-declaring the count form would silently revert the
        author's own signature."""
        from just_makeit._apply import run as apply_run

        root = tmp_path / "proj"
        _project_with_variable_output(root)
        _make_state_only(root, "widget", "run")
        h = root / "native" / "inc" / "widget" / "widget_core.h"
        before = h.read_text(encoding="utf-8")
        apply_run(root)

        assert "widget_run_max_out(widget_state_t *state)" in h.read_text(
            encoding="utf-8"
        )
        assert scan_max_out_arity(h.read_text(encoding="utf-8")) == (
            scan_max_out_arity(before)
        )


class TestModuleAndViewFacesAgree:
    """The two paths that diverged on doppler."""

    def test_module_aggregated_stub_follows_the_prototype(self, tmp_path):
        from just_makeit._apply import run as apply_run

        root = tmp_path / "proj"
        _project_with_variable_output(root, module="dsp")
        _make_state_only(root, "widget", "run")
        apply_run(root)

        pyi = flatten_signatures(
            (root / "src" / "proj" / "dsp" / "dsp.pyi").read_text()
        )
        assert "def run_max_out(self) -> int:" in pyi
        assert "def run_max_out(self, x_len: int)" not in pyi

    def test_a_view_follows_its_parents_prototype(self, tmp_path):
        """gh-761: a view looks its blocks up under a synthetic id, so the
        arity set has to be re-keyed *and* survive the parent-wins merge.
        Missing either left `MatchedDdcr` declaring a count its parent did
        not — the same disagreement one level down.
        """
        from just_makeit._apply import run as apply_run
        from just_makeit._view import run as view_run

        root = tmp_path / "proj"
        _project_with_variable_output(root, module="dsp")
        view_run(root, "widget", "Matched", "dsp", "widget_create_matched")
        _make_state_only(root, "widget", "run")
        apply_run(root)

        pyi = flatten_signatures(
            (root / "src" / "proj" / "dsp" / "dsp.pyi").read_text()
        )
        # Both the parent class and the view.
        assert pyi.count("def run_max_out(self) -> int:") >= 2
        assert "def run_max_out(self, x_len: int)" not in pyi


class TestStubAndBindingCannotDisagree:
    """The invariant, stated once, over whatever the project happens to be.

    Standalone only, deliberately. The module path's binding lives in
    ``<mod>_ext_<obj>.c``, which `apply` does not refresh (gh-767) — so on a
    module object the stub follows the prototype and the binding stays
    frozen at whatever it was first generated as. Asserting the invariant
    there today would fail for a reason this change cannot fix, and asserting
    it *after* gh-767 lands is the right time.
    """

    def test_every_stub_accessor_matches_its_binding(self, tmp_path):
        import re

        from just_makeit._apply import run as apply_run

        root = tmp_path / "proj"
        _project_with_variable_output(root)
        _make_state_only(root, "widget", "run")
        apply_run(root)

        pyi = flatten_signatures(
            (root / "src" / "proj" / "widget.pyi").read_text()
        )
        ext = "".join(
            p.read_text()
            for p in (root / "native" / "src" / "widget").glob("*.c")
        )
        for m in re.finditer(
            r'\{"(\w+_max_out)",\s*\(PyCFunction\)\w+,\s*(METH_\w+)',
            re.sub(r"\s+", " ", ext),
        ):
            meth, flag = m.group(1), m.group(2)
            takes_arg = f"def {meth}(self, " in pyi
            assert takes_arg == (flag == "METH_VARARGS"), (
                f"{meth}: stub takes_arg={takes_arg} but binding is {flag}"
            )
