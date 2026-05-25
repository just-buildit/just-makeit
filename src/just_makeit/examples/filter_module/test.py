"""End-to-end test for the filter_module example.

Called by tests/test_examples.py as: run(root: Path) -> None

Exercises:
  - just-makeit module
  - just-makeit object (twice, different arg/return types)
  - module _ext.c regenerated correctly after each object
  - C tests pass for both objects (CHECK macro, not assert)
  - Python: both types importable from the same subpackage
  - Python: basic correctness of Fir (impulse response) and Biquad (passband/stopband)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).parent
STEPS = HERE / ".steps"


def _make_env():
    return {**os.environ, "PYTHON": Path(sys.executable).as_posix()}


def _cmd(args, cwd, **kw):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        )


def run(root: Path) -> None:
    from just_makeit._module import run as module_run
    from just_makeit._new import run as new_run
    from just_makeit._object import run as object_run

    dest = root / "my_filters"

    # ── 1. Scaffold project (no component) ───────────────────────────────────
    new_run("my_filters", dest)

    # ── 2. Create filter module ───────────────────────────────────────────────
    module_run(dest, "filter")

    toml = (dest / "just-makeit.toml").read_text()
    assert "[module.filter]" in toml, "module entry missing from toml"
    assert (dest / "src" / "my_filters" / "filter" / "__init__.py").exists()

    # ── 3a. Add Fir object ────────────────────────────────────────────────────
    object_run(
        dest,
        "fir",
        module="filter",
        state_vars=[
            ("coeffs", "float[16]", ""),
            ("delay", "float _Complex[16]", ""),
            ("gain", "float", "1.0"),
        ],
    )

    frag_fir = (dest / "native" / "src" / "filter" / "filter_ext_fir.c").read_text()
    agg = (dest / "native" / "src" / "filter" / "filter_ext.c").read_text()
    assert "FirObject" in frag_fir
    assert "PyInit_filter" in agg
    assert "BiquadObject" not in frag_fir  # not added yet

    init_py = (dest / "src" / "my_filters" / "filter" / "__init__.py").read_text()
    assert "Fir" in init_py
    assert "Biquad" not in init_py

    # ── 3b. Add Biquad object (real float, different arg/return type) ─────────
    object_run(
        dest,
        "biquad",
        module="filter",
        state_vars=[
            ("b0", "float", "1.0f"),
            ("b1", "float", "0.0f"),
            ("b2", "float", "0.0f"),
            ("a1", "float", "0.0f"),
            ("a2", "float", "0.0f"),
            ("w1", "float", "0.0f"),
            ("w2", "float", "0.0f"),
        ],
        arg_type="float",
        return_type="float",
    )

    frag_fir = (dest / "native" / "src" / "filter" / "filter_ext_fir.c").read_text()
    frag_biquad = (
        dest / "native" / "src" / "filter" / "filter_ext_biquad.c"
    ).read_text()
    agg = (dest / "native" / "src" / "filter" / "filter_ext.c").read_text()
    assert "FirObject" in frag_fir
    assert "BiquadObject" in frag_biquad
    assert "PyInit_filter" in agg

    init_py = (dest / "src" / "my_filters" / "filter" / "__init__.py").read_text()
    assert "Fir" in init_py
    assert "Biquad" in init_py

    cmake_txt = (dest / "native" / "src" / "filter" / "CMakeLists.txt").read_text()
    assert "fir_core" in cmake_txt
    assert "biquad_core" in cmake_txt

    toml = (dest / "just-makeit.toml").read_text()
    assert '"fir"' in toml
    assert '"biquad"' in toml

    # ── 4. Patch step stubs ───────────────────────────────────────────────────
    _cmd([sys.executable, str(STEPS / "04_patch_fir.py")], cwd=dest)
    _cmd([sys.executable, str(STEPS / "04_patch_biquad.py")], cwd=dest)

    # ── 5. Build ──────────────────────────────────────────────────────────────
    _cmd(["make"], cwd=dest, env=_make_env())

    # ── 6. C tests ────────────────────────────────────────────────────────────
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=dest)

    # ── 7. Python smoke tests ─────────────────────────────────────────────────
    # Fir: impulse response of a 3-tap box filter
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys; sys.path.insert(0, 'src')
import numpy as np
from my_filters.filter import Fir, Biquad

# --- Fir: 3-tap box filter ---
fir = Fir(gain=1.0)
h = np.zeros(16, dtype=np.float32)
h[0], h[1], h[2] = 1/3, 1/3, 1/3
fir.set_coeffs(h)

imp = np.zeros(16, dtype=np.complex64); imp[0] = 1.0
ir  = fir.steps(imp)
assert abs(ir[0].real - 1/3) < 1e-5, f"ir[0]={ir[0].real}"
assert abs(ir[1].real - 1/3) < 1e-5, f"ir[1]={ir[1].real}"
assert abs(ir[2].real - 1/3) < 1e-5, f"ir[2]={ir[2].real}"
assert abs(ir[3].real)       < 1e-5, f"ir[3]={ir[3].real}"

# --- Biquad: passthrough (b0=1, all others 0) ---
bq = Biquad(b0=1.0)
x  = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
y  = bq.steps(x)
assert all(abs(float(y[i]) - float(x[i])) < 1e-5 for i in range(4)), f"passthrough failed: {y}"

# --- Biquad: low-pass spectral test ---
import math
fc, Q = 0.1, 0.707
w0    = 2 * math.pi * fc
alpha = math.sin(w0) / (2 * Q)
c     = math.cos(w0)
a0    = 1 + alpha
bq2 = Biquad(
    b0=(1 - c) / 2 / a0,
    b1=(1 - c)     / a0,
    b2=(1 - c) / 2 / a0,
    a1=-2 * c      / a0,
    a2=(1 - alpha) / a0,
)
n  = np.arange(512, dtype=np.float32)
lo = np.cos(2 * math.pi * 0.05 * n)
hi = np.cos(2 * math.pi * 0.40 * n)
p_lo = float(np.mean(bq2.steps(lo)**2))
bq3 = Biquad(
    b0=(1 - c) / 2 / a0,
    b1=(1 - c)     / a0,
    b2=(1 - c) / 2 / a0,
    a1=-2 * c      / a0,
    a2=(1 - alpha) / a0,
)
p_hi = float(np.mean(bq3.steps(hi)**2))
assert p_lo > 0.3,      f"passband too low: {p_lo}"
assert p_hi < 0.01,     f"stopband too high: {p_hi}"

print("filter_module: all checks passed")
""",
        ],
        cwd=dest,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Python smoke test failed:\n{result.stdout}\n{result.stderr}"
        )
    print(result.stdout.strip())

    # Verify module-level type stub (named filter.pyi, not __init__.pyi)
    pyi = (dest / "src" / "my_filters" / "filter" / "filter.pyi").read_text()
    assert pyi.startswith("# filter/filter.pyi")
    assert "class Fir:" in pyi
    assert "class Biquad:" in pyi
    assert "import numpy as np" in pyi
    assert "def steps(self, x: NDArray" in pyi


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("filter_module: PASSED")
