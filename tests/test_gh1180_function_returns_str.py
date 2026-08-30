"""gh-1180: a module function can hand back a string.

A module function could **take** a string (`const char *` is supported and
works) and had no way to give one back. doppler's `cvt` has the shape exactly:
`hex_to_bin` parses text and works, `bin_to_hex` is its inverse and could only
return a `uint8` buffer the caller decodes itself — so a pair of inverse
conversions read asymmetrically, and only the Python face paid.

The spelling is the issue's **option 2**, not its option 1. `char[]` as an
out-param would make the caller allocate a buffer it cannot read as text;
`out_type = "str"` on a `variable_output` function means the caller allocates
nothing, which the issue calls "nicer still". It reuses the allocate-call-trim
shape the ndarray self-sizing output already has — jm sizes from `out_size`,
the C function writes into `char *out` and returns the used length, and the
wrapper builds the `str`.

`char` is deliberately NOT added to `_CTYPE_META`. That would make it a legal
scalar type everywhere and silently retire the hint steering `char` to `int8_t`
for its platform-dependent signedness. This is one output shape, not a new type.

The bug found underneath
------------------------
Both C emitters hardcoded `void` for an `out_type` function, while the binding
for a `variable_output` one reads an integer return as the written length
(`size_t _n = (size_t)fn(...)`). A project declaring `return_type = "size_t"`
alongside `out_type` got a header saying `void` and a binding assigning from
it — **generated C that does not compile**.

Reproduced on `main` before this change with a plain `out_type = "double"`, so
the `str` shape did not introduce it; it only made it unavoidable, because a
string output has no length without a return. `TestTheDeclAndTheStubAgree`
compiles the two emitters' output together, which is the only kind of test that
could have caught it — the render assertions beside it were all perfectly happy.

The diagnostic
--------------
The issue's option 3, kept even though option 2 landed: `char[]` stays the
natural thing to type, and the message listed the supported set without saying
which member stood in. The array form of every *other* hinted scalar was
unhinted too (`long` had a suggestion, `long[]` did not), so that is derived
rather than enumerated.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _render as R  # noqa: E402
from just_makeit import _stubs as S  # noqa: E402
from just_makeit._types import unsupported_return_type_help  # noqa: E402

_CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")

#: doppler's case: bits in, hex text out, length reported by the return.
FN = dict(
    fn_name="bin_to_hex",
    params=[{"name": "bits", "type": "uint8_t[]"}],
    return_type="size_t",
    out_type="str",
    variable_output=True,
    out_size="bits_len * 2",
)


def _decl() -> str:
    return R.fn_c_decl(
        FN["fn_name"],
        [(p["name"], p["type"]) for p in FN["params"]],
        FN["return_type"],
        out_type=FN["out_type"],
        variable_output=True,
    )


def _stub() -> str:
    return R.fn_c_stub(
        FN["fn_name"],
        [(p["name"], p["type"]) for p in FN["params"]],
        FN["return_type"],
        out_type=FN["out_type"],
        variable_output=True,
    )


def _wrapper(**over) -> str:
    kw = dict(
        params=FN["params"],
        return_type=FN["return_type"],
        out_type=FN["out_type"],
        variable_output=True,
        out_size=FN["out_size"],
    )
    kw.update(over)
    return R._py_wrapper_for_function(FN["fn_name"], **kw)


class TestTheCSurfaceIsChar:
    """`str` names the PYTHON shape. The C the author implements takes a
    `char *` — if it did not, `out_type = "str"` would be asking them to write
    a function against a type that does not exist."""

    def test_the_declaration(self) -> None:
        assert (
            "size_t bin_to_hex(const uint8_t *bits, size_t bits_len, "
            "char *out);" in _decl()
        )

    def test_the_body_stub(self) -> None:
        body = _stub()
        assert "char *out" in body, body
        assert body.lstrip().startswith("/* <<IMPLEMENT: bin_to_hex>> */")
        assert "size_t\nbin_to_hex(" in body, body

    def test_the_stub_returns_something(self) -> None:
        """A `size_t` function whose stub falls off the end is a warning at
        best and undefined behaviour at worst, and it is the first thing the
        author compiles."""
        assert "return (size_t)0; /* placeholder */" in _stub()


class TestThePythonSurfaceIsStr:
    def test_the_wrapper_builds_a_str(self) -> None:
        w = _wrapper()
        assert "PyUnicode_FromStringAndSize(_buf, (Py_ssize_t)_n)" in w, w

    def test_it_sizes_from_out_size_and_frees(self) -> None:
        w = _wrapper()
        assert "size_t _cap = (size_t)(bits_len * 2);" in w, w
        assert "char *_buf = (char *)malloc(_cap + 1);" in w, w
        assert "free(_buf);" in w, w

    def test_the_return_is_the_length(self) -> None:
        w = _wrapper()
        assert "size_t _n = (size_t)bin_to_hex(bits, bits_len, _buf);" in w, w

    def test_a_callee_overrun_is_clamped(self) -> None:
        """The length comes from the callee, and a callee that reports more
        than it was given would otherwise read past the buffer on the way
        out."""
        assert "if (_n > _cap) _n = _cap;" in _wrapper()

    def test_the_stub_says_str(self) -> None:
        ann = S._fn_stub(
            {
                "name": "bin_to_hex",
                **{k: FN[k] for k in ("params", "return_type", "out_type")},
                "variable_output": True,
            }
        )
        assert "-> str" in ann, ann


class TestItRefusesWithoutALength:
    def test_a_void_function_is_refused(self) -> None:
        """A `void` function cannot say how much it wrote, and hunting for a
        NUL the callee may never have written is a read past the end waiting
        to happen. Refuse rather than guess."""
        with pytest.raises(ValueError) as exc:
            _wrapper(return_type="void")
        msg = str(exc.value)
        assert "needs its LENGTH back" in msg, msg
        assert "size_t" in msg, msg

    def test_a_float_return_is_refused(self) -> None:
        with pytest.raises(ValueError):
            _wrapper(return_type="double")


@pytest.mark.skipif(_CC is None, reason="no C compiler available")
class TestTheDeclAndTheStubAgree:
    """The regression the render assertions could not see.

    Both emitters hardcoded `void` while the binding assigned from the return,
    so the header and the body disagreed and the project did not compile. Only
    compiling the two together catches that, which is why this exists rather
    than a fourth string comparison.
    """

    @staticmethod
    def _compiles(tmp_path: Path, decl: str, stub: str) -> int:
        src = tmp_path / "probe.c"
        src.write_text(
            "#include <stdint.h>\n#include <stddef.h>\n" + decl + "\n" + stub,
            encoding="utf-8",
        )
        return subprocess.run(
            [
                _CC,
                "-c",
                "-std=c11",
                "-Werror",
                str(src),
                "-o",
                str(tmp_path / "probe.o"),
            ],
            capture_output=True,
            text=True,
            timeout=600,
        ).returncode

    def test_the_str_shape(self, tmp_path: Path) -> None:
        assert self._compiles(tmp_path, _decl(), _stub()) == 0

    def test_the_ndarray_shape_that_was_already_broken(
        self, tmp_path: Path
    ) -> None:
        """Reproduced on main with no `str` anywhere: a `variable_output`
        `out_type` function declaring an integer return."""
        params = [("x", "double[]")]
        decl = R.fn_c_decl(
            "trim", params, "size_t", out_type="double", variable_output=True
        )
        stub = R.fn_c_stub(
            "trim", params, "size_t", out_type="double", variable_output=True
        )
        assert "size_t trim(" in decl, decl
        assert self._compiles(tmp_path, decl, stub) == 0

    def test_a_void_out_fn_still_declares_void(self, tmp_path: Path) -> None:
        """The default and the overwhelming majority — unchanged."""
        params = [("x", "double[]")]
        decl = R.fn_c_decl(
            "scale", params, "void", out_type="double", variable_output=True
        )
        assert decl.startswith("void scale("), decl
        stub = R.fn_c_stub(
            "scale", params, "void", out_type="double", variable_output=True
        )
        assert self._compiles(tmp_path, decl, stub) == 0

    def test_the_arming_check(self, tmp_path: Path) -> None:
        """A compile that passes proves nothing unless the broken form fails.
        This is what `main` emitted: a `void` declaration and a body that
        returns a value."""
        bad_decl = "void trim(const double *x, size_t x_len, double *out);\n"
        bad_stub = (
            "size_t\ntrim(const double *x, size_t x_len, double *out)\n"
            "{ (void)x; (void)x_len; (void)out; return (size_t)0; }\n"
        )
        assert self._compiles(tmp_path, bad_decl, bad_stub) != 0


class TestTheDiagnostic:
    """Option 3, kept because `char[]` stays the natural thing to type."""

    def test_char_array_names_the_substitute(self) -> None:
        msg = unsupported_return_type_help("char[]", allow_void=False)
        assert "Did you mean 'uint8_t[]'?" in msg, msg

    def test_and_points_at_the_new_spelling_for_text(self) -> None:
        msg = unsupported_return_type_help("char[]", allow_void=False)
        assert 'out_type = "str"' in msg, msg

    def test_scalar_char_is_unchanged(self) -> None:
        """A different question with a different answer: `char` as a scalar is
        a platform-signedness problem, not a buffer."""
        msg = unsupported_return_type_help("char", allow_void=False)
        assert "Did you mean 'int8_t'?" in msg, msg
        assert "byte buffer" not in msg, msg

    @pytest.mark.parametrize(
        "spelling,want",
        [
            ("long[]", "int64_t[]"),
            ("unsigned[]", "uint32_t[]"),
            ("short[]", "int16_t[]"),
        ],
    )
    def test_the_array_form_of_every_hinted_scalar(
        self, spelling: str, want: str
    ) -> None:
        """Derived, not enumerated: `long` had a suggestion and `long[]` had
        none, for no reason anyone chose."""
        msg = unsupported_return_type_help(spelling, allow_void=False)
        assert f"Did you mean '{want}'?" in msg, msg

    def test_an_unknown_type_still_gets_no_guess(self) -> None:
        msg = unsupported_return_type_help("wat_t[]", allow_void=False)
        assert "Did you mean" not in msg, msg
