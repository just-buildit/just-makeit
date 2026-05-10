"""End-to-end test: running_stats scaffold → implement → build → add state.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/running_stats/test.py
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

    # 1. Scaffold
    jm_new(
        "my_stats",
        root / "my_stats",
        object_name="running_stats",
        state_vars=[
            ("n", "int32_t", "0"),
            ("mean", "double", "0.0"),
            ("m2", "double", "0.0"),
        ],
    )
    proj = root / "my_stats"

    # 2. Implement the Welford step
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

    # 4. Add min/max state variables, rebuild, retest
    jm_add(proj, "running_stats", [
        ("min_val", "double", "0.0"),
        ("max_val", "double", "0.0"),
    ])
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("running_stats: PASSED")
