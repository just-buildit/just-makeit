"""End-to-end test for the sliding_power example.

Called by tests/test_examples.py as: run(root: Path) -> None
"""
from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path


def run(root: Path) -> None:
    from just_makeit._new import run as new_run
    from just_makeit._init import run as init_run  # noqa: F401

    dest = root / "my_power"

    new_run(
        "my_power",
        dest,
        object_name="power_est",
        state_vars=[
            ("delay", "float[64]", ""),
            ("sum_sq", "double", "0.0"),
            ("pos", "int", "0"),
        ],
        perf=True,
        arg_type="float _Complex",
        return_type="float",
    )

    assert (dest / "native" / "inc" / "jm_simd.h").exists(), "jm_simd.h missing"
    assert (dest / "native" / "inc" / "jm_perf.h").exists(), "jm_perf.h missing"

    # Patch step(): replace stub (const + placeholder body) with real implementation.
    # The stub is: JM_FORCEINLINE JM_HOT float\npower_est_step(const ...) { ... }
    import re as _re
    header = dest / "native" / "inc" / "power_est" / "power_est_core.h"
    text = header.read_text()
    stub_re = _re.compile(
        r"JM_FORCEINLINE JM_HOT float\s*\n"
        r"power_est_step\(const power_est_state_t \*state.*?\n\}",
        _re.DOTALL,
    )
    assert stub_re.search(text), "step stub not found in header"
    impl = (
        "JM_FORCEINLINE JM_HOT float\n"
        "power_est_step(power_est_state_t *state, float complex x)\n"
        "{\n"
        "    float re = crealf(x), im = cimagf(x);\n"
        "    float mag_sq = re * re + im * im;\n"
        "    state->sum_sq += (double)(mag_sq - state->delay[state->pos]);\n"
        "    state->delay[state->pos] = mag_sq;\n"
        "    state->pos = (state->pos + 1) & 63;\n"
        "    return (float)(state->sum_sq * (1.0 / 64.0));\n"
        "}"
    )
    header.write_text(stub_re.sub(impl, text))

    # Build
    r = subprocess.run(["make"], cwd=dest, capture_output=True, text=True)
    assert r.returncode == 0, f"make failed:\n{r.stderr}"

    # C tests
    r = subprocess.run(["make", "test"], cwd=dest, capture_output=True, text=True)
    assert r.returncode == 0, f"make test failed:\n{r.stdout}\n{r.stderr}"

    # Python smoke test — step() now returns float, not complex
    r = subprocess.run(
        [sys.executable, "-c", """
import math, sys
sys.path.insert(0, 'src')
from my_power import PowerEst
est = PowerEst()
for n in range(128):
    y = est.step(math.sin(2 * math.pi * n / 16) + 0j)
assert 0.48 < y < 0.52, f"sine power out of range: {y}"
for _ in range(64):
    y = est.step(0j)
assert y < 0.01, f"silence power should be ~0: {y}"
print("ok")
"""],
        cwd=dest,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, f"Python smoke test failed:\n{r.stdout}\n{r.stderr}"
    assert "ok" in r.stdout
