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


def _cmd(args, cwd):
    r = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=600,
    )
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
    # Use "va_filter" as the project name to avoid colliding with
    # bench_upgrade, which also creates "my_filter" in the shared dest dir.
    jm_new(
        "va_filter",
        root / "va_filter",
        object_names=["filter"],
        state_vars=[("gain", "double", "1.0")],
        arg_type="float",
        return_type="float",
    )
    proj = root / "va_filter"

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

    pyi_t = (proj / "src" / "va_filter" / "filter.pyi").read_text()
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
    # Run inline rather than calling 05_demo.py (which imports "my_filter")
    # because the test project is named "va_filter" to avoid collisions with
    # bench_upgrade in the shared Docker dest directory.
    smoke = (
        "import sys; sys.path.insert(0, 'src');"
        "from va_filter import Filter;"
        "f = Filter(gain=1.0);"
        "assert f.step(2.0) == 2.0;"
        "f.configure(gain=0.5);"
        "assert f.step(2.0) == 1.0;"
        "f.configure(2.0);"
        "assert f.step(1.0) == 2.0;"
        "print('configure: PASSED')"
    )
    _cmd([sys.executable, "-c", smoke], cwd=proj)

    # ── 7. jm app — generated C binary face over the same core ────────────
    jm_app(
        proj,
        target="c",
        name="filter_tool",
        object_="filter",
    )
    app_text = (proj / "native" / "src" / "app" / "filter_tool.c").read_text()
    # filter is a scalar step() object → jm app generates a working tool:
    # a real argv parser + read→step→write loop, no <<IMPLEMENT>> stub.
    assert "<<IMPLEMENT" not in app_text
    assert "filter_step(state, x)" in app_text
    assert '"--input"' in app_text and "(void)argc" not in app_text


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("varargs_method: PASSED")
