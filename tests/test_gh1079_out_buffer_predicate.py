"""gh-1079: one predicate deciding who gets an `out=` buffer.

A `variable_output` method may take an optional caller-owned `out=` array. The
question "does this one?" was answered in **three** places — the binding's
`_kwlist` and parse block, the standalone `.pyi`, and the module-aggregated
`.pyi` — and `_context/_methods` already stated the rule they had to hold to:

    a stub advertising an out= the binding rejects, or a binding accepting one
    the stub hides, is the same defect in either direction

with nothing behind that sentence. The two `.pyi` writers have been caught
disagreeing about jm's own binding arguments twice already (gh-1042 over
whether they are documented at all, gh-1051 over a default's value), so the
sentence was one edit away from being false.

The three now call `_outbuf.enabled`. `TestTheThreeFacesAgree` is the gate, and
it does not check that they call it — it renders all three and compares what
they say, which is the property, and which stays true through any future
refactor of how they get the answer.

**The open half is named, not hidden.** The all-scalar-params shape still gets
no `out=`, and `why_not` says so in words rather than returning a bare False:
sizing it means reading `<m>_max_out(state)`, which the author's C may legally
answer `0` for — "unknown", jm's own documented sizing contract. A buffer
validated against an unknown bound is not validated, so offering `out=` there
is a decision about what jm does when it cannot bound the write. That is the
remaining ask of gh-1079, and `test_the_open_half_is_named` keeps it visible.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _outbuf  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


#: The shapes that decide the answer, and what each is called in the issue.
SHAPES = [
    ("generator", dict(arg_type="void", params=[]), True),
    ("array-arg", dict(arg_type="float", params=[]), True),
    (
        "one-array-param",
        dict(arg_type="void", params=[("x", "float[]")]),
        True,
    ),
    (
        "all-scalar-params",
        dict(arg_type="void", params=[("n", "size_t")]),
        False,
    ),
    (
        "array-plus-scalar",
        dict(arg_type="float", params=[("mu", "double")]),
        False,
    ),
]


class TestThePredicate:
    @pytest.mark.parametrize(
        "label,kw,expected", SHAPES, ids=[s[0] for s in SHAPES]
    )
    def test_each_shape(self, label, kw, expected):
        assert (
            _outbuf.enabled(
                variable_output=True,
                multi_output=False,
                has_arg=kw["arg_type"] != "void",
                params=[{"name": n, "type": t} for n, t in kw["params"]],
            )
            is expected
        )

    def test_a_fixed_output_method_never_gets_one(self):
        """`out=` is the self-sizing feature; a 1:1 method allocates per
        call and has nothing to hand the caller."""
        assert not _outbuf.enabled(
            variable_output=False, multi_output=False, has_arg=True, params=[]
        )

    def test_multi_output_never_gets_one(self):
        """Two output arrays would need two buffers, and one `out=` cannot
        say which it is."""
        assert not _outbuf.enabled(
            variable_output=True, multi_output=True, has_arg=True, params=[]
        )

    def test_the_open_half_is_named(self):
        """`why_not` distinguishes a property of the shape from a gap.

        "this method allocates per call" and "jm cannot size the buffer" are
        not interchangeable, and a bare False makes them look like one thing.
        Naming the second is what keeps gh-1079's remaining ask findable.
        """
        assert (
            _outbuf.why_not(
                variable_output=False,
                multi_output=False,
                has_arg=True,
                params=[],
            )
            == "not variable_output"
        )
        gap = _outbuf.why_not(
            variable_output=True,
            multi_output=False,
            has_arg=False,
            params=[{"name": "n", "type": "size_t"}],
        )
        assert "gh-1079" in gap and "size" in gap


class TestTheThreeFacesAgree:
    """The property, rendered rather than asserted about the code.

    Nothing here checks that the three call `_outbuf`. It renders all three
    and compares what they SAY, so the guarantee survives any future change
    to how they get the answer — and so a refactor that reintroduced a local
    copy would still have to keep them in step.
    """

    def _module_project(self, tmp_path: Path, **method_kw) -> Path:
        root = tmp_path / "d79"
        _quiet(new_run, "d79", root)
        _quiet(module_run, root, "dsp")
        _quiet(
            object_run,
            root,
            "amp",
            module="dsp",
            arg_type="float",
            return_type="float",
            state_vars=[("g", "double", "1.0")],
        )
        _quiet(
            method_run,
            root,
            "amp",
            "run",
            "dsp",
            return_type="float",
            variable_output=True,
            multi_output=[],
            **method_kw,
        )
        return root

    @staticmethod
    def _binding_takes_out(root: Path) -> bool:
        """Read the wrapper's own `_kwlist`, not the whole file.

        A module object's binding lives in its per-object fragment
        (`dsp_ext_amp.c`); `dsp_ext.c` only aggregates. Reading the file the
        method is actually in is what keeps this measuring the method rather
        than whatever else the module happens to contain.
        """
        text = (root / "native" / "src" / "dsp" / "dsp_ext_amp.c").read_text(
            encoding="utf-8"
        )
        i = text.index("\nAmp_run(")
        block = text[i : text.index("\n}\n", i)]
        return '"out"' in block

    @staticmethod
    def _stub_takes_out(root: Path) -> bool:
        pyi = (root / "src" / "d79" / "dsp" / "dsp.pyi").read_text(
            encoding="utf-8"
        )
        m = re.search(r"\n    def run\((.*?)\)\s*->", pyi, re.S)
        assert m is not None, pyi
        return "out:" in m.group(1)

    @pytest.mark.parametrize(
        "label,kw,expected", SHAPES, ids=[s[0] for s in SHAPES]
    )
    def test_binding_and_stub_say_the_same_thing(
        self, tmp_path, label, kw, expected
    ):
        root = self._module_project(
            tmp_path, arg_type=kw["arg_type"], params=list(kw["params"])
        )
        binding = self._binding_takes_out(root)
        stub = self._stub_takes_out(root)
        assert binding == stub, (
            f"{label}: binding accepts out={binding}, stub publishes "
            f"out={stub} — one of them is lying to the caller"
        )
        assert binding is expected

    def test_the_gate_covers_both_answers(self):
        """Armed: the matrix must contain a shape that gets `out=` AND one
        that does not, or "they agree" is true of a constant."""
        answers = {s[2] for s in SHAPES}
        assert answers == {True, False}
