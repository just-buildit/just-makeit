"""Integration tests for `just-makeit` --pure scaffolding."""

import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._init import run as init_run
from just_makeit import _config as C


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def scalar_pure(tmp_path):
    """Project with a scalar-pure component (all scalar state)."""
    dest = tmp_path / "my_norm"
    new_run("my_norm", dest, object_name="norm", state_vars=[("scale", "double", "1.0")], pure=True)
    return dest


@pytest.fixture()
def struct_pure(tmp_path):
    """Project with a struct-pure component (array state triggers struct mode)."""
    dest = tmp_path / "my_fir"
    new_run(
        "my_fir",
        dest,
        object_name="fir",
        state_vars=[("taps", "float[16]", ""), ("n_taps", "int", "16")],
        pure=True,
    )
    return dest


@pytest.fixture()
def scalar_pure_no_state(tmp_path):
    """Scalar pure with no extra params (just x → y)."""
    dest = tmp_path / "my_pass"
    new_run("my_pass", dest, object_name="passthrough", pure=True, state_vars=[])
    return dest


# ── Config persistence ────────────────────────────────────────────────────────

class TestPureConfig:
    def test_scalar_pure_style_in_toml(self, scalar_pure):
        cfg = C.load(scalar_pure)
        assert C.pure_style(cfg, "norm") == "scalar"

    def test_struct_pure_style_in_toml(self, struct_pure):
        cfg = C.load(struct_pure)
        assert C.pure_style(cfg, "fir") == "struct"

    def test_is_pure_component(self, scalar_pure):
        cfg = C.load(scalar_pure)
        assert C.is_pure_component(cfg, "norm")

    def test_stateful_not_pure(self, tmp_path):
        dest = tmp_path / "s"
        new_run("s", dest, object_name="gain")
        cfg = C.load(dest)
        assert not C.is_pure_component(cfg, "gain")

    def test_scalar_state_persisted(self, scalar_pure):
        cfg = C.load(scalar_pure)
        sv = C.state_vars(cfg, "norm")
        assert any(n == "scale" for n, _, _ in sv)

    def test_struct_state_persisted(self, struct_pure):
        cfg = C.load(struct_pure)
        sv = C.state_vars(cfg, "fir")
        names = [n for n, _, _ in sv]
        assert "taps" in names
        assert "n_taps" in names


# ── File presence ─────────────────────────────────────────────────────────────

class TestScalarPureFiles:
    def test_core_h_exists(self, scalar_pure):
        assert (scalar_pure / "native" / "inc" / "norm" / "norm_core.h").exists()

    def test_core_c_exists(self, scalar_pure):
        assert (scalar_pure / "native" / "src" / "norm" / "norm_core.c").exists()

    def test_ext_c_exists(self, scalar_pure):
        assert (scalar_pure / "native" / "src" / "norm" / "norm_ext.c").exists()

    def test_test_c_exists(self, scalar_pure):
        assert (scalar_pure / "native" / "tests" / "test_norm_core.c").exists()

    def test_bench_c_exists(self, scalar_pure):
        assert (scalar_pure / "native" / "benchmarks" / "bench_norm_core.c").exists()

    def test_pyi_exists(self, scalar_pure):
        assert (scalar_pure / "src" / "my_norm" / "norm.pyi").exists()

    def test_pytest_test_exists(self, scalar_pure):
        assert (scalar_pure / "src" / "my_norm" / "tests" / "test_norm.py").exists()

    def test_bench_py_exists(self, scalar_pure):
        assert (scalar_pure / "src" / "my_norm" / "benchmarks" / "bench_norm.py").exists()

    def test_init_py_exists(self, scalar_pure):
        assert (scalar_pure / "src" / "my_norm" / "__init__.py").exists()


class TestStructPureFiles:
    def test_core_h_exists(self, struct_pure):
        assert (struct_pure / "native" / "inc" / "fir" / "fir_core.h").exists()

    def test_core_c_exists(self, struct_pure):
        assert (struct_pure / "native" / "src" / "fir" / "fir_core.c").exists()

    def test_ext_c_exists(self, struct_pure):
        assert (struct_pure / "native" / "src" / "fir" / "fir_ext.c").exists()

    def test_pyi_exists(self, struct_pure):
        assert (struct_pure / "src" / "my_fir" / "fir.pyi").exists()

    def test_pytest_test_exists(self, struct_pure):
        assert (struct_pure / "src" / "my_fir" / "tests" / "test_fir.py").exists()


# ── Content: scalar pure ──────────────────────────────────────────────────────

class TestScalarPureContent:
    def test_no_unreplaced_placeholders(self, scalar_pure):
        for path in scalar_pure.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".c", ".h", ".toml", ".txt", ".md"):
                text = path.read_text(encoding="utf-8")
                assert "<<" not in text, f"Unreplaced placeholder in {path}"

    def test_header_has_fn_signature(self, scalar_pure):
        h = (scalar_pure / "native" / "inc" / "norm" / "norm_core.h").read_text()
        assert "norm_fn(float complex x" in h
        assert "double scale" in h

    def test_header_has_steps_signature(self, scalar_pure):
        h = (scalar_pure / "native" / "inc" / "norm" / "norm_core.h").read_text()
        assert "norm_steps(" in h

    def test_header_no_params_struct(self, scalar_pure):
        h = (scalar_pure / "native" / "inc" / "norm" / "norm_core.h").read_text()
        assert "norm_params_t" not in h

    def test_ext_exports_two_module_functions(self, scalar_pure):
        ext = (scalar_pure / "native" / "src" / "norm" / "norm_ext.c").read_text()
        assert "py_norm" in ext
        assert "py_norm_steps" in ext
        assert "PyInit_norm" in ext

    def test_ext_no_typeobject(self, scalar_pure):
        ext = (scalar_pure / "native" / "src" / "norm" / "norm_ext.c").read_text()
        assert "PyTypeObject" not in ext

    def test_pyi_has_functions_not_class(self, scalar_pure):
        pyi = (scalar_pure / "src" / "my_norm" / "norm.pyi").read_text()
        assert "def norm(" in pyi
        assert "def norm_steps(" in pyi
        assert "class Norm" not in pyi

    def test_pyi_has_scale_param(self, scalar_pure):
        pyi = (scalar_pure / "src" / "my_norm" / "norm.pyi").read_text()
        assert "scale" in pyi

    def test_init_py_pure_scalar_pattern(self, scalar_pure):
        init = (scalar_pure / "src" / "my_norm" / "__init__.py").read_text()
        assert "from .norm import norm, norm_steps" in init
        assert "norm.steps = norm_steps" in init

    def test_pytest_imports_functions(self, scalar_pure):
        test = (scalar_pure / "src" / "my_norm" / "tests" / "test_norm.py").read_text()
        assert "from my_norm import norm, norm_steps" in test

    def test_pytest_has_steps_attr_test(self, scalar_pure):
        test = (scalar_pure / "src" / "my_norm" / "tests" / "test_norm.py").read_text()
        assert "test_steps_attr" in test

    def test_bench_py_imports_functions(self, scalar_pure):
        bench = (scalar_pure / "src" / "my_norm" / "benchmarks" / "bench_norm.py").read_text()
        assert "from my_norm import norm, norm_steps" in bench

    def test_test_c_calls_fn(self, scalar_pure):
        c = (scalar_pure / "native" / "tests" / "test_norm_core.c").read_text()
        assert "norm_fn(" in c
        assert "norm_steps(" in c


# ── Content: struct pure ──────────────────────────────────────────────────────

class TestStructPureContent:
    def test_no_unreplaced_placeholders(self, struct_pure):
        for path in struct_pure.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".c", ".h", ".toml", ".txt", ".md"):
                text = path.read_text(encoding="utf-8")
                assert "<<" not in text, f"Unreplaced placeholder in {path}"

    def test_header_has_params_struct(self, struct_pure):
        h = (struct_pure / "native" / "inc" / "fir" / "fir_core.h").read_text()
        assert "fir_params_t" in h
        assert "float taps[16]" in h
        assert "int n_taps" in h

    def test_header_has_alloc_helpers(self, struct_pure):
        h = (struct_pure / "native" / "inc" / "fir" / "fir_core.h").read_text()
        assert "fir_params_create" in h
        assert "fir_params_free" in h
        assert "fir_params_init" in h

    def test_header_fn_takes_params_ptr(self, struct_pure):
        h = (struct_pure / "native" / "inc" / "fir" / "fir_core.h").read_text()
        assert "fir_fn(float complex x, fir_params_t *params)" in h

    def test_core_c_has_calloc(self, struct_pure):
        c = (struct_pure / "native" / "src" / "fir" / "fir_core.c").read_text()
        assert "calloc" in c
        assert "aligned_alloc" in c

    def test_core_c_has_params_init(self, struct_pure):
        c = (struct_pure / "native" / "src" / "fir" / "fir_core.c").read_text()
        assert "fir_params_init" in c
        assert "memset" in c

    def test_ext_has_typeobject(self, struct_pure):
        ext = (struct_pure / "native" / "src" / "fir" / "fir_ext.c").read_text()
        assert "PyTypeObject" in ext

    def test_ext_has_tp_call(self, struct_pure):
        ext = (struct_pure / "native" / "src" / "fir" / "fir_ext.c").read_text()
        assert "tp_call" in ext

    def test_ext_no_step_method_name(self, struct_pure):
        ext = (struct_pure / "native" / "src" / "fir" / "fir_ext.c").read_text()
        assert '"step"' not in ext

    def test_pyi_has_class_not_functions(self, struct_pure):
        pyi = (struct_pure / "src" / "my_fir" / "fir.pyi").read_text()
        assert "class Fir:" in pyi
        assert "def fir(" not in pyi

    def test_pyi_has_call_not_step(self, struct_pure):
        pyi = (struct_pure / "src" / "my_fir" / "fir.pyi").read_text()
        assert "def __call__(" in pyi
        assert "def step(" not in pyi

    def test_init_py_imports_class(self, struct_pure):
        init = (struct_pure / "src" / "my_fir" / "__init__.py").read_text()
        assert "from .fir import Fir" in init

    def test_pytest_imports_class(self, struct_pure):
        test = (struct_pure / "src" / "my_fir" / "tests" / "test_fir.py").read_text()
        assert "from my_fir import Fir" in test

    def test_pytest_has_context_manager(self, struct_pure):
        test = (struct_pure / "src" / "my_fir" / "tests" / "test_fir.py").read_text()
        assert "test_context_manager" in test

    def test_pytest_has_destroy(self, struct_pure):
        test = (struct_pure / "src" / "my_fir" / "tests" / "test_fir.py").read_text()
        assert "test_destroy" in test


# ── Auto-detection ────────────────────────────────────────────────────────────

class TestPureAutoDetect:
    def test_all_scalar_gives_scalar_style(self, tmp_path):
        dest = tmp_path / "p"
        new_run("p", dest, object_name="comp",
                state_vars=[("a", "double", "1.0"), ("b", "int", "4")], pure=True)
        cfg = C.load(dest)
        assert C.pure_style(cfg, "comp") == "scalar"

    def test_one_array_gives_struct_style(self, tmp_path):
        dest = tmp_path / "p"
        new_run("p", dest, object_name="comp",
                state_vars=[("buf", "float[8]", "")], pure=True)
        cfg = C.load(dest)
        assert C.pure_style(cfg, "comp") == "struct"

    def test_mixed_scalar_and_array_gives_struct(self, tmp_path):
        dest = tmp_path / "p"
        new_run("p", dest, object_name="comp",
                state_vars=[("gain", "double", "1.0"), ("buf", "float[8]", "")], pure=True)
        cfg = C.load(dest)
        assert C.pure_style(cfg, "comp") == "struct"

    def test_no_state_gives_scalar_style(self, tmp_path):
        dest = tmp_path / "p"
        new_run("p", dest, object_name="comp", state_vars=[], pure=True)
        cfg = C.load(dest)
        assert C.pure_style(cfg, "comp") == "scalar"

    def test_no_pure_flag_gives_stateful(self, tmp_path):
        dest = tmp_path / "p"
        new_run("p", dest, object_name="comp", state_vars=[("gain", "double", "1.0")])
        cfg = C.load(dest)
        assert C.pure_style(cfg, "comp") is None


# ── init command with --pure ──────────────────────────────────────────────────

class TestInitPure:
    def test_init_pure_scalar(self, tmp_path):
        from just_makeit._new import run as new_run2
        dest = tmp_path / "proj"
        new_run2("proj", dest)
        init_run(dest, "norm", [("scale", "double", "1.0")], pure=True)
        cfg = C.load(dest)
        assert C.pure_style(cfg, "norm") == "scalar"
        h = (dest / "native" / "inc" / "norm" / "norm_core.h").read_text()
        assert "norm_fn(" in h

    def test_init_pure_struct(self, tmp_path):
        from just_makeit._new import run as new_run2
        dest = tmp_path / "proj"
        new_run2("proj", dest)
        init_run(dest, "fir", [("taps", "float[8]", "")], pure=True)
        cfg = C.load(dest)
        assert C.pure_style(cfg, "fir") == "struct"
        h = (dest / "native" / "inc" / "fir" / "fir_core.h").read_text()
        assert "fir_params_t" in h
