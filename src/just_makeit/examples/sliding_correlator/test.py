"""End-to-end test: sliding_correlator scaffold → implement → build → perf.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/sliding_correlator/test.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
STEPS = HERE / ".steps"


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
    from just_makeit._perf import run as jm_perf

    # 1. Scaffold
    jm_new(
        "my_corr",
        root / "my_corr",
        object_names=["sliding_correlator"],
        state_vars=[
            ("ref", "float _Complex[16]", ""),
            ("delay", "float _Complex[16]", ""),
        ],
    )
    proj = root / "my_corr"

    # 2. Implement the correlator step
    _cmd([sys.executable, str(STEPS / "02_patch.py")], cwd=proj)

    # 3. CMake configure + build + CTest
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
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # 4. Upgrade to perf annotations + JM_DEFINE_STEPS kernel
    jm_perf(proj)
    _cmd([sys.executable, str(STEPS / "04_patch.py")], cwd=proj)
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # 5. Verify type stub
    pyi = (proj / "src" / "my_corr" / "sliding_correlator.pyi").read_text()
    assert "class SlidingCorrelator:" in pyi
    assert "def step(self, x: complex) -> complex:" in pyi
    assert "def steps(self, x: NDArray[np.complex64]" in pyi


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("sliding_correlator: PASSED")
