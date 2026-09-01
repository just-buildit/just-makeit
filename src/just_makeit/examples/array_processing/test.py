"""End-to-end test: array processing scaffold → method → variable-output.

Exercises all six array processing patterns described in the README:
  1. Auto-generated steps() on a stateful object
  2. just-makeit method (scalar stub, batch companion hand-written)
  3. just-makeit method --variable-output
  4. just-makeit method --variable-output --multi-output
  5. --arg-type type[] (array-buffer primary arg)
  6. just-makeit method --out-type (per-call typed output array)

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/array_processing/test.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from just_makeit._pyfmt import flatten_signatures

HERE = Path(__file__).parent
STEPS = HERE / ".steps"


def _cmd(args, cwd, **kw):
    r = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=600, **kw
    )
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\n"
            f"stderr:\n{r.stderr}"
        )
    return r


def run(root: Path) -> None:
    from just_makeit._apply import run as apply_run
    from just_makeit._method import run as jm_method
    from just_makeit._new import run as jm_new

    # ── Pattern 1 & 2: EMA object (steps() auto-generated; method for uint32) ──

    jm_new(
        "my_arrays",
        root / "my_arrays",
        object_names=["ema"],
        arg_type="float",
        return_type="float",
        state_vars=[
            ("alpha", "float", "0.1f"),
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

    # Implement quantize + enrich the sacred header with Doxygen, then let
    # `jm apply` re-derive the glue (.pyi included). The hand-written
    # @brief/@param/@return/@code comments on ema_create() and ema_quantize()
    # become a rich numpy-style class docstring and a runnable doctest that CI
    # executes against the built extension.
    _cmd([sys.executable, str(STEPS / "06_doxygen.py")], cwd=proj_ema)
    apply_run(proj_ema)

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
        cwd=proj_ema,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj_ema)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj_ema)

    # The Doxygen enrichment reached the stub: class summary from create()'s
    # @brief, method prose from @brief/@param/@return, and a @code block on
    # quantize() rendered as a runnable Examples doctest.
    ema_pyi_text = (proj_ema / "src" / "my_arrays" / "ema.pyi").read_text()
    assert "Exponential moving average filter" in ema_pyi_text, (
        "class @brief missing from ema.pyi"
    )
    assert (
        "Quantize one sample to an unsigned integer code." in ema_pyi_text
    ), "quantize @brief missing from ema.pyi"
    assert (
        ">>> e.quantize(3.4)" in ema_pyi_text
        and ">>> e.quantize(3.6)" in ema_pyi_text
    ), "quantize() @code doctest missing from ema.pyi"

    # The header-authored doctest actually runs against the built .so:
    # `pytest --doctest-glob='*.pyi'` imports the compiled extension and
    # executes every `>>>` in the enriched stub. If quantize ever drifts from
    # its documented example, CI fails here.
    doctest_res = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--doctest-glob=*.pyi",
            "-q",
            str(Path("src") / "my_arrays" / "ema.pyi"),
        ],
        cwd=proj_ema,
        env={**os.environ, "PYTHONPATH": str(proj_ema / "src")},
        capture_output=True,
        text=True,
    )
    assert doctest_res.returncode == 0, (
        "header-authored .pyi doctests failed:\n"
        f"{doctest_res.stdout}\n{doctest_res.stderr}"
    )

    # ── Patterns 3 & 4: hbdecim object with --variable-output ─────────────────

    jm_new(
        "my_decim",
        root / "my_decim",
        object_names=["hbdecim"],
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
            "cmake",
            "-B",
            "build",
            "-S",
            ".",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        cwd=proj_decim,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj_decim)
    _cmd(
        ["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj_decim
    )

    # ── Pattern 5: --arg-type type[] (array-buffer primary arg) ───────────────
    # Objects whose primary operation processes a whole buffer in one call —
    # no sample-by-sample loop, no auto-generated steps().

    jm_new(
        "my_buf",
        root / "my_buf",
        object_names=["buf_proc"],
        arg_type="float _Complex[]",
        return_type="int32_t",
        state_vars=[("count", "int32_t", "0")],
    )
    proj_buf = root / "my_buf"

    # step() takes a numpy array, returns int — no steps() generated
    core_h = (
        proj_buf / "native" / "inc" / "buf_proc" / "buf_proc_core.h"
    ).read_text()
    assert "const float _Complex *x, size_t x_len" in core_h, (
        "array arg not in step signature"
    )
    assert "buf_proc_steps" not in core_h, (
        "steps() must not be generated for array arg"
    )

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
        cwd=proj_buf,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj_buf)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj_buf)

    # Type stub: step takes NDArray, returns int; no steps() line
    # gh-744: signatures are wrapped to 79 cols when they do not fit,
    # so rejoin them before matching -- the assertion is about the
    # parameters, not where the line happens to break.
    pyi = flatten_signatures(
        (proj_buf / "src" / "my_buf" / "buf_proc.pyi").read_text()
    )
    assert "def step(self, x: NDArray[np.complex64]) -> int:" in pyi, (
        f"array-arg step stub missing or wrong:\n{pyi}"
    )
    assert "def steps" not in pyi, (
        "steps() stub must be absent for array-arg object"
    )

    # Also verify ema's pyi from pattern 1
    # gh-744: signatures are wrapped to 79 cols when they do not fit,
    # so rejoin them before matching -- the assertion is about the
    # parameters, not where the line happens to break.
    ema_pyi = flatten_signatures(
        (proj_ema / "src" / "my_arrays" / "ema.pyi").read_text()
    )
    assert "class Ema:" in ema_pyi
    assert "def step(self, x: float) -> float:" in ema_pyi
    assert "def steps(self, x: NDArray[np.float32]" in ema_pyi

    # ── Pattern 6: --out-type (per-call typed output array) ───────────────────
    # Method takes an array param; output array is a different type and length.

    jm_new(
        "my_conv",
        root / "my_conv",
        object_names=["ci8_conv"],
        state_vars=[("gain", "float", "1.0")],
    )
    proj_conv = root / "my_conv"

    from just_makeit._method import run as jm_method_conv

    jm_method_conv(
        root=proj_conv,
        object_name="ci8_conv",
        method_name="convert",
        module=None,
        arg_type="void",
        return_type="void",
        variable_output=False,
        multi_output=[],
        params=[("raw", "int8_t[]")],
        out_type="float _Complex",
        out_divisor=2,
    )

    # Verify ext has PyArray_EMPTY (per-call alloc) not pre-allocated buffer
    ext = (proj_conv / "native/src/ci8_conv/ci8_conv_ext.c").read_text()
    assert "PyArray_EMPTY" in ext, "out-type must use PyArray_EMPTY"
    assert "/ 2" in ext, "out-divisor 2 must appear in length expression"

    # Verify C stub has the *out parameter
    src = (proj_conv / "native/src/ci8_conv/ci8_conv_core.c").read_text()
    assert "float _Complex *out" in src, "*out param missing from stub"
    assert "const int8_t *raw" in src, "raw array param missing from stub"

    # my_conv was created only for structural verification; remove it so it
    # doesn't appear as an unbuilt project in the examples directory.
    import shutil

    shutil.rmtree(proj_conv, ignore_errors=True)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("array_processing: PASSED")
