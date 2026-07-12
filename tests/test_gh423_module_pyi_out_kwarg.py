"""
gh-423 — the module-aggregated `.pyi` stub generator (`_stubs.py::_obj_stub`,
used by `make_module_pyi` for `--module` objects) is a separate code path
from `make_methods_ctx`'s per-object stub (used for standalone objects) and
was never taught the gh-219/gh-412 `out=`/`<name>_max_out()` shape. A
variable_output method on a *module* object therefore kept emitting the
pre-#219 stub signature — no `out=` param, no `<name>_max_out()` method —
even though its `.c` binding correctly gained both.
"""

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
        assert "def execute_cf32_max_out(self) -> int:" in pyi

    def test_single_array_param_gets_out_kwarg_and_max_out(self, tmp_path):
        root = self._scaffold(tmp_path, params=[("x", "float _Complex[]")])
        pyi = _module_pyi(root)
        assert "out:" in pyi and "| None = None" in pyi
        assert "def execute_cf32_max_out(self) -> int:" in pyi

    def test_extra_param_method_gets_no_out_kwarg(self, tmp_path):
        # Farrow.delay-shaped: variable_output with a genuine extra scalar
        # param — stays ineligible for `out=` (gh-412).
        root = self._scaffold(
            tmp_path,
            params=[("x", "float _Complex[]"), ("mu", "double")],
        )
        pyi = _module_pyi(root)
        assert "execute_cf32_max_out" not in pyi
