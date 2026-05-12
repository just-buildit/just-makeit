"""Integration test: run each step of examples/fir_filter/README.md in sequence.

Each step mirrors what a user following the README would do.  If any command
fails, or expected output is not produced, the test fails — keeping the README
honest.
"""

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

STEPS = Path(__file__).parent.parent / "src" / "just_makeit" / "examples" / "fir_filter" / ".steps"
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
            "--object",
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
        build_dir = str(project / "build")
        gcc_cmd = [
            "gcc", "-O2", "-std=c99", "-Inative/inc", "demo.c",
            f"-L{build_dir}", "-lmy_fir",
        ]
        if sys.platform != "win32":
            gcc_cmd += [f"-Wl,-rpath,{build_dir}"]
        gcc_cmd += ["-lm", "-o", "demo"]
        r = _run(gcc_cmd, cwd=project)
        assert r.returncode == 0, f"gcc failed:\n{r.stderr}"
        if sys.platform == "win32":
            exe = str(project / "demo.exe")
            env = {**os.environ, "PATH": f"{build_dir};{os.environ.get('PATH', '')}"}
        else:
            exe = "./demo"
            env = None
        r = _run([exe], cwd=project, env=env)
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
        cwd=Path(__file__).parent.parent / "src" / "just_makeit" / "examples" / "fir_filter",
    )
    assert r.returncode == 0, "README.md is stale — run: python3 assemble.py"


# ---------------------------------------------------------------------------
# Step 7: `just-makeit perf` upgrade + scratch-buffer kernel (scalar path)
# ---------------------------------------------------------------------------

_IMPULSE_CHECK = textwrap.dedent("""\
    import numpy as np
    from my_fir import FirFilter

    f = FirFilter(gain=1.0)
    h = np.array([0.25, 0.5, 0.25] + [0.0] * 13, dtype=np.float32)
    f.set_coeffs(h)

    impulse = np.zeros(16, dtype=np.complex64)
    impulse[0] = 1.0
    y = f.steps(impulse)
    print(y[:4].real.tolist())
""")

_TAIL_CHECK = textwrap.dedent("""\
    import numpy as np
    from my_fir import FirFilter

    f = FirFilter(gain=1.0)
    h = np.array([0.25, 0.5, 0.25] + [0.0] * 13, dtype=np.float32)
    f.set_coeffs(h)

    # 19 samples: AVX-512 handles first 16, step() handles last 3
    x = np.zeros(19, dtype=np.complex64)
    x[0] = 1.0
    y = f.steps(x)
    print(y[:5].real.tolist())
""")

_CONTINUITY_CHECK = textwrap.dedent("""\
    import numpy as np
    from my_fir import FirFilter

    f = FirFilter(gain=1.0)
    h = np.array([0.25, 0.5, 0.25] + [0.0] * 13, dtype=np.float32)
    f.set_coeffs(h)

    # Split the impulse across two steps() calls so the tail crosses the boundary
    y1 = f.steps(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.complex64))
    y2 = f.steps(np.zeros(4, dtype=np.complex64))
    print(y1.real.tolist())
    print(y2.real.tolist())
""")


def _approx_list(got, expected, atol=1e-5):
    assert len(got) == len(expected), f"length mismatch: {got} vs {expected}"
    for i, (g, e) in enumerate(zip(got, expected)):
        assert abs(g - e) < atol, f"index {i}: got {g}, expected {e}"


def _scaffold_perf(tmp_path_factory):
    """Scaffold plain my_fir, implement step(), upgrade via 'just-makeit perf', apply step-7 patch."""
    _require("cmake")
    _require("gcc")
    _require("just-makeit")

    root = tmp_path_factory.mktemp("fir_perf") / "my_fir"

    r = _run(
        [
            "just-makeit", "new", "my_fir",
            "--object", "fir_filter",
            "--state", "coeffs:float[16]",
            "--state", "delay:float _Complex[16]",
            "--state", "gain:float:1.0",
        ],
        cwd=root.parent,
    )
    assert r.returncode == 0, f"scaffold failed:\n{r.stderr}"

    r = _run([PYTHON, str(STEPS / "02_patch.py")], cwd=root)
    assert r.returncode == 0, f"step-2 patch failed:\n{r.stderr}"

    r = _run(["just-makeit", "perf"], cwd=root)
    assert r.returncode == 0, f"perf upgrade failed:\n{r.stderr}"

    r = _run([PYTHON, str(STEPS / "07_patch.py")], cwd=root)
    assert r.returncode == 0, f"step-7 patch failed:\n{r.stderr}"

    r = _run(["make"], cwd=root)
    assert r.returncode == 0, f"make failed:\n{r.stderr}"

    return root


def _has_avx512() -> bool:
    cpuinfo = Path("/proc/cpuinfo")
    return cpuinfo.exists() and "avx512f" in cpuinfo.read_text(encoding="utf-8")


class TestStep7PerfScalar:
    """Scalar-path correctness — always runs, no AVX-512 required."""

    @pytest.fixture(scope="class")
    def installed(self, tmp_path_factory):
        root = _scaffold_perf(tmp_path_factory)
        r = _run(["uv", "pip", "install", "-e", "."], cwd=root)
        assert r.returncode == 0, f"pip install failed:\n{r.stderr}"
        return root

    def test_impulse_response(self, installed):
        r = _run([PYTHON, "-c", _IMPULSE_CHECK], cwd=installed)
        assert r.returncode == 0, r.stderr
        vals = eval(r.stdout.strip())
        _approx_list(vals, [0.25, 0.5, 0.25, 0.0])

    def test_multi_block_continuity(self, installed):
        """Impulse tail crosses the boundary between two steps() calls."""
        r = _run([PYTHON, "-c", _CONTINUITY_CHECK], cwd=installed)
        assert r.returncode == 0, r.stderr
        lines = r.stdout.strip().splitlines()
        y1 = eval(lines[0])
        y2 = eval(lines[1])
        _approx_list(y1, [0.25, 0.5, 0.25, 0.0])
        _approx_list(y2, [0.0, 0.0, 0.0, 0.0])

    def test_avx_to_step_handoff(self, installed):
        """19 samples: first 16 via AVX-512 scratch, last 3 via step() tail."""
        r = _run([PYTHON, "-c", _TAIL_CHECK], cwd=installed)
        assert r.returncode == 0, r.stderr
        vals = eval(r.stdout.strip())
        _approx_list(vals, [0.25, 0.5, 0.25, 0.0, 0.0])


class TestStep7PerfSIMD:
    """AVX-512 path — skipped when the CPU or flags are unavailable."""

    @pytest.fixture(scope="class")
    def installed_simd(self, tmp_path_factory):
        if not _has_avx512():
            pytest.skip("AVX-512 not available on this host")

        root = _scaffold_perf(tmp_path_factory)

        r = _run(
            [
                "cmake", "-B", "build", "-S", ".",
                "-DCMAKE_BUILD_TYPE=Release",
                "-DENABLE_SIMD=ON",
                f"-DPython3_EXECUTABLE={PYTHON}",
            ],
            cwd=root,
        )
        assert r.returncode == 0, f"cmake failed:\n{r.stderr}"

        r = _run(["cmake", "--build", "build", "--parallel"], cwd=root)
        assert r.returncode == 0, f"simd build failed:\n{r.stderr}"

        r = _run(["uv", "pip", "install", "-e", ".", "--force-reinstall"], cwd=root)
        assert r.returncode == 0, f"pip install failed:\n{r.stderr}"

        return root

    def test_simd_impulse_response(self, installed_simd):
        r = _run([PYTHON, "-c", _IMPULSE_CHECK], cwd=installed_simd)
        assert r.returncode == 0, r.stderr
        vals = eval(r.stdout.strip())
        _approx_list(vals, [0.25, 0.5, 0.25, 0.0])

    def test_simd_avx_to_step_handoff(self, installed_simd):
        """19 samples: first 16 via AVX-512 scratch, last 3 via step() tail."""
        r = _run([PYTHON, "-c", _TAIL_CHECK], cwd=installed_simd)
        assert r.returncode == 0, r.stderr
        vals = eval(r.stdout.strip())
        _approx_list(vals, [0.25, 0.5, 0.25, 0.0, 0.0])

    def test_simd_multi_block_continuity(self, installed_simd):
        r = _run([PYTHON, "-c", _CONTINUITY_CHECK], cwd=installed_simd)
        assert r.returncode == 0, r.stderr
        lines = r.stdout.strip().splitlines()
        y1 = eval(lines[0])
        y2 = eval(lines[1])
        _approx_list(y1, [0.25, 0.5, 0.25, 0.0])
        _approx_list(y2, [0.0, 0.0, 0.0, 0.0])
