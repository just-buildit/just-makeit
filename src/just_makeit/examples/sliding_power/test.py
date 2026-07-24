"""End-to-end test for the sliding_power example.

Called by tests/test_examples.py as: run(root: Path) -> None
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _make_env():
    """Return env with PYTHON pinned to sys.executable (POSIX path for cmake)."""
    return {**os.environ, "PYTHON": Path(sys.executable).as_posix()}


def run(root: Path) -> None:
    from just_makeit._new import run as new_run
    from just_makeit._init import run as init_run  # noqa: F401

    dest = root / "my_power"

    new_run(
        "my_power",
        dest,
        object_names=["power_est"],
        state_vars=[
            ("delay", "float[64]", ""),
            ("sum_sq", "double", "0.0"),
            ("pos", "uint32_t", "0"),
        ],
        perf=True,
        arg_type="float _Complex",
        return_type="float",
    )

    assert (dest / "native" / "inc" / "jm_simd.h").exists(), (
        "jm_simd.h missing"
    )
    assert (dest / "native" / "inc" / "jm_perf.h").exists(), (
        "jm_perf.h missing"
    )

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

    # Enrich the sacred header: replace jm's trivial scaffold @brief on
    # power_est_create() with a real one-sentence summary. The header is the
    # single source of truth for docs — `jm apply` re-derives the .pyi from it,
    # turning create()'s @brief into the class-level docstring summary (instead
    # of the generic "PowerEst component." fallback). Run after the step patch
    # so both edits land on the finished header.
    from just_makeit._apply import run as apply_run

    text = header.read_text()
    scaffold_re = _re.compile(
        r"/\*\*\n \* @brief Create a power_est instance\..*?"
        r"(?=power_est_state_t \*power_est_create)",
        _re.DOTALL,
    )
    create_brief = (
        "Create a sliding-window signal-power estimator over a "
        "64-sample window, zeroed."
    )
    new_create = f"/**\n * @brief {create_brief}\n */\n"
    text, n = scaffold_re.subn(new_create, text, count=1)
    assert n == 1, "power_est_create scaffold brief not found"
    header.write_text(text)
    apply_run(dest)

    env = _make_env()

    # Build
    r = subprocess.run(
        ["make"],
        cwd=dest,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert r.returncode == 0, f"make failed:\n{r.stderr}"

    # C tests
    r = subprocess.run(
        ["make", "test"],
        cwd=dest,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert r.returncode == 0, f"make test failed:\n{r.stdout}\n{r.stderr}"

    # Python smoke test — step() now returns float, not complex
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            """
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
""",
        ],
        cwd=dest,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert r.returncode == 0, (
        f"Python smoke test failed:\n{r.stdout}\n{r.stderr}"
    )
    assert "ok" in r.stdout

    # Verify type stub: complex arg -> float return, steps() present
    pyi = (dest / "src" / "my_power" / "power_est.pyi").read_text()
    assert "class PowerEst:" in pyi
    assert "def step(self, x: complex) -> float:" in pyi
    assert "def steps(self, x: NDArray[np.complex64]" in pyi

    # The header-authored class summary (create()'s @brief) reached the stub,
    # replacing the generic "PowerEst component." fallback.
    assert "Create a sliding-window signal-power estimator" in pyi, (
        "class @brief summary missing from .pyi"
    )


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("sliding_power: PASSED")
