"""
gh-611 — an array init_param with a declared default (``default = "[]"``)
was always hoisted into the constructor's mandatory positional group
regardless of where it was declared, while the generated ``.pyi`` rendered
it in manifest position with a fake ``= ...`` placeholder — so the stub
blessed a positional call the binding rejected.

Repro (doppler's ``objects/carrier_acq.toml``): ``psd_template`` (``float[]``,
``default = "[]"``) declared 7th among a run of defaulted scalars generated a
kwlist with ``psd_template`` FIRST. A plain array with a declared default is
now genuinely optional — parsed as a keyword defaulting to an empty array
when omitted — so it takes its declared position among the other optional
params instead of being hoisted, matching what the `.pyi` already did.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from conftest import flatten_signatures  # noqa: E402

from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


def _ext_c(root, obj):
    return (root / "native" / "src" / obj / f"{obj}_ext.c").read_text(
        encoding="utf-8"
    )


def _core_h(root, obj):
    return (root / "native" / "inc" / obj / f"{obj}_core.h").read_text(
        encoding="utf-8"
    )


def _pyi(root, pkg, obj):
    return flatten_signatures(
        (root / "src" / pkg / f"{obj}.pyi").read_text(encoding="utf-8")
    )


_PSD_PARAMS = [
    ("sample_rate_hz", "float", "0.0"),
    ("symbol_rate_hz", "float", "0.0"),
    ("resolution_hz", "float", "0.0"),
    ("psd_template", "float[]", "[]"),
    ("pfa", "float", "1e-3"),
]


class TestDefaultedArrayPreservesDeclOrder:
    def test_kwlist_keeps_declared_position(self, tmp_path):
        root = tmp_path / "wfm"
        new_run("wfm", root)
        object_run(
            root,
            "carrier_acq",
            None,
            no_state=True,
            init_params=_PSD_PARAMS,
        )
        ext = _ext_c(root, "carrier_acq")
        assert (
            'kwlist[] = {"sample_rate_hz", "symbol_rate_hz",'
            ' "resolution_hz", "psd_template", "pfa", NULL}' in ext
        )

    def test_pyi_matches_kwlist_position(self, tmp_path):
        # gh-611 review: the previous version of this assertion built `order`
        # by filtering a hardcoded literal tuple for membership, so it came
        # out in the literal's order regardless of what the generated
        # signature actually said — it could not fail. Assert the exact
        # expected signature text instead (mirrors
        # test_gh422_string_enum_order.py's test_pyi_init_matches_declared_order),
        # so a reintroduced hoist-to-front bug makes this go red for real.
        root = tmp_path / "wfm"
        new_run("wfm", root)
        object_run(
            root,
            "carrier_acq",
            None,
            no_state=True,
            init_params=_PSD_PARAMS,
        )
        pyi = _pyi(root, "wfm", "carrier_acq")
        assert (
            "def __init__(self, sample_rate_hz: float = 0.0, "
            "symbol_rate_hz: float = 0.0, resolution_hz: float = 0.0, "
            "psd_template: npt.ArrayLike = ..., pfa: float = 1e-3)"
            " -> None: ..." in pyi
        )

    def test_omitted_array_compiles_to_null_len_zero(self, tmp_path):
        # The generated conversion must not dereference a NULL PyObject* —
        # the ternary-guarded call arg is the load-bearing part of the fix.
        root = tmp_path / "wfm"
        new_run("wfm", root)
        object_run(
            root,
            "carrier_acq",
            None,
            no_state=True,
            init_params=_PSD_PARAMS,
        )
        ext = _ext_c(root, "carrier_acq")
        assert "PyObject *psd_template_obj = NULL;" in ext
        assert "if (psd_template_obj && psd_template_obj != Py_None) {" in ext
        assert (
            "psd_template_arr ? (const float *)PyArray_DATA(psd_template_arr)"
            " : NULL, psd_template_len" in ext
        )

    def test_required_array_unaffected(self, tmp_path):
        # A same-shaped array with NO default is untouched: still hoisted,
        # still mandatory-positional — this is the existing gh-422 contract.
        root = tmp_path / "wfm"
        new_run("wfm", root)
        object_run(
            root,
            "fir",
            None,
            no_state=True,
            init_params=[
                ("taps", "float[]", ""),
                ("gain", "float", "1.0"),
            ],
        )
        ext = _ext_c(root, "fir")
        assert 'kwlist[] = {"taps", "gain", NULL}' in ext


class TestRequiredArrayModulePyiOrder:
    """gh-611 review, item 2: a REQUIRED array (no default at all) skewed the
    module-aggregated `.pyi` the same way a `default = "[]"` array did.

    `test_required_array_unaffected` above only declares the array FIRST,
    which can never expose a skew either way. Reversing the declaration
    order — a scalar (`gain`) declared before a required array (`taps`,
    no default) — shows the C kwlist correctly hoisting `taps` first (a
    required array is a positional-before-`|` param in the C ABI, same as
    any other required param), while `_stubs.py::_obj_stub` (the
    module-aggregated `.pyi` builder, a separate code path from
    `_context/_state.py`) kept printing `gain` then `taps` in manifest
    order with a fake `taps: ... = ...` default — the same class of bug
    gh-611 was filed over, just for a required array instead of a
    defaulted one.
    """

    def test_module_pyi_hoists_required_array_like_kwlist(self, tmp_path):
        root = tmp_path / "pkg"
        new_run("pkg", root, modules=["dsp"])
        object_run(
            root,
            "fir",
            "dsp",
            no_state=True,
            init_params=[
                ("gain", "float", "1.0"),
                ("taps", "float[]", ""),
            ],
        )
        ext = (root / "native" / "src" / "dsp" / "dsp_ext_fir.c").read_text(
            encoding="utf-8"
        )
        assert 'kwlist[] = {"taps", "gain", NULL}' in ext

        pyi = flatten_signatures(
            (root / "src" / "pkg" / "dsp" / "dsp.pyi").read_text(
                encoding="utf-8"
            )
        )
        assert (
            "def __init__(self, taps: NDArray[np.float32],"
            " gain: float = ...) -> None: ..." in pyi
        )


class TestDefaultedArrayValidation:
    def test_2d_default_array_rejected(self, tmp_path):
        root = tmp_path / "wfm"
        new_run("wfm", root)
        with pytest.raises(ValueError, match="1-D"):
            object_run(
                root,
                "corr2d",
                None,
                no_state=True,
                init_params=[("mat", "float[][]", "[]")],
            )

    def test_non_empty_literal_default_rejected(self, tmp_path):
        root = tmp_path / "wfm"
        new_run("wfm", root)
        with pytest.raises(ValueError, match='default = "\\[\\]"'):
            object_run(
                root,
                "weird",
                None,
                no_state=True,
                init_params=[("taps", "float[]", "[1.0, 2.0]")],
            )
