"""gh-1072: `void` in a C parameter list means the list is EMPTY.

`void` as a parameter list says "takes nothing", so the standard forbids it
being followed by anything. jm emitted

    size_t peaks(void, row_t *result, size_t max_results);

into its own sacred header from

    jm function peaks --module dsp --return-type row_t \\
        --result-field "i:size_t" --result-field "v:double"

— accepted, exited 0, and rejected by the compiler with *'void' must be the
only parameter and unnamed*. The trigger is **zero declared parameters plus at
least one generated one**: the placeholder was chosen from the DECLARED list,
and the result buffer a `result_fields` function needs was appended after.

The fix is an order, not a check. `_fn_c_params` now returns the parts as a
list and `_types.c_param_list` makes the placeholder decision last, from the
complete list — so there is nothing left to append to once the decision has
been made. `_context/_state`'s two `create()` joins go through the same
primitive; they were already correct, and the point is that the rule can no
longer be re-spelled locally, which is how it went wrong.

`out_type` is NOT a second instance, contrary to the issue's guess: its branch
appends `<T> *out` into a freshly built list, so the list is never empty when
the shape is in play. Measured rather than reasoned — `void taps(double *out)`
— and asserted below so the claim stays honest.

The matrix here is registration-free in the way that matters. It enumerates
jm's prototype emitters from the module (`fn_c_*`) rather than naming them, and
builds the shape cross-product from each emitter's OWN keyword parameters. A
shape kwarg added later with no value in `_SHAPE_VALUES` fails
`test_every_shape_kwarg_has_a_value` by name — the matrix says it has gone
stale instead of quietly covering less, which is the failure mode this repo
keeps meeting from the other side.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import itertools
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _render  # noqa: E402
from just_makeit._function import run as function_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._types import c_param_list  # noqa: E402

_NO_TOOLCHAIN = shutil.which("cmake") is None or (
    shutil.which("cc") is None and shutil.which("gcc") is None
)


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


# ── the property ──────────────────────────────────────────────────────────


def param_lists(c_text: str) -> list[list[str]]:
    """Every function parameter list in *c_text*, split into its parts.

    Reads what was emitted rather than predicting it, so an emitter that
    grows a new shape is covered by the same assertion with no edit. Nesting
    is tracked because a function-pointer parameter carries its own
    parentheses and its commas do not separate parameters of the outer list.
    """
    out: list[list[str]] = []
    for m in re.finditer(r"\b\w+\s*\(", c_text):
        i = m.end()
        depth, start = 1, i
        while i < len(c_text) and depth:
            if c_text[i] == "(":
                depth += 1
            elif c_text[i] == ")":
                depth -= 1
            i += 1
        if depth:
            continue
        inner = c_text[start : i - 1]
        parts, depth, cur = [], 0, ""
        for ch in inner:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(cur.strip())
                cur = ""
            else:
                cur += ch
        if cur.strip():
            parts.append(cur.strip())
        out.append(parts)
    return out


def offending(c_text: str) -> list[list[str]]:
    """Parameter lists where a bare ``void`` is not the whole list.

    `void *p` is a perfectly good parameter, so the test is equality with
    ``void``, not containment — a substring test would call
    ``f(void *ctx, int n)`` a defect.
    """
    return [
        parts
        for parts in param_lists(c_text)
        if any(p == "void" for p in parts) and len(parts) > 1
    ]


class TestThePropertyItself:
    """The detector must be able to see the bug it is written for.

    A checker that reports clean on the original defect is worse than none —
    this repo's recurring finding, and the reason it is asserted first.
    """

    def test_it_catches_the_shipped_defect(self):
        bad = "size_t peaks(void, row_t *result, size_t max_results);\n"
        assert offending(bad) == [
            ["void", "row_t *result", "size_t max_results"]
        ]

    def test_a_genuinely_empty_list_is_fine(self):
        assert offending("void tick(void);\n") == []

    def test_a_void_pointer_parameter_is_not_a_finding(self):
        assert offending("int run(void *ctx, int n);\n") == []

    def test_a_function_pointer_parameter_does_not_split_the_list(self):
        """Its commas belong to the inner list, not the outer one."""
        text = "int sort(void *base, int (*cmp)(const void *, const void *));"
        assert offending(text) == []


class TestTheOnePlaceThatDecides:
    def test_empty_is_void(self):
        assert c_param_list([]) == "void"

    def test_non_empty_is_joined(self):
        assert c_param_list(["double x", "size_t n"]) == "double x, size_t n"

    def test_it_takes_a_list_so_nothing_can_be_appended_after(self):
        """The fix is an ORDER, and this is the shape that enforces it.

        A helper returning the joined text invites exactly the append that
        produced the bug; taking a list means the caller has to assemble
        everything first, because there is no other moment to do it in.
        """
        sig = inspect.signature(c_param_list)
        (only,) = sig.parameters.values()
        assert only.annotation in ("list[str]", list)


# ── the shape matrix ──────────────────────────────────────────────────────

#: One value per shape-bearing keyword of jm's prototype emitters, keyed by
#: the PARAMETER NAME so the cross-product is built from each emitter's own
#: signature rather than from a hand-kept list of shapes.
_SHAPE_VALUES: dict[str, tuple] = {
    "out_type": ("", "double"),
    "result_fields": (None, [{"name": "i", "type": "size_t"}]),
    "max_results_param": ("",),
    "variable_output": (False, True),
}

#: Keyword parameters every emitter has that say nothing about the shape.
_NON_SHAPE = {"fn_name", "params", "return_type"}


def _emitters() -> dict:
    """jm's C prototype emitters, enumerated from the module.

    By name shape (`fn_c_*`) rather than by a list, so an emitter added later
    is under this gate with no edit here — the bug being guarded is one where
    two of the three emitters shared a defect and the third did not.
    """
    return {
        name: fn
        for name, fn in vars(_render).items()
        if name.startswith("fn_c_") and callable(fn)
    }


def _shape_kwargs(fn) -> list[str]:
    return [
        n
        for n, p in inspect.signature(fn).parameters.items()
        if n not in _NON_SHAPE and p.default is not inspect.Parameter.empty
    ]


def _combos(fn):
    names = _shape_kwargs(fn)
    for values in itertools.product(*(_SHAPE_VALUES[n] for n in names)):
        yield dict(zip(names, values))


class TestEveryEmitterEveryShape:
    def test_the_gate_is_armed(self):
        """A matrix that exercises nothing looks exactly like one that
        passes, so assert it found the emitters it exists for."""
        found = _emitters()
        assert "fn_c_decl" in found
        assert "fn_c_stub" in found
        assert "fn_c_inline_stub" in found

    def test_every_shape_kwarg_has_a_value(self):
        """The matrix says when it has gone stale.

        A shape added to an emitter with no entry here would otherwise be
        silently untested — the same "covers less, looks identical" shape as
        gh-1029 and gh-1033.
        """
        missing = {
            name: [k for k in _shape_kwargs(fn) if k not in _SHAPE_VALUES]
            for name, fn in _emitters().items()
        }
        missing = {k: v for k, v in missing.items() if v}
        assert not missing, (
            "these emitter keyword(s) select a signature shape and have no "
            f"value in _SHAPE_VALUES, so the matrix does not cover them: "
            f"{missing}"
        )

    @pytest.mark.parametrize("emitter", sorted(_emitters()))
    def test_no_shape_emits_a_void_beside_a_parameter(self, emitter):
        """Zero declared params, every shape, both emitters.

        `all([])` is `True` and `", ".join([]) or "void"` is `"void"`: the
        empty declared list is where a placeholder decision made too early
        shows up, and gh-1060 found the same corner from the call side.
        """
        fn = _emitters()[emitter]
        bad = []
        for kw in _combos(fn):
            text = fn("peaks", [], "row_t", **kw)
            if offending(text):
                bad.append((kw, text.strip()))
        assert not bad, bad

    @pytest.mark.parametrize("emitter", sorted(_emitters()))
    def test_declared_params_survive_every_shape(self, emitter):
        """The guard against over-fixing.

        Dropping the declared parameters would satisfy the assertion above by
        emitting `void` everywhere — a prototype that takes nothing and a
        stub that ignores its inputs.
        """
        fn = _emitters()[emitter]
        for kw in _combos(fn):
            text = fn("peaks", [("x", "double")], "row_t", **kw)
            assert "double x" in text, (kw, text)
            assert offending(text) == [], (kw, text)


class TestTheDeclAndTheStubAgree:
    """They are compiled against each other, so they must match exactly.

    Both grew the same append and both had to be fixed; a fix applied to one
    would leave a stub whose signature contradicts its own prototype, which
    is a worse failure than the one being fixed.
    """

    @pytest.mark.parametrize(
        "params", [[], [("x", "double")]], ids=["no-params", "with-params"]
    )
    def test_the_result_fields_signature_is_identical(self, params):
        fields = [{"name": "i", "type": "size_t"}]
        decl = _render.fn_c_decl(
            "peaks", params, "row_t", result_fields=fields
        )
        stub = _render.fn_c_stub(
            "peaks", params, "row_t", result_fields=fields
        )
        assert param_lists(decl)[0] == param_lists(stub)[0]


class TestThroughTheCliInterface:
    """The header a user actually gets, from the command they actually run."""

    def _project(self, tmp_path: Path) -> Path:
        root = tmp_path / "demo"
        _quiet(new_run, "demo", root)
        _quiet(module_run, root, "dsp")
        return root

    def _header(self, root: Path) -> str:
        return (root / "native" / "inc" / "dsp" / "dsp_core.h").read_text(
            encoding="utf-8"
        )

    def test_a_zero_param_result_fields_function(self, tmp_path):
        root = self._project(tmp_path)
        _quiet(
            function_run,
            root,
            "peaks",
            "dsp",
            params=[],
            return_type="row_t",
            result_fields=[
                {"name": "i", "type": "size_t"},
                {"name": "v", "type": "double"},
            ],
        )
        header = self._header(root)
        assert offending(header) == [], header
        assert "size_t peaks(row_t *result, size_t max_results);" in header

    def test_the_stub_on_disk_matches_the_header(self, tmp_path):
        root = self._project(tmp_path)
        _quiet(
            function_run,
            root,
            "peaks",
            "dsp",
            params=[],
            return_type="row_t",
            result_fields=[{"name": "i", "type": "size_t"}],
        )
        stub = (root / "native" / "src" / "dsp" / "peaks.c").read_text(
            encoding="utf-8"
        )
        assert offending(stub) == [], stub
        assert "peaks(row_t *result, size_t max_results)" in stub

    def test_an_out_type_function_was_never_affected(self, tmp_path):
        """The issue's guessed sibling, measured rather than assumed.

        `out_type` appends `<T> *out` into a freshly built list, so the list
        is never empty while the shape is in play. Asserted so the claim in
        this module's docstring cannot quietly stop being true.
        """
        root = self._project(tmp_path)
        _quiet(function_run, root, "taps", "dsp", params=[], out_type="double")
        header = self._header(root)
        assert offending(header) == [], header
        assert "void taps(double *out);" in header

    def test_a_plain_zero_param_function_still_says_void(self, tmp_path):
        """`void` is right here, and must survive the fix."""
        root = self._project(tmp_path)
        _quiet(function_run, root, "tick", "dsp", params=[], return_type="int")
        assert "int tick(void);" in self._header(root)


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
class TestItActuallyCompiles:
    """The oracle that has no opinions.

    The emitted string reads fine — this is a parameter list, spelled with
    real types, in the right order. Only a compiler objects, which is why the
    property tests above are paired with one that runs one.
    """

    def test_the_header_is_valid_c(self, tmp_path):
        root = tmp_path / "demo"
        _quiet(new_run, "demo", root)
        _quiet(module_run, root, "dsp")
        _quiet(
            function_run,
            root,
            "peaks",
            "dsp",
            params=[],
            return_type="row_t",
            result_fields=[
                {"name": "i", "type": "size_t"},
                {"name": "v", "type": "double"},
            ],
        )
        header = root / "native" / "inc" / "dsp" / "dsp_core.h"
        # The row struct is the author's, in the sacred header, and jm never
        # sees it — so the compile needs it declared. A translation unit that
        # includes the header and nothing else is the narrowest thing that
        # can fail, and it fails for exactly one reason.
        tu = tmp_path / "tu.c"
        tu.write_text(
            "#include <stddef.h>\n"
            "typedef struct { size_t i; double v; } row_t;\n"
            f'#include "{header.name}"\n',
            encoding="utf-8",
        )
        cc = shutil.which("cc") or shutil.which("gcc")
        inc = root / "native" / "inc"
        proc = subprocess.run(
            [
                cc,
                "-std=c99",
                "-fsyntax-only",
                f"-I{header.parent}",
                # `clib_common.h` sits one level up, beside the package
                # header; the generated CMake puts both on the include path.
                f"-I{inc}",
                str(tu),
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
