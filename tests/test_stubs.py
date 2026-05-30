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
    return (root / "src" / pkg / module / f"{module}.pyi").read_text(
        encoding="utf-8"
    )


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
    function_run(
        root, "apply", "dsp", params=[("x", "float")], return_type="float"
    )
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
        assert (
            "def execute_ctrl(self, ctrl: NDArray[np.complex64]) -> int:"
            in pyi
        )


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
            root,
            "osc",
            "dsp",
            arg_type="float _Complex",
            return_type="float _Complex",
        )
        pyi = _pyi(root, "dsp", "p")
        assert "def step(self, x: complex) -> complex:" in pyi

    def test_double_maps_to_float(self, tmp_path):
        root = tmp_path / "p"
        new_run("p", root, modules=["dsp"])
        object_run(
            root, "filt", "dsp", arg_type="double", return_type="double"
        )
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
        assert pyi.startswith(
            "# dsp/dsp.pyi — type stubs for the dsp C extension."
        )


class TestArrayArgTypeStubs:
    @pytest.fixture()
    def arr_arg_project(self, tmp_path):
        root = tmp_path / "myproj"
        new_run("myproj", root, modules=["dsp"])
        object_run(
            root,
            "resamp",
            "dsp",
            arg_type="float _Complex[]",
            return_type="int",
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


class TestNoStateStub:
    @pytest.fixture()
    def no_state_project(self, tmp_path):
        root = tmp_path / "myproj"
        new_run("myproj", root, modules=["dsp"])
        object_run(
            root,
            "fir",
            "dsp",
            no_state=True,
            no_step=True,
            arg_type="float _Complex",
            return_type="float _Complex",
        )
        return root

    def test_init_accepts_args(self, no_state_project):
        pyi = _pyi(no_state_project, "dsp", "myproj")
        assert "def __init__(self, /, *args, **kwargs) -> None: ..." in pyi

    def test_init_not_no_arg(self, no_state_project):
        pyi = _pyi(no_state_project, "dsp", "myproj")
        assert "def __init__(self) -> None: ..." not in pyi


class TestBoolPropertyStub:
    @pytest.fixture()
    def bool_prop_project(self, tmp_path):
        root = tmp_path / "myproj"
        new_run("myproj", root, modules=["dsp"])
        object_run(
            root,
            "fir",
            "dsp",
            no_state=True,
            no_step=True,
            arg_type="float _Complex",
            return_type="float _Complex",
        )
        property_run(root, "fir", "is_real", "dsp", "bool", False)
        return root

    def test_bool_property_annotation(self, bool_prop_project):
        pyi = _pyi(bool_prop_project, "dsp", "myproj")
        assert "def is_real(self) -> bool:" in pyi

    def test_bool_not_int(self, bool_prop_project):
        pyi = _pyi(bool_prop_project, "dsp", "myproj")
        assert "def is_real(self) -> int:" not in pyi


class TestMultiOutputArrayStub:
    @pytest.fixture()
    def multi_out_project(self, tmp_path):
        root = tmp_path / "myproj"
        new_run("myproj", root, modules=["dsp"])
        object_run(
            root,
            "nco",
            "dsp",
            no_state=True,
            no_step=True,
            arg_type="void",
            return_type="void",
        )
        method_run(
            root,
            "nco",
            "steps_u32_ovf",
            "dsp",
            "uint32_t[]",
            "uint32_t[]",
            True,
            ["uint8_t[]"],
        )
        return root

    def test_multi_output_tuple_annotation(self, multi_out_project):
        pyi = _pyi(multi_out_project, "dsp", "myproj")
        assert "tuple[NDArray[np.uint32], NDArray[np.uint8]]" in pyi

    def test_no_any_in_multi_output(self, multi_out_project):
        pyi = _pyi(multi_out_project, "dsp", "myproj")
        assert "NDArray[Any]" not in pyi


# ── issue #26 fixes ───────────────────────────────────────────────────────────


class TestStringEnumStub:
    @pytest.fixture()
    def enum_project(self, tmp_path):
        root = tmp_path / "myproj"
        new_run("myproj", root, modules=["dsp"])
        object_run(
            root,
            "det",
            "dsp",
            no_state=True,
            no_step=True,
            arg_type="float",
            return_type="float",
            init_params=[
                ("rate", "double", "1.0", "", "", "", False, ""),
                (
                    "mode",
                    "string_enum:mean,median,min",
                    "mean",
                    "",
                    "",
                    "",
                    False,
                    "",
                ),
            ],
        )
        return root

    def test_literal_in_init(self, enum_project):
        pyi = _pyi(enum_project, "dsp", "myproj")
        assert 'Literal["mean", "median", "min"]' in pyi

    def test_literal_import_emitted(self, enum_project):
        pyi = _pyi(enum_project, "dsp", "myproj")
        assert "from typing import Literal" in pyi

    def test_literal_before_numpy(self, enum_project):
        pyi = _pyi(enum_project, "dsp", "myproj")
        lines = pyi.splitlines()
        lit_idx = next(
            i
            for i, line in enumerate(lines)
            if "from typing import Literal" in line
        )
        np_idx = next(
            i for i, line in enumerate(lines) if "import numpy" in line
        )
        assert lit_idx < np_idx

    def test_default_quoted_in_init(self, enum_project):
        pyi = _pyi(enum_project, "dsp", "myproj")
        assert 'mode: Literal["mean", "median", "min"] = "mean"' in pyi

    def test_no_any_for_enum_param(self, enum_project):
        pyi = _pyi(enum_project, "dsp", "myproj")
        assert "mode: Any" not in pyi

    def test_no_literal_import_when_no_enum(self, basic_project):
        pyi = _pyi(basic_project, "dsp", "myproj")
        assert "from typing import Literal" not in pyi


class TestOutTypeFunctionStub:
    @pytest.fixture()
    def out_type_project(self, tmp_path):
        root = tmp_path / "myproj"
        new_run("myproj", root, modules=["dsp"])
        object_run(root, "dummy", "dsp", arg_type="float", return_type="float")
        function_run(
            root,
            "magnitude_db",
            "dsp",
            params=[("x", "float _Complex[]"), ("floor", "float")],
            return_type="void",
            out_type="float",
        )
        return root

    def test_out_type_ndarray_return(self, out_type_project):
        pyi = _pyi(out_type_project, "dsp", "myproj")
        assert "def magnitude_db(" in pyi
        assert "-> NDArray[np.float32]:" in pyi

    def test_out_type_not_none_return(self, out_type_project):
        pyi = _pyi(out_type_project, "dsp", "myproj")
        for line in pyi.splitlines():
            if "def magnitude_db" in line:
                assert "-> None:" not in line

    def test_numpy_imported_for_out_type(self, out_type_project):
        pyi = _pyi(out_type_project, "dsp", "myproj")
        assert "from numpy.typing import NDArray" in pyi


class TestResultFieldsStub:
    @pytest.fixture()
    def result_fields_project(self, tmp_path):
        root = tmp_path / "myproj"
        new_run("myproj", root, modules=["dsp"])
        object_run(
            root,
            "det",
            "dsp",
            no_state=True,
            no_step=True,
            arg_type="float _Complex[]",
            return_type="void",
        )
        method_run(
            root,
            "det",
            "push",
            "dsp",
            "float _Complex[]",
            "float",
            False,
            [],
            result_fields=[
                {"name": "lag", "type": "size_t"},
                {"name": "peak", "type": "float"},
                {"name": "snr", "type": "double"},
            ],
        )
        return root

    def test_result_fields_return_type(self, result_fields_project):
        pyi = _pyi(result_fields_project, "dsp", "myproj")
        assert "list[tuple[int, float, float]]" in pyi

    def test_no_bare_list_tuple(self, result_fields_project):
        pyi = _pyi(result_fields_project, "dsp", "myproj")
        assert "list[tuple]" not in pyi

    def test_result_fields_method_present(self, result_fields_project):
        pyi = _pyi(result_fields_project, "dsp", "myproj")
        assert "def push(" in pyi


class TestPyReturnTypeStub:
    @pytest.fixture()
    def py_ret_project(self, tmp_path):
        root = tmp_path / "myproj"
        new_run("myproj", root, modules=["dsp"])
        object_run(
            root,
            "det",
            "dsp",
            no_state=True,
            no_step=True,
            arg_type="float _Complex[]",
            return_type="void",
        )
        method_run(
            root,
            "det",
            "push",
            "dsp",
            "float _Complex[]",
            "void",
            False,
            [],
            py_return_type="list[tuple[int, float, float]]",
        )
        return root

    def test_py_return_type_used_in_stub(self, py_ret_project):
        pyi = _pyi(py_ret_project, "dsp", "myproj")
        assert (
            "def push(self, x: NDArray[np.complex64]) -> list[tuple[int, float, float]]:"
            in pyi
        )

    def test_no_any_with_py_return_type(self, py_ret_project):
        pyi = _pyi(py_ret_project, "dsp", "myproj")
        for line in pyi.splitlines():
            if "def push" in line:
                assert "Any" not in line

    def test_py_return_type_persisted_in_toml(self, py_ret_project):
        import just_makeit._config as C

        cfg = C.load(py_ret_project)
        methods = cfg.get("det", {}).get("methods", [])
        push = next(m for m in methods if m["name"] == "push")
        assert push.get("py_return_type") == "list[tuple[int, float, float]]"


class TestStringEnumDocstring:
    @pytest.fixture()
    def enum_doc_project(self, tmp_path):
        root = tmp_path / "myproj"
        new_run("myproj", root, modules=["dsp"])
        object_run(
            root,
            "det",
            "dsp",
            no_state=True,
            no_step=True,
            arg_type="float",
            return_type="float",
            init_params=[
                ("rate", "double", "1.0", "", "", "", False, ""),
                (
                    "mode",
                    "string_enum:mean,median,min",
                    "mean",
                    "",
                    "",
                    "",
                    False,
                    "",
                ),
            ],
        )
        return root

    def test_enum_docstring_not_any(self, enum_doc_project):
        pyi = _pyi(enum_doc_project, "dsp", "myproj")
        assert "mode : Any" not in pyi

    def test_enum_docstring_shows_literal(self, enum_doc_project):
        pyi = _pyi(enum_doc_project, "dsp", "myproj")
        assert 'mode : Literal["mean", "median", "min"]' in pyi


class TestArrayInitParamDocstring:
    @pytest.fixture()
    def array_ip_project(self, tmp_path):
        root = tmp_path / "myproj"
        new_run("myproj", root, modules=["dsp"])
        object_run(
            root,
            "fir",
            "dsp",
            no_state=True,
            no_step=True,
            arg_type="float _Complex",
            return_type="float _Complex",
            init_params=[
                ("n_taps", "int", "64", "", "", "", False, ""),
                ("coeff", "float _Complex[]", "", "", "", "", False, ""),
            ],
        )
        return root

    def test_array_param_docstring_type(self, array_ip_project):
        pyi = _pyi(array_ip_project, "dsp", "myproj")
        assert "coeff : NDArray[np.complex64]" in pyi

    def test_no_any_in_docstring(self, array_ip_project):
        pyi = _pyi(array_ip_project, "dsp", "myproj")
        assert "coeff : Any" not in pyi


class TestTwoDArrayStub:
    @pytest.fixture()
    def twod_project(self, tmp_path):
        root = tmp_path / "myproj"
        new_run("myproj", root, modules=["dsp"])
        object_run(
            root,
            "resamp",
            "dsp",
            no_state=True,
            no_step=True,
            arg_type="float",
            return_type="float",
            init_params=[
                ("rate", "double", "1.0", "", "", "", False, ""),
                (
                    "bank",
                    "float[][]",
                    "",
                    "",
                    "",
                    "",
                    True,
                    "Resamp_create_custom",
                ),
            ],
        )
        return root

    def test_2d_array_ndarray_annotation(self, twod_project):
        pyi = _pyi(twod_project, "dsp", "myproj")
        assert "NDArray[np.float32]" in pyi

    def test_2d_array_not_any(self, twod_project):
        pyi = _pyi(twod_project, "dsp", "myproj")
        assert "bank: Any" not in pyi

    def test_optional_null_default(self, twod_project):
        pyi = _pyi(twod_project, "dsp", "myproj")
        assert "bank: NDArray[np.float32] | None = None" in pyi
