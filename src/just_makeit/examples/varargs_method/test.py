"""End-to-end test: jm method --varargs and jm app --argc-argv.

Exercises:
  1. Scaffold a filter project (float step, gain state var).
  2. Add a --varargs method configure.
  3. Verify generated file structure and content.
  4. Patch step and configure implementations from .steps/.
  5. cmake configure + build + CTest.
  6. Python smoke test: Filter.step(), configure(gain=...).
  7. jm app --argc-argv: verify generated main has if (argc > 1).

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/varargs_method/test.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
STEPS = HERE / ".steps"


def _cmake_gen():
    return ["-G", "MinGW Makefiles"] if sys.platform == "win32" else []


def _cmd(args, cwd):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\n"
            f"stderr:\n{r.stderr}"
        )
    return r


def run(root: Path) -> None:
    from just_makeit._new import run as jm_new
    from just_makeit._method import run as jm_method
    from just_makeit._app import run as jm_app

    # ── 1. Scaffold ───────────────────────────────────────────────────────
    jm_new(
        "my_filter",
        root / "my_filter",
        object_names=["filter"],
        state_vars=[("gain", "double", "1.0")],
        arg_type="float",
        return_type="float",
    )
    proj = root / "my_filter"

    # ── 2. Add configure --varargs ────────────────────────────────────────
    jm_method(
        proj,
        object_name="filter",
        method_name="configure",
        module=None,
        arg_type="float",
        return_type="float",
        variable_output=False,
        multi_output=[],
        varargs=True,
    )

    # ── 3. Verify generated artifacts ────────────────────────────────────

    binding_c = proj / "native" / "src" / "filter" / "filter_configure_core.c"
    assert binding_c.exists(), f"Sacred binding file missing: {binding_c}"
    bt = binding_c.read_text()
    assert "#include <Python.h>" in bt
    assert "PyObject *" in bt
    assert "IMPLEMENT" in bt

    ext_c = proj / "native" / "src" / "filter" / "filter_ext.c"
    et = ext_c.read_text()
    assert "extern PyObject *" in et
    assert "filter_configure(" in et
    assert "METH_VARARGS | METH_KEYWORDS" in et
    assert "filter_configure_core.c" in et

    cmake_t = (
        proj / "native" / "src" / "filter" / "CMakeLists.txt"
    ).read_text()
    assert "filter_configure_core.c" in cmake_t

    pyi_t = (proj / "src" / "my_filter" / "filter.pyi").read_text()
    assert "def configure(self, *args: Any, **kwargs: Any) -> Any" in pyi_t

    # ── 4. Implement step and configure ──────────────────────────────────
    _cmd([sys.executable, str(STEPS / "03_patch.py")], cwd=proj)

    # ── 5. cmake configure + build + CTest ───────────────────────────────
    _cmd(
        [
            "cmake",
            "-B",
            "build",
            "-S",
            ".",
            *_cmake_gen(),
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        cwd=proj,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(
        ["ctest", "--test-dir", "build", "--output-on-failure"],
        cwd=proj,
    )

    # ── 6. Python smoke test ──────────────────────────────────────────────
    _cmd([sys.executable, str(STEPS / "05_demo.py")], cwd=proj)

    # ── 7. jm app --argc-argv ────────────────────────────────────────────
    jm_app(
        proj,
        target="c",
        name="filter_tool",
        object_="filter",
        argc_argv=True,
    )
    app_text = (proj / "native" / "src" / "app" / "filter_tool.c").read_text()
    assert "if (argc > 1)" in app_text
    assert "(void)argc" not in app_text


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("varargs_method: PASSED")
