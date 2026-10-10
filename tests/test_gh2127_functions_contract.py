"""gh-2127: a functions-only module's Python tests call its wrappers.

A module that declares module-level functions and no objects built an
extension and generated no Python test that called it. A broken wrapper
passed `jm build` and `jm test` both, and gh-1950 made the empty suite a pass,
so nothing could fail. An object gets a scaffolded test; a function did not.

The maintainer's decision (option c) fixes the contract: each wrapper is called
with arguments derived from its declared parameters, and the test asserts the
call succeeds and the result has the declared return type. It asserts no
values. The bodies are the author's, and their documented cases are the
header's ``@code`` examples.

GATE: a functions-only module's `jm test` collects and passes the contract
test, and a binding that breaks the contract fails it. The build test below
runs the real `jm build` and `jm test`. Sabotage proofs, against the generated
binding and not the kernel:

- in the repo, the float entry of the type registry (``_types.py``) converts
  its return with ``PyLong_FromLong`` instead of ``PyFloat_FromDouble``: the
  build test goes red (run with ``scripts/sabotage.py``);
- in a scratch project, the generated ``fft_ext.c`` drops one character from a
  wrapper's ``PyArg_ParseTupleAndKeywords`` format: the test goes red with a
  ``SystemError``, and the sibling function's test stays green.
"""

# gh-1591: this file's expectations spell jm's bare derived names, so its
# projects opt out of the prefix `jm new` now defaults to.

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from _compilers import default_cc  # noqa: E402
from _jmrun import run_cli  # noqa: E402
from just_makeit import _fncontract as F  # noqa: E402
from just_makeit._function import run as function_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._remove import run as remove_run  # noqa: E402

_NEEDS_BUILD = pytest.mark.skipif(
    shutil.which("cmake") is None or default_cc() is None,
    reason="the build gate needs cmake and a C compiler",
)

_SCALE = {
    "name": "fft_scale",
    "params": [{"name": "n", "type": "int"}, {"name": "x", "type": "float"}],
    "return_type": "float",
}


class TestCaseText:
    """The rendered call and check for each shape the contract covers."""

    def test_scalars_and_a_float_return(self) -> None:
        text, uses_np = F.case(_SCALE)
        assert "result = module.fft_scale(n=1, x=1.0)" in text
        assert "self.assertIsInstance(result, float)" in text
        assert uses_np is False

    def test_an_array_argument_is_a_zero_buffer_of_its_dtype(self) -> None:
        fn = {
            "name": "fft_sum",
            "params": [
                {"name": "buf", "type": "float[]"},
                {"name": "n", "type": "size_t"},
            ],
            "return_type": "double",
        }
        text, uses_np = F.case(fn)
        assert "buf=np.zeros(4, dtype=np.float32)" in text
        assert uses_np is True

    def test_an_out_array_is_passed_as_a_buffer_and_is_not_skipped(
        self,
    ) -> None:
        """The binding fills a caller's writable array and returns None, so
        the contract passes a zero buffer and checks None. Skipping it would
        leave every out-parameter wrapper untested."""
        fn = {
            "name": "fft_fill",
            "params": [
                {"name": "v", "type": "int"},
                {"name": "out", "type": "uint8_t[]", "out": True},
            ],
            "return_type": "void",
        }
        text, uses_np = F.case(fn)
        assert "@unittest.skip" not in text
        assert "out=np.zeros(4, dtype=np.uint8)" in text
        assert "self.assertIsNone(result)" in text
        assert uses_np is True

    def test_void_checks_none(self) -> None:
        fn = {"name": "fft_global_setup", "params": [], "return_type": "void"}
        text, _ = F.case(fn)
        assert "self.assertIsNone(result)" in text

    def test_int_return_rejects_bool(self) -> None:
        """A bool is an int in Python. Without the exclusion a binding that
        returned a bool for an int return would pass."""
        fn = {"name": "count", "params": [], "return_type": "size_t"}
        text, _ = F.case(fn)
        assert "self.assertNotIsInstance(result, bool)" in text

    @pytest.mark.parametrize(
        "fn, reason",
        [
            (
                {
                    "name": "fill_scalar",
                    "params": [{"name": "o", "type": "float", "out": True}],
                    "return_type": "void",
                },
                "parameter 'o'",
            ),
            (
                {
                    "name": "name_of",
                    "params": [],
                    "return_type": "const char *",
                },
                "return type 'const char *'",
            ),
            (
                {
                    "name": "tagged",
                    "params": [{"name": "s", "type": "const char *"}],
                    "return_type": "void",
                },
                "parameter 's'",
            ),
        ],
    )
    def test_an_unsupported_shape_is_a_named_skip_not_a_pass(
        self, fn: dict, reason: str
    ) -> None:
        """A shape the contract cannot call yet becomes a method decorated
        ``unittest.skip`` with the reason. The run reports it as skipped; it
        cannot read as a check that ran and passed."""
        text, _ = F.case(fn)
        assert "@unittest.skip(" in text
        assert reason in text
        assert "result = " not in text

    def test_a_shape_flag_is_a_named_skip(self) -> None:
        fn = dict(_SCALE, variable_output=True, out_type="float")
        text, _ = F.case(fn)
        assert "@unittest.skip(" in text


class TestRenderedModule:
    def test_the_whole_module_compiles(self) -> None:
        """The file is read by pytest in a project, so a generated syntax
        error would be a red suite no one could read."""
        src = F.render("dsp.fft", "fft", [_SCALE])
        compile(src, "test_fft_functions.py", "exec")

    def test_the_module_is_imported_not_its_functions(self) -> None:
        """Importing the module leaves no unused import behind when a wrapper
        is skipped."""
        src = F.render(
            "dsp.fft",
            "fft",
            [dict(_SCALE, variable_output=True, out_type="float")],
        )
        assert "import dsp.fft as module" in src
        assert "from dsp.fft import" not in src


class TestWhatIsWritten:
    @pytest.fixture
    def functions_only(self, tmp_path: Path) -> Path:
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"], c_prefix=None)
        function_run(
            root,
            "fft_scale",
            "fft",
            params=[("n", "int"), ("x", "float")],
            return_type="float",
        )
        function_run(
            root,
            "fft_sum",
            "fft",
            params=[("buf", "float[]"), ("n", "size_t")],
            return_type="double",
        )
        return root

    def test_a_functions_only_module_gets_the_contract_test(
        self, functions_only: Path
    ) -> None:
        """It is written beside the extension, born owned, and lists every
        function the manifest holds, not only the last one added."""
        test = functions_only / "src/dsp/fft/tests/test_fft_functions.py"
        assert test.is_file()
        text = test.read_text(encoding="utf-8")
        assert "jm:generated test_fft_functions.py" in text
        assert "def test_fft_scale_contract" in text
        assert "def test_fft_sum_contract" in text
        assert (functions_only / "src/dsp/fft/tests/__init__.py").is_file()

    def test_removing_a_function_rewrites_the_contract_test(
        self, functions_only: Path
    ) -> None:
        """The file lists the functions it calls, so removing one must drop its
        case in the same command: a replay that no longer produces the case
        would leave a test calling a function that is gone."""
        test = functions_only / "src/dsp/fft/tests/test_fft_functions.py"
        remove_run(
            functions_only, "function", "fft_scale", module="fft", force=True
        )
        text = test.read_text(encoding="utf-8")
        assert "def test_fft_scale_contract" not in text
        assert "def test_fft_sum_contract" in text

    def test_removing_the_last_function_removes_the_owned_contract_test(
        self, functions_only: Path
    ) -> None:
        """A module with no functions has nothing for the file to call, so the
        owned file goes, in the same command."""
        test = functions_only / "src/dsp/fft/tests/test_fft_functions.py"
        remove_run(
            functions_only, "function", "fft_scale", module="fft", force=True
        )
        remove_run(
            functions_only, "function", "fft_sum", module="fft", force=True
        )
        assert not test.exists()

    def test_a_contract_test_the_author_took_over_is_left_alone(
        self, functions_only: Path
    ) -> None:
        """Deleting the ownership token hands the file to its author. jm must
        not delete a file it no longer owns, even when the last function goes."""
        test = functions_only / "src/dsp/fft/tests/test_fft_functions.py"
        text = test.read_text(encoding="utf-8")
        taken = text.replace("jm:generated", "authored")
        test.write_text(taken, encoding="utf-8")
        remove_run(
            functions_only, "function", "fft_scale", module="fft", force=True
        )
        assert test.read_text(encoding="utf-8") == taken
        remove_run(
            functions_only, "function", "fft_sum", module="fft", force=True
        )
        assert test.read_text(encoding="utf-8") == taken

    def test_a_mixed_module_gets_the_contract_test(
        self, tmp_path: Path
    ) -> None:
        """A module with objects and functions: the functions are called by the
        contract test beside the objects' own tests (#2156)."""
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["dsp"], c_prefix=None)
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
        function_run(root, "global_setup", "dsp", doc="Setup.")
        test = root / "src/dsp/dsp/tests/test_dsp_functions.py"
        assert test.is_file()
        assert "def test_global_setup_contract" in test.read_text(
            encoding="utf-8"
        )


@_NEEDS_BUILD
class TestTheBuildRuns:
    def test_a_functions_only_project_builds_and_its_test_passes(
        self, tmp_path: Path
    ) -> None:
        """The real `jm build` then `jm test`, on a project of one typed
        function. The wrapper is a stub that returns the right type, so the
        contract holds and the suite must pass."""
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"], c_prefix=None)
        function_run(
            root,
            "fft_scale",
            "fft",
            params=[("n", "int"), ("x", "float")],
            return_type="float",
        )
        built = run_cli("build", cwd=root)
        assert built.returncode == 0, built.stdout + built.stderr
        ran = run_cli("test", cwd=root)
        assert ran.returncode == 0, ran.stdout + ran.stderr
        assert "1 passed" in ran.stdout + ran.stderr

    def test_a_mixed_module_builds_and_its_contract_test_passes(
        self, tmp_path: Path
    ) -> None:
        """A module with an object and a function: the real `jm build` then
        `jm test`, with the function's contract test among the tests that run
        and pass (#2156)."""
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["dsp"], c_prefix=None)
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
        function_run(
            root,
            "global_setup",
            "dsp",
            params=[("n", "int")],
            return_type="float",
        )
        built = run_cli("build", cwd=root)
        assert built.returncode == 0, built.stdout + built.stderr
        ran = run_cli("test", cwd=root)
        output = ran.stdout + ran.stderr
        assert ran.returncode == 0, output
        assert "test_global_setup_contract PASSED" in output
        assert "FAILED" not in output
