"""End-to-end test: array processing scaffold → method → variable-output.

Exercises the five array processing patterns the README walks through, its
section-6 header documentation, and one shape it only links to:
  1. Auto-generated steps() on a stateful object
  2. just-makeit method (scalar stub) + its `--batch` companion
  3. just-makeit method --variable-output
  4. just-makeit method --variable-output --multi-output
  5. --arg-type type[] (array-buffer primary arg)
  6. just-makeit method --out-type (per-call typed output array)

The C bodies the README shows for patterns 2-4 are its .steps/*.c files;
they are spliced into the generated stubs and built, so a README kernel
that no longer fits jm's prototypes fails here.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/array_processing/test.py
"""

import os
import re
import subprocess
import sys
from just_makeit import _impl
from just_makeit import _incpath as INC
from pathlib import Path

from just_makeit._example import scratch_dir
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


def _params(c_text: str, fn: str) -> str:
    """*fn*'s parameters in *c_text*, comments dropped, spaces collapsed."""
    m = re.search(r"\b" + re.escape(fn) + r"\s*\(([^)]*)\)", c_text)
    assert m, f"{fn} not found"
    return " ".join(re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S).split())


def _implement_from_readme(proj: Path, comp: str, shown: Path, fns) -> None:
    """Fill the generated stubs for *fns* with the README's own C (*shown*).

    Bodies go in through ``--impl``'s primitives, so they compile against
    the prototypes jm generated; the parameter check refuses a README that
    shows a different signature -- the drift that left a one-argument
    ``execute_max_out(state)`` in the README after jm added ``n_in``.
    """
    header = (INC.header_root(proj) / comp / f"{comp}_core.h").read_text(
        encoding="utf-8"
    )
    core = proj / "native" / "src" / comp / f"{comp}_core.c"
    text = core.read_text(encoding="utf-8")
    shown_text = shown.read_text(encoding="utf-8")
    for fn in fns:
        assert _params(shown_text, fn) == _params(header, fn), (
            f"{shown.name}: {fn}() parameters differ from the generated"
            f" prototype:\n  README:    {_params(shown_text, fn)}"
            f"\n  generated: {_params(header, fn)}"
        )
        patched = _impl.patch_function_body(
            text, fn, _impl.extract_body(shown, fn)
        )
        assert patched != text, f"{fn}() stub not found in {core}"
        text = patched
    core.write_text(text, encoding="utf-8")


def _py(proj: Path, code: str) -> None:
    """Run *code* against *proj*'s built extension."""
    _cmd(
        [sys.executable, "-c", code],
        cwd=proj,
        env={**os.environ, "PYTHONPATH": str(proj / "src")},
    )


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
    # ...and its 1:1-rate batch companion, bound by jm (`--batch`) and
    # implemented with the README's loop.
    jm_method(
        root=proj_ema,
        object_name="ema",
        method_name="quantize_steps",
        module=None,
        arg_type="float",
        return_type="uint32_t",
        variable_output=False,
        multi_output=[],
        batch=True,
    )
    _implement_from_readme(
        proj_ema,
        "ema",
        STEPS / "02_method_scalar_batch.c",
        ["my_arrays_ema_quantize_steps"],
    )

    # Implement quantize + enrich the sacred header with Doxygen, then let
    # `jm apply` re-derive the glue (.pyi included). The hand-written
    # @brief/@param/@return/@code comments on my_arrays_ema_create() and my_arrays_ema_quantize()
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
    ema_pyi_text = (proj_ema / "src" / "my_arrays" / "ema.pyi").read_text(
        encoding="utf-8"
    )
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

    # Pattern 2's batch method, out= included, as the README describes it.
    _py(
        proj_ema,
        "import numpy as np\n"
        "from my_arrays import Ema\n"
        "f = Ema()\n"
        "x = np.array([-1.0, 0.4, 3.4, 3.6], dtype=np.float32)\n"
        "q = f.quantize_steps(x)\n"
        "assert q.dtype == np.uint32 and q.tolist() == [0, 0, 3, 4], q\n"
        "buf = np.empty(4, dtype=np.uint32)\n"
        "assert f.quantize_steps(x, buf) is buf\n"
        "assert buf.tolist() == [0, 0, 3, 4], buf\n",
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
    _implement_from_readme(
        proj_decim,
        "hbdecim",
        STEPS / "03_max_out.c",
        ["my_decim_hbdecim_execute_max_out", "my_decim_hbdecim_execute"],
    )
    _implement_from_readme(
        proj_decim,
        "hbdecim",
        STEPS / "04_execute_ovf.c",
        [
            "my_decim_hbdecim_execute_ovf_max_out",
            "my_decim_hbdecim_execute_ovf",
        ],
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
    # Patterns 3 and 4 as the README describes them: per call, NumPy-owned,
    # trimmed to the kernel's count, independent of every other result.
    _py(
        proj_decim,
        "import numpy as np\n"
        "from my_decim import Hbdecim\n"
        "d = Hbdecim()\n"
        "block = np.ones(1024, dtype=np.complex64)\n"
        "assert d.execute_max_out(1024) == 512\n"
        "a = d.execute(block)\n"
        "b = d.execute(block)\n"
        "assert a.shape == (512,) and a.flags.owndata, a.shape\n"
        "assert not np.shares_memory(a, b)\n"
        "samples, flags = d.execute_ovf(block)\n"
        "assert samples.shape == flags.shape == (512,)\n"
        "assert flags.dtype == np.uint8 and not flags.any(), flags\n",
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
        INC.header_root(proj_buf) / "buf_proc" / "buf_proc_core.h"
    ).read_text(encoding="utf-8")
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
        (proj_buf / "src" / "my_buf" / "buf_proc.pyi").read_text(
            encoding="utf-8"
        )
    )
    assert "def step(self, x: npt.NDArray[np.complex64]) -> int:" in pyi, (
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
        (proj_ema / "src" / "my_arrays" / "ema.pyi").read_text(
            encoding="utf-8"
        )
    )
    assert "class Ema:" in ema_pyi
    assert "def step(self, x: float) -> float:" in ema_pyi
    assert "def steps(self, x: npt.NDArray[np.float32]" in ema_pyi

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
    ext = (proj_conv / "native/src/ci8_conv/ci8_conv_ext.c").read_text(
        encoding="utf-8"
    )
    assert "PyArray_EMPTY" in ext, "out-type must use PyArray_EMPTY"
    assert "/ 2" in ext, "out-divisor 2 must appear in length expression"

    # Verify C stub has the *out parameter
    src = (proj_conv / "native/src/ci8_conv/ci8_conv_core.c").read_text(
        encoding="utf-8"
    )
    assert "float _Complex *out" in src, "*out param missing from stub"
    assert "const int8_t *raw" in src, "raw array param missing from stub"

    # my_conv was created only for structural verification; remove it so it
    # doesn't appear as an unbuilt project in the examples directory.
    import shutil

    shutil.rmtree(proj_conv, ignore_errors=True)


if __name__ == "__main__":
    with scratch_dir() as tmp:
        run(Path(tmp))
    print("array_processing: PASSED")
