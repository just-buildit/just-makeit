"""End-to-end test for the dsp_toolkit example.

Called by tests/test_examples.py as: run(root: Path) -> None
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
STEPS = HERE / ".steps"


def _cmd(args, cwd):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        )


def run(root: Path) -> None:
    from just_makeit._new import run as new_run
    from just_makeit._init import run as init_run

    dest = root / "dsp_toolkit"

    # Step 1: scaffold with gain component
    new_run(
        "dsp_toolkit",
        dest,
        object_name="gain",
        state_vars=[("gain", "float", "1.0")],
        arg_type="float",
        return_type="float",
    )

    # Step 2: patch gain_step
    _cmd([sys.executable, str(STEPS / "02_patch.py")], cwd=dest)

    # Step 3: add ema component — __init__.py is spliced automatically
    init_run(
        dest,
        "ema",
        state_vars=[("alpha", "double", "0.1"), ("prev", "float", "0.0")],
        arg_type="float",
        return_type="float",
        _hint=False,
    )

    # Verify both are exported without any manual edit
    init_py = (dest / "src" / "dsp_toolkit" / "__init__.py").read_text()
    assert "from .gain import Gain" in init_py
    assert "from .ema import Ema" in init_py
    assert '"Ema"' in init_py

    # Step 4: patch ema_step (drops const, adds body)
    _cmd([sys.executable, str(STEPS / "04_patch.py")], cwd=dest)

    # Build
    _cmd(["make"], cwd=dest)

    # C tests
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=dest)

    # Python tests
    _cmd([sys.executable, "-m", "pytest", "src/", "-v",
          "--ignore=src/dsp_toolkit/benchmarks"], cwd=dest)

    # Smoke test: both classes work end-to-end
    _cmd(
        [sys.executable, "-c", """
import sys
sys.path.insert(0, 'src')
from dsp_toolkit import Gain, Ema

g = Gain(gain=2.0)
assert abs(g.step(1.0) - 2.0) < 1e-6

e = Ema(alpha=0.5, prev=0.0)
y = e.step(1.0)
assert abs(y - 0.5) < 1e-6, f"expected 0.5, got {y}"
y = e.step(1.0)
assert abs(y - 0.75) < 1e-6, f"expected 0.75, got {y}"

print("ok")
"""],
        cwd=dest,
    )

    # Verify type stubs for both components
    gain_pyi = (dest / "src" / "dsp_toolkit" / "gain.pyi").read_text()
    assert "class Gain:" in gain_pyi
    assert "gain" in gain_pyi
    assert "def step(self, x: float) -> float:" in gain_pyi

    ema_pyi = (dest / "src" / "dsp_toolkit" / "ema.pyi").read_text()
    assert "class Ema:" in ema_pyi
    assert "def step(self, x: float) -> float:" in ema_pyi
    assert "def steps(self, x: NDArray[np.float32]" in ema_pyi


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("dsp_toolkit: PASSED")
