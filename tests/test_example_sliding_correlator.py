"""End-to-end tests for the sliding_correlator example.

Covers:
  - README currency
  - step() correctness (identity, two-tap, complex ref)
  - just-makeit perf upgrade + JM_DEFINE_STEPS (scalar path)
  - multi-block state continuity across steps() calls
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

_MAKE_ENV = {**os.environ, "PYTHON": Path(sys.executable).as_posix()}

import pytest

PYTHON = sys.executable
STEPS = (
    Path(__file__).parent.parent
    / "src"
    / "just_makeit"
    / "examples"
    / "sliding_correlator"
    / ".steps"
)


def _run(cmd, cwd=None, env=None):
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, env=env
    )


def _require(tool: str) -> None:
    import shutil

    if shutil.which(tool) is None:
        pytest.skip(f"{tool} not found")


# ── README currency ───────────────────────────────────────────────────────────


def test_readme_up_to_date():
    r = _run(
        [PYTHON, "assemble.py", "--check"],
        cwd=Path(__file__).parent.parent
        / "src"
        / "just_makeit"
        / "examples"
        / "sliding_correlator",
    )
    assert r.returncode == 0, "README.md is stale — run: python3 assemble.py"


# ── Integration helpers ───────────────────────────────────────────────────────


def _scaffold_plain(tmp_path_factory):
    """Scaffold my_corr, implement step(), build and install."""
    _require("cmake")
    _require("gcc")
    _require("just-makeit")

    root = tmp_path_factory.mktemp("corr_plain") / "my_corr"

    r = _run(
        [
            "just-makeit",
            "new",
            "my_corr",
            "--object",
            "sliding_correlator",
            "--state",
            "ref:float _Complex[16]",
            "--state",
            "delay:float _Complex[16]",
        ],
        cwd=root.parent,
    )
    assert r.returncode == 0, f"scaffold failed:\n{r.stderr}"

    r = _run([PYTHON, str(STEPS / "02_patch.py")], cwd=root)
    assert r.returncode == 0, f"step patch failed:\n{r.stderr}"

    r = _run(["make"], cwd=root, env=_MAKE_ENV)
    assert r.returncode == 0, f"make failed:\n{r.stderr}"

    return root


def _scaffold_perf(tmp_path_factory):
    """Scaffold my_corr, implement step(), upgrade via perf, apply JM_DEFINE_STEPS."""
    _require("cmake")
    _require("gcc")
    _require("just-makeit")

    root = tmp_path_factory.mktemp("corr_perf") / "my_corr"

    r = _run(
        [
            "just-makeit",
            "new",
            "my_corr",
            "--object",
            "sliding_correlator",
            "--state",
            "ref:float _Complex[16]",
            "--state",
            "delay:float _Complex[16]",
        ],
        cwd=root.parent,
    )
    assert r.returncode == 0, f"scaffold failed:\n{r.stderr}"

    r = _run([PYTHON, str(STEPS / "02_patch.py")], cwd=root)
    assert r.returncode == 0, f"step patch failed:\n{r.stderr}"

    r = _run(["just-makeit", "perf"], cwd=root)
    assert r.returncode == 0, f"perf upgrade failed:\n{r.stderr}"

    r = _run([PYTHON, str(STEPS / "04_patch.py")], cwd=root)
    assert r.returncode == 0, f"JM_DEFINE_STEPS patch failed:\n{r.stderr}"

    r = _run(["make"], cwd=root, env=_MAKE_ENV)
    assert r.returncode == 0, f"make failed:\n{r.stderr}"

    return root


# ── Python snippets ───────────────────────────────────────────────────────────

_IDENTITY_CHECK = textwrap.dedent("""\
    import numpy as np
    from my_corr import SlidingCorrelator

    c = SlidingCorrelator()
    ref = np.zeros(16, dtype=np.complex64)
    ref[0] = 1.0
    c.set_ref(ref)

    impulse = np.zeros(16, dtype=np.complex64)
    impulse[0] = 1.0
    y = c.steps(impulse)
    print(y[:4].tolist())
""")

_TWO_TAP_CHECK = textwrap.dedent("""\
    import numpy as np
    from my_corr import SlidingCorrelator

    c = SlidingCorrelator()
    ref = np.zeros(16, dtype=np.complex64)
    ref[0] = 1.0
    ref[1] = 1.0
    c.set_ref(ref)

    impulse = np.zeros(16, dtype=np.complex64)
    impulse[0] = 1.0
    y = c.steps(impulse)
    print(y[:4].real.tolist())
""")

_COMPLEX_REF_CHECK = textwrap.dedent("""\
    import numpy as np
    from my_corr import SlidingCorrelator

    c = SlidingCorrelator()
    ref = np.zeros(16, dtype=np.complex64)
    ref[0] = 1.0 + 1.0j   # conj(ref[0]) = 1 - 1j
    c.set_ref(ref)

    impulse = np.zeros(16, dtype=np.complex64)
    impulse[0] = 1.0
    y = c.steps(impulse)
    print([float(y[0].real), float(y[0].imag)])
""")

_CONTINUITY_CHECK = textwrap.dedent("""\
    import numpy as np
    from my_corr import SlidingCorrelator

    c = SlidingCorrelator()
    ref = np.zeros(16, dtype=np.complex64)
    ref[0] = 1.0
    ref[1] = 1.0
    c.set_ref(ref)

    y1 = c.steps(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.complex64))
    y2 = c.steps(np.zeros(4, dtype=np.complex64))
    print(y1.real.tolist())
    print(y2.real.tolist())
""")


def _approx(got, expected, atol=1e-5):
    assert len(got) == len(expected)
    for i, (g, e) in enumerate(zip(got, expected)):
        assert abs(g - e) < atol, f"[{i}]: got {g}, expected {e}"


# ── Plain scaffold tests ──────────────────────────────────────────────────────


class TestStepPlain:
    @pytest.fixture(scope="class")
    def installed(self, tmp_path_factory):
        root = _scaffold_plain(tmp_path_factory)
        r = _run(["uv", "pip", "install", "-e", "."], cwd=root)
        assert r.returncode == 0, f"pip install failed:\n{r.stderr}"
        return root

    def test_identity(self, installed):
        r = _run([PYTHON, "-c", _IDENTITY_CHECK], cwd=installed)
        assert r.returncode == 0, r.stderr
        vals = eval(r.stdout.strip())
        _approx([v.real for v in vals], [1.0, 0.0, 0.0, 0.0])
        _approx([v.imag for v in vals], [0.0, 0.0, 0.0, 0.0])

    def test_two_tap(self, installed):
        r = _run([PYTHON, "-c", _TWO_TAP_CHECK], cwd=installed)
        assert r.returncode == 0, r.stderr
        vals = eval(r.stdout.strip())
        _approx(vals, [1.0, 1.0, 0.0, 0.0])

    def test_complex_ref(self, installed):
        r = _run([PYTHON, "-c", _COMPLEX_REF_CHECK], cwd=installed)
        assert r.returncode == 0, r.stderr
        re_val, im_val = eval(r.stdout.strip())
        assert abs(re_val - 1.0) < 1e-5
        assert abs(im_val - (-1.0)) < 1e-5


# ── Perf + JM_DEFINE_STEPS tests ─────────────────────────────────────────────


class TestStepPerf:
    @pytest.fixture(scope="class")
    def installed(self, tmp_path_factory):
        root = _scaffold_perf(tmp_path_factory)
        r = _run(["uv", "pip", "install", "-e", "."], cwd=root)
        assert r.returncode == 0, f"pip install failed:\n{r.stderr}"
        return root

    def test_identity(self, installed):
        r = _run([PYTHON, "-c", _IDENTITY_CHECK], cwd=installed)
        assert r.returncode == 0, r.stderr
        vals = eval(r.stdout.strip())
        _approx([v.real for v in vals], [1.0, 0.0, 0.0, 0.0])

    def test_two_tap(self, installed):
        r = _run([PYTHON, "-c", _TWO_TAP_CHECK], cwd=installed)
        assert r.returncode == 0, r.stderr
        vals = eval(r.stdout.strip())
        _approx(vals, [1.0, 1.0, 0.0, 0.0])

    def test_complex_ref(self, installed):
        r = _run([PYTHON, "-c", _COMPLEX_REF_CHECK], cwd=installed)
        assert r.returncode == 0, r.stderr
        re_val, im_val = eval(r.stdout.strip())
        assert abs(re_val - 1.0) < 1e-5
        assert abs(im_val - (-1.0)) < 1e-5

    def test_continuity(self, installed):
        """State persists correctly across steps() calls."""
        r = _run([PYTHON, "-c", _CONTINUITY_CHECK], cwd=installed)
        assert r.returncode == 0, r.stderr
        y1, y2 = [eval(line) for line in r.stdout.strip().splitlines()]
        _approx(y1, [1.0, 1.0, 0.0, 0.0])
        _approx(y2, [0.0, 0.0, 0.0, 0.0])
