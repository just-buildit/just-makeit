"""gh-1099: an init-param `default` must be a literal of its declared type.

A manifest could carry any string as a `default`, and nothing checked it
against the `type` written beside it. The value is emitted **verbatim** into
four places, each of which fails differently and none of which names the
manifest:

* the C local — `int mode = hann;`, which does not compile;
* `_context/_types._py_default` and `_stubs._py_default_stub`, which seed a
  `.pyi` signature. The integer branch emitted a bare undefined name; the
  FLOAT branch appended `.0` to it, giving `x: float = hann.0` — a
  **SyntaxError** breaking the whole stub, which is gh-515's failure mode in
  the one branch gh-515 did not cover;
* `_app`, which seeds an `argparse` default and so raises `NameError` when the
  generated app starts.

The earliest of those is a compiler error one command later. Refusing at the
manifest says it where it was written.

**A constant is a real use case and is NOT refused.** `INT_MAX` is a macro and
`hann` is a typo; only the compiler can tell them apart, and jm can render
neither as the Python literal the stub, the docstring and the app flag all
need. `default_raw` already meant "this is C, not a literal" and already did
the right thing — text straight into the C, `...` on the Python side — but it
was consulted only for ctypes carrying a `parse_type`, so it worked on a
`size_t` and was silently dropped on an `int`. Both branches read it now,
which is what lets the refusal point an author at it for any scalar.

`TestTheFallbacksWereNotWidened` is the regression fence in the other
direction, and it is why this is one gate rather than three: gh-515 left the
float branch synthesising `.0` for an absent default *deliberately*, because
`...` suppresses the generated construction example. Defending inside
`_py_default` would have churned those working examples away, and is
unnecessary once a non-literal cannot reach it.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._context._types import _py_default  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._stubs import _py_default_stub  # noqa: E402
from just_makeit._types import default_type_error  # noqa: E402


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _project(tmp_path: Path, name: str, frag: str) -> Path:
    root = tmp_path / name
    _quiet(new_run, name, root)
    _quiet(
        object_run,
        root,
        "obj",
        module=None,
        arg_type="float",
        return_type="float",
        state_vars=[("gain", "float", "0.0f")],
    )
    man = root / "objects" / "obj.toml"
    if not man.exists():
        man = root / "just-makeit.toml"
    man.write_text(man.read_text(encoding="utf-8") + frag, encoding="utf-8")
    return root


def _apply(root: Path):
    return __import__("just_makeit._apply", fromlist=["run"]).run(root)


class TestALiteralIsAccepted:
    """The set that must keep working, including every C suffix form."""

    @pytest.mark.parametrize(
        "ctype,default",
        [
            ("int", "16"),
            ("int", "-3"),
            ("size_t", "0U"),
            ("uint64_t", "5ULL"),
            ("double", "1.5"),
            ("float", "1.5f"),
            ("bool", "true"),
            ("bool", "false"),
            ("const char *", "/dev/null"),
            ("const char *", "NULL"),
            # An absent default is what every REQUIRED param carries.
            ("int", ""),
            ("double", ""),
        ],
    )
    def test_no_error(self, ctype, default):
        assert default_type_error(ctype, default) == ""


class TestAWrongTypeIsRefused:
    def test_an_identifier_on_an_int(self):
        msg = default_type_error("int", "hann")
        assert "not a valid `int` literal" in msg

    def test_an_identifier_on_a_double(self):
        """The float branch is the severe one: it appended `.0`, making
        `hann.0` — a SyntaxError rather than merely an undefined name."""
        assert "not a valid `double` literal" in default_type_error(
            "double", "hann"
        )

    def test_it_points_at_default_raw(self):
        """A refusal without the remedy sends the author to the docs, and the
        remedy here is a key that already exists."""
        msg = default_type_error("int", "DP_MAX_TAPS")
        assert 'default_raw = "DP_MAX_TAPS"' in msg

    def test_bool_gets_its_own_message(self):
        """`bool`'s kind is "int", so it must dispatch on the concrete ctype —
        the same reason gh-610 gave for the branch in `_py_default`. Telling a
        bool author to "spell the value" would not help."""
        msg = default_type_error("bool", "yes")
        assert "not a bool" in msg
        assert "`true` or `false`" in msg

    def test_apply_refuses_before_the_param_reaches_a_file(self, tmp_path):
        root = _project(
            tmp_path,
            "r",
            '\n[[obj.init_params]]\nname = "mode"\ntype = "int"\n'
            'default = "hann"\n',
        )
        # `_apply` catches the ValueError and exits 1 — a refusal is a
        # message, not a traceback.
        with pytest.raises(SystemExit) as e:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                _quiet(_apply, root)
        assert e.value.code == 1
        assert "mode" in err.getvalue()
        assert "default_raw" in err.getvalue()
        # The stub exists from scaffolding; what matters is that the
        # refused param never reached it.
        pyi = (root / "src" / "r" / "obj.pyi").read_text(encoding="utf-8")
        assert "mode" not in pyi


class TestDefaultRawIsTheConstantSpelling:
    """The escape the refusal names, on every scalar rather than some."""

    FRAG = (
        '\n[[obj.init_params]]\nname = "taps"\ntype = "int"\n'
        'default_raw = "DP_MAX_TAPS"\n'
        '\n[[obj.init_params]]\nname = "cap"\ntype = "size_t"\n'
        'default_raw = "SIZE_MAX"\n'
        '\n[[obj.init_params]]\nname = "gain"\ntype = "double"\n'
        'default_raw = "DP_UNITY"\n'
    )

    def test_the_c_carries_the_constant_verbatim(self, tmp_path):
        """`int` and `double` are the regression fence: `default_raw` was read
        only on the `parse_type` branch, so it worked for `size_t` and was
        silently dropped for these two, which fell back to the type's zero."""
        root = _project(tmp_path, "c", self.FRAG)
        _quiet(_apply, root)
        ext = (root / "native" / "src" / "obj" / "obj_ext.c").read_text(
            encoding="utf-8"
        )
        assert "int taps = DP_MAX_TAPS;" in ext
        assert "cap_raw = SIZE_MAX;" in ext
        assert "double gain = DP_UNITY;" in ext

    def test_the_stub_says_it_has_no_literal(self, tmp_path):
        """`...` is jm's established sentinel for "no literal jm can seed".
        Rendering the constant here would put an undefined name in the stub,
        which is the defect being fixed, one face over."""
        root = _project(tmp_path, "s", self.FRAG)
        _quiet(_apply, root)
        pyi = (root / "src" / "s" / "obj.pyi").read_text(encoding="utf-8")
        assert "taps: int = ..." in pyi
        assert "cap: int = ..." in pyi
        assert "gain: float = ..." in pyi

    def test_the_stub_is_valid_python(self, tmp_path):
        import ast

        root = _project(tmp_path, "v", self.FRAG)
        _quiet(_apply, root)
        ast.parse((root / "src" / "v" / "obj.pyi").read_text(encoding="utf-8"))


class TestTheFallbacksWereNotWidened:
    """Why this is ONE gate and not three.

    gh-515 left the float branch synthesising `.0` for an absent default
    deliberately — `...` suppresses the generated construction example, so
    churning these into the sentinel would delete working doctests. An earlier
    cut of this fix did exactly that and broke three gh-515/gh-610 tests.
    Defending inside `_py_default` is also unnecessary: with the manifest
    refusing a non-literal, one cannot reach it.
    """

    @pytest.mark.parametrize("ctype", ["double", "float"])
    def test_an_absent_float_default_still_synthesises_zero(self, ctype):
        """`_py_default` only.

        Its `_stubs` peer answers `...` here and always has — its very first
        line returns the sentinel for any empty default, before the kind
        dispatch. The two are not reachable with an empty default from the
        same init-param site (the `_stubs` one is guarded by `dflt.strip()`),
        so this is not the gh-1051 class of peer disagreement and is left
        alone rather than "fixed" into a churn gh-515 argued against.
        """
        assert _py_default(ctype, "") == ".0"

    def test_a_real_literal_is_still_passed_through(self):
        assert _py_default("int", "0U") == "0"
        assert _py_default_stub("double", "1.5") == "1.5"
