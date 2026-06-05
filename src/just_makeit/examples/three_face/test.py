"""End-to-end test: one C core exposed as THREE faces — fully generated.

Proves the combined-target pattern just-makeit is built for — a single C core
(`gain_core.c`, compiled once as an OBJECT library) drives:

  1. a standalone **C binary CLI**      (build/gaintool)
  2. a **Python CLI** / console entry   (python -m gaintool.cli)
  3. a **Python module API**            (from gaintool import Gain)

`gaintool` scales a stream of float32 samples by --gain. All three faces call
the same `gain_step()` and must produce byte-identical output.

Unlike the original version of this example, NOTHING is hand-written: `jm app`
now *generates* the argv parser and the read→step()→write loop for all three
targets from the object model. This test scaffolds, builds, and runs them with
no manual code — it is the regression test for that generator.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/three_face/test.py
"""

import struct
import subprocess
import sys
import tempfile
from pathlib import Path


def _cmake_gen():
    return ["-G", "MinGW Makefiles"] if sys.platform == "win32" else []


def _cmd(args, cwd):
    r = subprocess.run(
        [str(a) for a in args], cwd=cwd, capture_output=True, text=True
    )
    if r.returncode != 0:
        raise AssertionError(
            f"command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        )
    return r


def run(root: Path) -> None:
    from just_makeit._new import run as jm_new
    from just_makeit._object import run as jm_object
    from just_makeit._app import run as jm_app
    from just_makeit import _config as C

    proj = root / "gaintool"

    # ── 1. Scaffold project + the single shared core object ──────────────
    jm_new("gaintool", proj)
    jm_object(
        proj,
        "gain",
        None,  # standalone object (its own .so)
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
        impl_body="return state->gain * x;",
    )

    # ── 2. Generate all three faces over that one object — NO hand-edit ──
    jm_app(proj, target="c", name="gaintool", object_="gain")
    jm_app(proj, target="console", name="gaintool", object_="gain")
    jm_app(proj, target="pep723", name="gaintool", object_="gain")

    app_c = (proj / "native" / "src" / "app" / "gaintool.c").read_text()
    cli_py = (proj / "src" / "gaintool" / "cli.py").read_text()
    cmake = (proj / "CMakeLists.txt").read_text()
    pyproject = (proj / "pyproject.toml").read_text()

    # ── 3. Assert the generator produced WORKING faces (no stubs) ────────
    assert "<<IMPLEMENT" not in app_c, "C app must be fully generated, no stub"
    assert "<<IMPLEMENT" not in cli_py, "Python CLI must be fully generated"
    assert '"--gain"' in app_c and "gain_step(state, x)" in app_c, (
        "C app must parse --gain and run the step() loop"
    )
    assert '"--gain"' in cli_py and "obj.step(x)" in cli_py, (
        "Python CLI must parse --gain and run the step() loop"
    )
    assert "add_executable(gaintool" in cmake and "gain_core" in cmake
    assert "gaintool.cli:main" in pyproject

    # ── 4. Build the shared core + C binary + Python extension; ctest ────
    _cmd(
        [
            "cmake",
            "-B",
            "build",
            "-S",
            ".",
            *_cmake_gen(),
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        cwd=proj,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # ── 5. Drive all three generated faces on identical input ───────────
    samples = [1.0, 2.0, 3.0, 4.0, -5.0]
    gain = 2.0
    raw = struct.pack(f"<{len(samples)}f", *samples)
    expected = [gain * s for s in samples]

    def _floats(b: bytes):
        return list(struct.unpack(f"<{len(b) // 4}f", b))

    # Faces run from src/ (where the package + cmake-built gain.so live; the
    # pep723 gaintool.py at the root would otherwise shadow the package).
    src = proj / "src"
    exe = (
        proj
        / "build"
        / ("gaintool.exe" if sys.platform == "win32" else "gaintool")
    )
    assert exe.exists(), f"C binary not built: {exe}"

    # Face 1 — standalone C binary.
    r = subprocess.run(
        [str(exe), "--gain", str(gain)],
        input=raw,
        capture_output=True,
        cwd=proj,
    )
    assert r.returncode == 0, f"C binary failed: {r.stderr!r}"
    c_out = _floats(r.stdout)

    # Face 2 — Python CLI (the same code the `gaintool` console script runs).
    r = subprocess.run(
        [sys.executable, "-m", "gaintool.cli", "--gain", str(gain)],
        input=raw,
        capture_output=True,
        cwd=src,
    )
    assert r.returncode == 0, (
        f"Python CLI failed: {r.stderr.decode(errors='replace')}"
    )
    py_out = _floats(r.stdout)

    # Face 3 — Python module API.
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "from gaintool import Gain;"
            f"g = Gain({gain});"
            f"print(' '.join(repr(g.step(x)) for x in {samples}))",
        ],
        capture_output=True,
        text=True,
        cwd=src,
    )
    assert r.returncode == 0, f"module API failed: {r.stderr}"
    mod_out = [float(t) for t in r.stdout.split()]

    # ── 6. One core ⇒ identical results across all three faces ──────────
    def _close(a, b):
        return len(a) == len(b) and all(
            abs(x - y) < 1e-5 for x, y in zip(a, b)
        )

    assert _close(c_out, expected), f"C binary: {c_out} != {expected}"
    assert _close(py_out, expected), f"Python CLI: {py_out} != {expected}"
    assert _close(mod_out, expected), f"module API: {mod_out} != {expected}"
    assert _close(c_out, py_out) and _close(c_out, mod_out), (
        f"faces disagree: c={c_out} py={py_out} mod={mod_out}"
    )

    app_cfg = C.app_config(C.load(proj))
    assert app_cfg["object"] == "gain", app_cfg


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("three_face: PASSED")
