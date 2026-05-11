"""End-to-end test: array processing scaffold → method → variable-output.

Exercises all four array processing patterns described in the README:
  1. Auto-generated steps() on a stateful object
  2. just-makeit method (scalar stub, batch companion hand-written)
  3. just-makeit method --variable-output
  4. just-makeit method --variable-output --multi-output

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/array_processing/test.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent


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

    # ── Pattern 1 & 2: EMA object (steps() auto-generated; method for uint32) ──

    jm_new(
        "my_arrays",
        root / "my_arrays",
        object_name="ema",
        arg_type="float",
        return_type="float",
        state_vars=[
            ("alpha", "double", "0.1"),
            ("prev", "float", "0.0"),
        ],
    )
    proj_ema = root / "my_arrays"

    # Pattern 2: scalar method with different return type
    jm_method(
        root=proj_ema,
        object_name="ema",
        method_name="quantize",
        module=None,
        arg_type="float",
        return_type="uint32_t",
        variable_output=False,
        multi_output=[],
    )

    _cmd(
        [
            "cmake", "-B", "build", "-S", ".",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        cwd=proj_ema,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj_ema)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj_ema)

    # ── Patterns 3 & 4: hbdecim object with --variable-output ─────────────────

    jm_new(
        "my_decim",
        root / "my_decim",
        object_name="hbdecim",
        arg_type="float _Complex",
        return_type="float _Complex",
        state_vars=[
            ("delay", "float _Complex[12]", ""),
        ],
    )
    proj_decim = root / "my_decim"

    # Pattern 3: --variable-output single stream
    jm_method(
        root=proj_decim,
        object_name="hbdecim",
        method_name="execute",
        module=None,
        arg_type="float _Complex",
        return_type="float _Complex",
        variable_output=True,
        multi_output=[],
    )

    # Pattern 4: --variable-output with secondary uint8_t stream
    jm_method(
        root=proj_decim,
        object_name="hbdecim",
        method_name="execute_ovf",
        module=None,
        arg_type="float _Complex",
        return_type="float _Complex",
        variable_output=True,
        multi_output=["uint8_t"],
    )

    _cmd(
        [
            "cmake", "-B", "build", "-S", ".",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        cwd=proj_decim,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj_decim)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj_decim)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("array_processing: PASSED")
