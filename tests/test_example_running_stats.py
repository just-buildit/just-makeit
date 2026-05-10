"""Integration test: run each step of examples/running_stats/README.md in sequence."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

STEPS = Path(__file__).parent.parent / "examples" / "running_stats" / ".steps"
PYTHON = sys.executable


def _run(cmd: list[str], cwd: Path, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, **kw)


def _require(name: str) -> None:
    if not shutil.which(name):
        pytest.skip(f"{name} not found")


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    """Scaffold → implement → build.  Shared by all steps."""
    _require("cmake")
    _require("gcc")
    _require("just-makeit")

    root = tmp_path_factory.mktemp("stats") / "my_stats"

    r = _run(
        [
            "just-makeit",
            "new",
            "my_stats",
            "--object",
            "running_stats",
            "--state",
            "n:int32_t:0",
            "--state",
            "mean:double:0.0",
            "--state",
            "m2:double:0.0",
        ],
        cwd=root.parent,
    )
    assert r.returncode == 0, f"scaffold failed:\n{r.stderr}"

    r = _run([PYTHON, str(STEPS / "02_patch.py")], cwd=root)
    assert r.returncode == 0, f"patch failed:\n{r.stderr}"

    r = _run(["make"], cwd=root)
    assert r.returncode == 0, f"make failed:\n{r.stderr}"

    return root


class TestStep3Build:
    def test_make_test_passes(self, project):
        r = _run(["make", "test"], cwd=project)
        assert r.returncode == 0, f"make test failed:\n{r.stdout}\n{r.stderr}"
        assert "passed" in r.stdout


class TestStep4Python:
    @pytest.fixture(scope="class")
    def installed(self, project):
        r = _run(["uv", "pip", "install", "-e", "."], cwd=project)
        assert r.returncode == 0, f"pip install failed:\n{r.stderr}"
        return project

    def test_n(self, installed):
        r = _run([PYTHON, str(STEPS / "04_demo.py")], cwd=installed)
        assert r.returncode == 0, f"demo.py failed:\n{r.stderr}"
        assert "n:        8" in r.stdout

    def test_mean(self, installed):
        r = _run([PYTHON, str(STEPS / "04_demo.py")], cwd=installed)
        assert "mean:     5.0000" in r.stdout

    def test_variance(self, installed):
        r = _run([PYTHON, str(STEPS / "04_demo.py")], cwd=installed)
        assert "variance: 4.0000" in r.stdout

    def test_steps_block(self, installed):
        r = _run([PYTHON, str(STEPS / "04_demo.py")], cwd=installed)
        assert "final mean from steps(): 5.0000" in r.stdout
        assert "final var  from steps(): 4.0000" in r.stdout


class TestStep5C:
    @pytest.fixture(scope="class")
    def c_output(self, project):
        shutil.copy(STEPS / "05_demo.c", project / "demo.c")
        build_dir = str(project / "build")
        r = _run(
            [
                "gcc",
                "-O2",
                "-std=c99",
                "-Inative/inc",
                "demo.c",
                f"-L{build_dir}",
                "-lmy_stats",
                f"-Wl,-rpath,{build_dir}",
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

    def test_n(self, c_output):
        assert "n:        8" in c_output

    def test_mean(self, c_output):
        assert "mean:     5.0000" in c_output

    def test_variance(self, c_output):
        assert "variance: 4.0000" in c_output

    def test_reset(self, c_output):
        assert "after reset: n=0 mean=0.0" in c_output


class TestStep6AddState:
    def test_add_min_max(self, project):
        r = _run(
            [
                "just-makeit",
                "add",
                "--state",
                "min_val:double:0.0",
                "--state",
                "max_val:double:0.0",
            ],
            cwd=project,
        )
        assert r.returncode == 0, f"add failed:\n{r.stderr}"
        r = _run(["make", "test"], cwd=project)
        assert r.returncode == 0, f"make test after add failed:\n{r.stdout}"


def test_readme_up_to_date():
    r = subprocess.run(
        [PYTHON, "assemble.py", "--check"],
        cwd=Path(__file__).parent.parent / "examples" / "running_stats",
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, "README.md is stale — run: python3 assemble.py"
