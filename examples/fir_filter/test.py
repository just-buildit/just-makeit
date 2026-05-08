"""End-to-end test: fir_filter scaffold → implement → build → add state → perf.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/fir_filter/test.py
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
    from just_makeit._add import run as jm_add
    from just_makeit._perf import run as jm_perf

    # 1. Scaffold
    jm_new(
        "my_fir",
        root / "my_fir",
        component="fir_filter",
        state_vars=[
            ("coeffs", "float[16]", ""),
            ("delay", "float _Complex[16]", ""),
            ("gain", "float", "1.0"),
        ],
    )
    proj = root / "my_fir"

    # 2. Implement the FIR step
    _cmd([sys.executable, str(STEPS / "02_patch.py")], cwd=proj)

    # 3. CMake configure + build + CTest
    _cmd(
        [
            "cmake", "-B", "build", "-S", ".",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        cwd=proj,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # 4. Add n_taps scalar state, rebuild, retest
    jm_add(proj, "fir_filter", [("n_taps", "int32_t", "16")])
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # 5. Upgrade to perf annotations + scratch-buffer kernel
    jm_perf(proj)
    _cmd([sys.executable, str(STEPS / "07_patch.py")], cwd=proj)
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("fir_filter: PASSED")
