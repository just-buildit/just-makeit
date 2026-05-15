"""Integration tests for __init__.pyi type-stub generation."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._function import run as function_run
from just_makeit._method import run as method_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._property import run as property_run


# ── helpers ───────────────────────────────────────────────────────────────────


def _pyi(root: Path, module: str, pkg: str) -> str:
    return (root / "src" / pkg / module / f"{module}.pyi").read_text(encoding="utf-8")


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def basic_project(tmp_path):
    """Project with one object: float arg/return, one state var, one method,
    one writable property, one read-only property, one module function."""
    root = tmp_path / "myproj"
    new_run("myproj", root, modules=["dsp"])
    object_run(
        root,
        "filt",
        "dsp",
        state_vars=[("coeff", "float", "0.5")],
        arg_type="float",
        return_type="float",
    )
    # method_run: root, object, method, module, arg_type, return_type,
    #             variable_output, multi_output, params
    method_run(root, "filt", "reset", "dsp", "void", "void", False, [])
    property_run(root, "filt", "gain", "dsp", "float", True)
    property_run(root, "filt", "order", "dsp", "int", False)
    function_run(root, "apply", "dsp", params=[("x", "float")], return_type="float")
    return root


@pytest.fixture()
def void_return_project(tmp_path):
    """Sink object: float arg, void return."""
    root = tmp_path / "myproj"
    new_run("myproj", root, modules=["dsp"])
    object_run(root, "sink", "dsp", arg_type="float", return_type="void")
    return root


@pytest.fixture()
def void_arg_project(tmp_path):
    """Generator object: void arg, float return."""
    root = tmp_path / "myproj"
    new_run("myproj", root, modules=["dsp"])
    object_run(root, "gen", "dsp", arg_type="void", return_type="float")
    return root


@pytest.fixture()
def both_void_project(tmp_path):
    """Pure side-effect object: void arg, void return."""
    root = tmp_path / "myproj"
    new_run("myproj", root, modules=["dsp"])
    object_run(root, "proc", "dsp", arg_type="void", return_type="void")
    return root


@pytest.fixture()
def array_param_project(tmp_path):
    """Object with an array-param method."""
    root = tmp_path / "myproj"
    new_run("myproj", root, modules=["dsp"])
    object_run(
        root,
        "resamp",
        "dsp",
        state_vars=[("rate", "double", "1.0")],
        arg_type="void",
        return_type="int",
    )
    method_run(
        root,
        "resamp",
        "execute_ctrl",
        "dsp",
        "void",
        "int",
        False,
        [],
        params=[("ctrl", "float _Complex[]")],
    )
    return root


# ── file presence ─────────────────────────────────────────────────────────────


class TestStubFileCreated:
    def test_pyi_created_with_module(self, tmp_path):
        root = tmp_path / "p"
        new_run("p", root)
        module_run(root, "dsp")
        assert (root / "src" / "p" / "dsp" / "dsp.pyi").exists()

    def test_pyi_updated_after_object(self, basic_project):
        assert (basic_project / "src" / "myproj" / "dsp" / "dsp.pyi").exists()

    def test_pyi_updated_after_method(self, basic_project):
        pyi = _pyi(basic_project, "dsp", "myproj")
        assert "def reset" in pyi

    def test_pyi_updated_after_property(self, basic_project):
        pyi = _pyi(basic_project, "dsp", "myproj")
        assert "def gain" in pyi

    def test_pyi_updated_after_function(self, basic_project):
        pyi = _pyi(basic_project, "dsp", "myproj")
        assert "def apply" in pyi


# ── class and constructor ─────────────────────────────────────────────────────


class TestClassStub:
    def test_class_name_title_cased(self, basic_project):
        pyi = _pyi(basic_project, "dsp", "myproj")
        assert "class Filt:" in pyi

    def test_init_with_state_param(self, basic_project):
        pyi = _pyi(basic_project, "dsp", "myproj")
        assert "def __init__(self, coeff: float = ...) -> None: ..." in pyi

    def test_init_default_gain_param(self, void_arg_project):
        # object_run defaults to state_vars=[("gain","double","0.0")] when
        # none are supplied.
        pyi = _pyi(void_arg_project, "dsp", "myproj")
        assert "def __init__(self, gain: float = ...) -> None: ..." in pyi


# ── step / steps ──────────────────────────────────────────────────────────────


class TestStepStubs:
    def test_step_scalar_arg_return(self, basic_project):
        pyi = _pyi(basic_project, "dsp", "myproj")
        assert "def step(self, x: float) -> float:" in pyi

    def test_steps_ndarray_arg_return(self, basic_project):
        pyi = _pyi(basic_project, "dsp", "myproj")
        assert "def steps(self, x: NDArray[np.float32]" in pyi
        assert "NDArray[np.float32]" in pyi

    def test_step_void_return(self, void_return_project):
        pyi = _pyi(void_return_project, "dsp", "myproj")
        assert "def step(self, x: float) -> None:" in pyi

    def test_steps_void_return(self, void_return_project):
        pyi = _pyi(void_return_project, "dsp", "myproj")
        assert "def steps(self, x: NDArray[np.float32]) -> None:" in pyi

    def test_step_void_arg(self, void_arg_project):
        pyi = _pyi(void_arg_project, "dsp", "myproj")
        assert "def step(self) -> float:" in pyi

    def test_steps_void_arg(self, void_arg_project):
        pyi = _pyi(void_arg_project, "dsp", "myproj")
        assert "def steps(self, n: int) -> NDArray[np.float32]:" in pyi

    def test_steps_both_void(self, both_void_project):
        pyi = _pyi(both_void_project, "dsp", "myproj")
        assert "def steps(self, n: int) -> None:" in pyi


# ── methods ───────────────────────────────────────────────────────────────────


class TestMethodStubs:
    def test_void_method(self, basic_project):
        pyi = _pyi(basic_project, "dsp", "myproj")
        assert "def reset(self) -> None:" in pyi

    def test_array_param_method(self, array_param_project):
        pyi = _pyi(array_param_project, "dsp", "myproj")
        assert "def execute_ctrl(self, ctrl: NDArray[np.complex64]) -> int:" in pyi


# ── properties ────────────────────────────────────────────────────────────────


class TestPropertyStubs:
    def test_writable_property_has_setter(self, basic_project):
        pyi = _pyi(basic_project, "dsp", "myproj")
        assert "@gain.setter" in pyi
        assert "def gain(self, value: float) -> None: ..." in pyi

    def test_readonly_property_has_no_setter(self, basic_project):
        pyi = _pyi(basic_project, "dsp", "myproj")
        assert "@order.setter" not in pyi

    def test_property_getter_annotation(self, basic_project):
        pyi = _pyi(basic_project, "dsp", "myproj")
        assert "def order(self) -> int:" in pyi


# ── module-level functions ────────────────────────────────────────────────────


class TestFunctionStubs:
    def test_function_signature(self, basic_project):
        pyi = _pyi(basic_project, "dsp", "myproj")
        assert "def apply(x: float) -> float:" in pyi

    def test_function_at_module_level(self, basic_project):
        pyi = _pyi(basic_project, "dsp", "myproj")
        # must not be indented (not inside the class)
        for line in pyi.splitlines():
            if "def apply" in line:
                assert not line.startswith(" ")


# ── numpy imports ─────────────────────────────────────────────────────────────


class TestNumpyImports:
    def test_numpy_imported_when_needed(self, basic_project):
        pyi = _pyi(basic_project, "dsp", "myproj")
        assert "import numpy as np" in pyi
        assert "from numpy.typing import NDArray" in pyi

    def test_numpy_omitted_when_not_needed(self, both_void_project):
        pyi = _pyi(both_void_project, "dsp", "myproj")
        assert "import numpy" not in pyi
        assert "NDArray" not in pyi


# ── type annotations ──────────────────────────────────────────────────────────


class TestTypeAnnotations:
    def test_complex_maps_to_complex(self, tmp_path):
        root = tmp_path / "p"
        new_run("p", root, modules=["dsp"])
        object_run(
            root, "osc", "dsp", arg_type="float _Complex", return_type="float _Complex"
        )
        pyi = _pyi(root, "dsp", "p")
        assert "def step(self, x: complex) -> complex:" in pyi

    def test_double_maps_to_float(self, tmp_path):
        root = tmp_path / "p"
        new_run("p", root, modules=["dsp"])
        object_run(root, "filt", "dsp", arg_type="double", return_type="double")
        pyi = _pyi(root, "dsp", "p")
        assert "def step(self, x: float) -> float:" in pyi

    def test_size_t_maps_to_int(self, array_param_project):
        pyi = _pyi(array_param_project, "dsp", "myproj")
        assert "def step(self) -> int:" in pyi

    def test_array_param_ndarray(self, array_param_project):
        pyi = _pyi(array_param_project, "dsp", "myproj")
        assert "NDArray[np.complex64]" in pyi


# ── header comment ────────────────────────────────────────────────────────────


class TestStubHeader:
    def test_header_comment(self, basic_project):
        pyi = _pyi(basic_project, "dsp", "myproj")
        assert pyi.startswith("# dsp/dsp.pyi — type stubs for the dsp C extension.")


class TestArrayArgTypeStubs:
    @pytest.fixture()
    def arr_arg_project(self, tmp_path):
        root = tmp_path / "myproj"
        new_run("myproj", root, modules=["dsp"])
        object_run(
            root, "resamp", "dsp", arg_type="float _Complex[]", return_type="int"
        )
        return root

    def test_step_annotation(self, arr_arg_project):
        pyi = _pyi(arr_arg_project, "dsp", "myproj")
        assert "def step(self, x: NDArray[np.complex64]) -> int:" in pyi

    def test_no_steps(self, arr_arg_project):
        pyi = _pyi(arr_arg_project, "dsp", "myproj")
        assert "def steps" not in pyi

    def test_numpy_imported(self, arr_arg_project):
        pyi = _pyi(arr_arg_project, "dsp", "myproj")
        assert "import numpy as np" in pyi
        assert "from numpy.typing import NDArray" in pyi
