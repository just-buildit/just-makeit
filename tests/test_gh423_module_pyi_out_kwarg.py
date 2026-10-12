"""
gh-423 — the module-aggregated `.pyi` stub generator (`_stubs.py::_obj_stub`,
used by `make_module_pyi` for `--module` objects) is a separate code path
from `make_methods_ctx`'s per-object stub (used for standalone objects) and
was never taught the gh-219/gh-412 `out=`/`<name>_max_out()` shape. A
variable_output method on a *module* object therefore kept emitting the
pre-#219 stub signature — no `out=` param, no `<name>_max_out()` method —
even though its `.c` binding correctly gained both.
"""

import re
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._method import run as method_run


def _module_pyi(root, module="dsp"):
    return (root / "src" / "pkg" / module / f"{module}.pyi").read_text(
        encoding="utf-8"
    )


class TestModulePyiOutKwarg:
    def _scaffold(self, tmp_path, params=None, arg_type="void"):
        root = tmp_path / "pkg"
        new_run("pkg", root, modules=["dsp"])
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
        method_run(
            root,
            "nco",
            "execute_cf32",
            "dsp",
            arg_type,
            "float _Complex",
            True,
            [],
            params=params,
        )
        return root

    def test_bare_arg_type_gets_out_kwarg_and_max_out(self, tmp_path):
        root = self._scaffold(tmp_path, arg_type="float _Complex")
        pyi = _module_pyi(root)
        assert "out:" in pyi and "| None = None" in pyi
        # gh-607: max_out() mirrors the kernel's own count param.
        assert "def execute_cf32_max_out(self, n_in: int) -> int:" in pyi

    def test_single_array_param_gets_out_kwarg_and_max_out(self, tmp_path):
        root = self._scaffold(tmp_path, params=[("x", "float _Complex[]")])
        pyi = _module_pyi(root)
        assert "out:" in pyi and "| None = None" in pyi
        assert "def execute_cf32_max_out(self, x_len: int) -> int:" in pyi

    def test_array_beside_a_scalar_gets_out_kwarg_and_max_out(self, tmp_path):
        # Farrow.delay-shaped: an array param beside a scalar. gh-412 left it
        # without `out=`; gh-1998 sizes it from the array, as the allocation
        # already was, so the module stub publishes both.
        root = self._scaffold(
            tmp_path,
            params=[("x", "float _Complex[]"), ("mu", "double")],
        )
        pyi = _module_pyi(root)
        assert "out:" in pyi and "| None = None" in pyi
        assert "def execute_cf32_max_out(self, x_len: int) -> int:" in pyi

    def test_params_beside_an_input_get_out_kwarg_and_max_out(self, tmp_path):
        # An `arg_type` input plus a param: parsed since gh-1960, and offered
        # `out=` since gh-2028, sized from the input as without the param --
        # so `max_out()` takes the same `n_in` the bare input shape's does.
        root = self._scaffold(
            tmp_path, arg_type="float _Complex", params=[("mu", "double")]
        )
        pyi = _module_pyi(root)
        assert re.search(
            r"def execute_cf32\(\s*self,\s*x: [^,]+,\s*mu: float,\s*"
            r"out: [^=]+\| None = None,?\s*\)",
            pyi,
        ), pyi
        assert "def execute_cf32_max_out(self, n_in: int) -> int:" in pyi

    def test_multi_output_gets_no_out_kwarg(self, tmp_path):
        # Two output arrays would need two buffers; one `out=` cannot say
        # which. The one variable_output shape still offered none.
        root = tmp_path / "pkg"
        new_run("pkg", root, modules=["dsp"])
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
        method_run(
            root,
            "nco",
            "execute_cf32",
            "dsp",
            "float _Complex",
            "float _Complex",
            True,
            ["float"],
        )
        pyi = _module_pyi(root)
        assert "execute_cf32_max_out" not in pyi
        sig = re.search(r"def execute_cf32\((.*?)\)\s*->", pyi, re.S)
        assert sig is not None, pyi
        assert "out:" not in sig.group(1)
