"""Integration test: run each step of examples/fir_filter/README.md in sequence.

Each step mirrors what a user following the README would do.  If any command
fails, or expected output is not produced, the test fails — keeping the README
honest.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

STEPS = Path(__file__).parent.parent / "examples" / "fir_filter" / ".steps"
PYTHON = sys.executable


def _run(cmd: list[str], cwd: Path, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, **kw)


def _require(name: str) -> None:
    if not shutil.which(name):
        pytest.skip(f"{name} not found")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    """Scaffold → implement → build.  Shared by all steps."""
    _require("cmake")
    _require("gcc")
    _require("just-makeit")

    root = tmp_path_factory.mktemp("fir") / "my_fir"

    # Step 1 — scaffold
    r = _run(
        [
            "just-makeit",
            "new",
            "my_fir",
            "--component",
            "fir_filter",
            "--state",
            "coeffs:float[16]",
            "--state",
            "delay:float _Complex[16]",
            "--state",
            "gain:float:1.0",
        ],
        cwd=root.parent,
    )
    assert r.returncode == 0, f"scaffold failed:\n{r.stderr}"

    # Step 2 — apply implementation
    r = _run([PYTHON, str(STEPS / "02_patch.py")], cwd=root)
    assert r.returncode == 0, f"patch failed:\n{r.stderr}"

    # Step 3 — build
    r = _run(["make"], cwd=root)
    assert r.returncode == 0, f"make failed:\n{r.stderr}"

    return root


# ---------------------------------------------------------------------------
# Step 3: make test
# ---------------------------------------------------------------------------


class TestStep3Build:
    def test_make_test_passes(self, project):
        r = _run(["make", "test"], cwd=project)
        assert r.returncode == 0, f"make test failed:\n{r.stdout}\n{r.stderr}"
        assert "passed" in r.stdout


# ---------------------------------------------------------------------------
# Step 4: pip install + Python demo
# ---------------------------------------------------------------------------


class TestStep4Python:
    @pytest.fixture(scope="class")
    def installed(self, project):
        r = _run(["uv", "pip", "install", "-e", "."], cwd=project)
        assert r.returncode == 0, f"pip install failed:\n{r.stderr}"
        return project

    def test_writeable_false(self, installed):
        r = _run([PYTHON, str(STEPS / "04_demo.py")], cwd=installed)
        assert r.returncode == 0, f"demo.py failed:\n{r.stderr}"
        assert "writeable: False" in r.stdout

    def test_h1_value(self, installed):
        r = _run([PYTHON, str(STEPS / "04_demo.py")], cwd=installed)
        assert "h[1]: 0.5" in r.stdout

    def test_impulse_response(self, installed):
        r = _run([PYTHON, str(STEPS / "04_demo.py")], cwd=installed)
        assert "impulse response:" in r.stdout

    def test_gain2_response(self, installed):
        r = _run([PYTHON, str(STEPS / "04_demo.py")], cwd=installed)
        assert "gain=2 response:" in r.stdout


# ---------------------------------------------------------------------------
# Step 5: C demo
# ---------------------------------------------------------------------------


class TestStep5C:
    @pytest.fixture(scope="class")
    def c_output(self, project):
        demo_c = project / "demo.c"
        shutil.copy(STEPS / "05_demo.c", demo_c)
        lib = (
            project / "build" / "native" / "src" / "fir_filter" / "libfir_filter_core.a"
        )
        r = _run(
            [
                "gcc",
                "-O2",
                "-std=c99",
                "-Inative/inc",
                "demo.c",
                str(lib),
                "-lm",
                "-o",
                "demo",
            ],
            cwd=project,
        )
        assert r.returncode == 0, f"gcc failed:\n{r.stderr}"
        r = _run(["./demo"], cwd=project)
        assert r.returncode == 0, f"demo failed:\n{r.stderr}"
        return r.stdout

    def test_h1(self, c_output):
        assert "h[1] = 0.50" in c_output

    def test_impulse_response(self, c_output):
        assert "out[0]=0.25" in c_output
        assert "out[1]=0.50" in c_output
        assert "out[2]=0.25" in c_output

    def test_delay_line(self, c_output):
        assert "delay[0]" in c_output


# ---------------------------------------------------------------------------
# Step 6: add state
# ---------------------------------------------------------------------------


class TestStep6AddState:
    def test_add_scalar_state(self, project):
        r = _run(
            ["just-makeit", "add", "--state", "n_taps:int32_t:16"],
            cwd=project,
        )
        assert r.returncode == 0, f"add failed:\n{r.stderr}"
        r = _run(["make", "test"], cwd=project)
        assert r.returncode == 0, f"make test after add failed:\n{r.stdout}"


# ---------------------------------------------------------------------------
# Assembler: README.md must be up to date
# ---------------------------------------------------------------------------


def test_readme_up_to_date():
    r = _run(
        [PYTHON, "assemble.py", "--check"],
        cwd=Path(__file__).parent.parent / "examples" / "fir_filter",
    )
    assert r.returncode == 0, "README.md is stale — run: python3 assemble.py"
