"""gh-335: `jm apply` (and the `jm function` CLI) must honor
`variable_output` / `out_size` for module functions.

#318 added self-sizing outputs for module functions, but only the build-time
renderer (`make_functions_ctx`) honored them — `_function.run` dropped the two
fields, so the manifest entry it re-saved during an `apply` replay lost them.
The replayed binding then fell into the plain-`out_type` branch: `out` inserted
*first* and `_dim` sized from `1` / the first array length (the `out_size`
expression ignored), under-allocating the output → the C kernel overran it.

These tests pin three things end-to-end through the scaffold + apply path
(`make_functions_ctx` is already covered by test_function_variable_output_build):

1. the C decl/stub append `out` LAST (matching the binding's call), and
2. the binding sizes `_dim` from the verbatim `out_size` expression, and
3. `apply` preserves all of the above (the regression itself).
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._function import run as function_run  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._config import (  # noqa: E402
    load,
    module_functions as cfg_module_functions,
)


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _scaffold(tmp_path: Path) -> Path:
    """A `wfm` module with two self-sizing functions: a scalar-arg one sized
    by a pure expression, and a two-array one whose size multiplies an input
    length by a scalar (the case the issue showed sizing wrongly as `x_len`)."""
    root = tmp_path / "wfmproj"
    _silent(new_run, "wfmproj", root, modules=["wfm"])
    _silent(
        function_run,
        root,
        "rrc_taps",
        "wfm",
        params=[
            ("beta", "double", False, ""),
            ("sps", "int", False, ""),
            ("span", "int", False, ""),
        ],
        return_type="void",
        out_type="float",
        variable_output=True,
        out_size="2 * sps * span + 1",
    )
    _silent(
        function_run,
        root,
        "dsss_spread",
        "wfm",
        params=[
            ("syms", "float _Complex[]", False, ""),
            ("code", "int8_t[]", False, ""),
            ("sf", "int", False, ""),
        ],
        return_type="void",
        out_type="float _Complex",
        variable_output=True,
        out_size="syms_len * sf",
    )
    return root


def _ext_c(root: Path) -> str:
    return (root / "native" / "src" / "wfm" / "wfm_ext.c").read_text(
        encoding="utf-8"
    )


def _core_h(root: Path) -> str:
    return (root / "native" / "inc" / "wfm" / "wfm_core.h").read_text(
        encoding="utf-8"
    )


def test_cli_persists_variable_output_and_out_size(tmp_path):
    root = _scaffold(tmp_path)
    fns = {f["name"]: f for f in cfg_module_functions(load(root), "wfm")}
    rrc = fns["rrc_taps"]
    assert rrc["variable_output"] is True
    assert rrc["out_size"] == "2 * sps * span + 1"
    assert fns["dsss_spread"]["out_size"] == "syms_len * sf"


def test_decl_stub_binding_agree_out_last(tmp_path):
    root = _scaffold(tmp_path)
    # C decl and stub both append `out` LAST (after the scalars), matching the
    # binding — otherwise the call passes the buffer in the wrong slot.
    assert (
        "void rrc_taps(double beta, int sps, int span, float *out);"
        in _core_h(root)
    )
    stub = (root / "native" / "src" / "wfm" / "rrc_taps.c").read_text(
        encoding="utf-8"
    )
    assert "rrc_taps(double beta, int sps, int span, float *out)" in stub
    ext = _ext_c(root)
    assert "npy_intp _dim = (npy_intp)(2 * sps * span + 1);" in ext
    assert (
        "rrc_taps(beta, sps, span, (float *)PyArray_DATA"
        "((PyArrayObject *)_out));" in ext
    )
    # Two-array case: out_size used verbatim (not collapsed to syms_len), out
    # appended after both arrays AND the scalar.
    assert "npy_intp _dim = (npy_intp)(syms_len * sf);" in ext
    assert (
        "const float _Complex *syms, size_t syms_len, "
        "const int8_t *code, size_t code_len, int sf, float _Complex *out"
        in _core_h(root)
    )


def test_apply_preserves_self_sizing_output(tmp_path):
    """The regression: deleting the glue and replaying via `apply` must
    reproduce the out-last / out_size-sized binding, not the under-allocating
    plain-out_type path."""
    root = _scaffold(tmp_path)
    # Remove the regenerable glue + decl so apply must re-materialize them.
    (root / "native" / "src" / "wfm" / "wfm_ext.c").unlink()
    (root / "native" / "inc" / "wfm" / "wfm_core.h").unlink()
    (root / "native" / "src" / "wfm" / "rrc_taps.c").unlink()

    _silent(apply_run, root)

    ext = _ext_c(root)
    assert "npy_intp _dim = (npy_intp)(2 * sps * span + 1);" in ext
    assert (
        "rrc_taps(beta, sps, span, (float *)PyArray_DATA"
        "((PyArrayObject *)_out));" in ext
    )
    # The buggy path would have emitted these — assert they are gone.
    assert "npy_intp _dim = (npy_intp)1;" not in ext
    assert "rrc_taps((float *)PyArray_DATA" not in ext
    # Decl regenerated with out last.
    assert (
        "void rrc_taps(double beta, int sps, int span, float *out);"
        in _core_h(root)
    )
