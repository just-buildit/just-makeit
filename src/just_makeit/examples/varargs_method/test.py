"""End-to-end test: jm method --varargs and jm app --argc-argv.

Exercises:
  1. Scaffold a filter project (float step, gain state var).
  2. Add a --varargs method configure and a typed current_gain() companion.
  3. Verify generated file structure and content.
  4. Patch step, configure, and current_gain implementations from .steps/.
  4b. Enrich the header with Doxygen; regenerate the .pyi via jm apply.
  5. cmake configure + build + CTest.
  6. Python smoke test: Filter.step(), configure(gain=...), current_gain().
  6b. Run the header-authored .pyi doctest against the built extension.
  7. jm app --argc-argv: verify generated main has if (argc > 1).

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/varargs_method/test.py
"""

import os
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
    from just_makeit._apply import run as apply_run

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

    # ── 2b. Add a typed companion method current_gain() ───────────────────
    # A plain --param method declared in the sacred header (unlike --varargs,
    # whose binding lives in a PyObject* .c file jm cannot attribute docs to).
    # Its header Doxygen — including a @code doctest — therefore flows into the
    # .pyi, giving the object a runnable, header-authored example.
    jm_method(
        proj,
        object_name="filter",
        method_name="current_gain",
        module=None,
        arg_type="void",
        return_type="double",
        variable_output=False,
        multi_output=[],
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
    assert "def current_gain(self) -> float" in pyi_t

    # ── 4. Implement step, configure, and current_gain ────────────────────
    _cmd([sys.executable, str(STEPS / "03_patch.py")], cwd=proj)

    # ── 4b. Enrich the header with Doxygen, regenerate the stubs ──────────
    # The sacred header is the single source of truth for docs: a hand-written
    # @brief on create() becomes the class summary, and @brief/@return/@code
    # on the typed current_gain() method become a rich numpy-style docstring
    # with a runnable Examples doctest. --varargs configure() cannot carry
    # header docs (its binding lives in a PyObject* .c file), so the doctest
    # lives on current_gain() and exercises configure() from there. `jm apply`
    # re-derives the .pyi from the edited header.
    _cmd([sys.executable, str(STEPS / "04b_doxygen.py")], cwd=proj)
    apply_run(proj)

    # The enrichment reached the stub: class summary from create()'s @brief,
    # and current_gain()'s @brief/@return/@code rendered as a runnable doctest.
    pyi_enriched = (proj / "src" / "va_filter" / "filter.pyi").read_text()
    assert "single-tap gain stage" in pyi_enriched, "class @brief missing"
    assert "Return the filter's current gain coefficient." in pyi_enriched, (
        "current_gain @brief missing"
    )
    assert ">>> f.configure(gain=6.0)" in pyi_enriched, (
        "configure() call missing from current_gain doctest"
    )
    assert ">>> f.current_gain()" in pyi_enriched and "6.0" in pyi_enriched, (
        "current_gain() @code doctest missing"
    )

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
        "f.configure(gain=6.0);"
        "assert f.current_gain() == 6.0;"
        "print('configure: PASSED')"
    )
    _cmd([sys.executable, "-c", smoke], cwd=proj)

    # ── 6b. The header-authored doctest runs against the built .so ────────
    # `pytest --doctest-glob='*.pyi'` imports the compiled extension and
    # executes every `>>>` in the enriched stub — including current_gain()'s
    # @code, which drives configure() and asserts the gain reads back. If the
    # kernel ever drifts from its documented example, CI fails here.
    doctest_res = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--doctest-glob=*.pyi",
            "-q",
            str(Path("src") / "va_filter" / "filter.pyi"),
        ],
        cwd=proj,
        env={**os.environ, "PYTHONPATH": str(proj / "src")},
        capture_output=True,
        text=True,
    )
    assert doctest_res.returncode == 0, (
        "header-authored .pyi doctests failed:\n"
        f"{doctest_res.stdout}\n{doctest_res.stderr}"
    )

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
