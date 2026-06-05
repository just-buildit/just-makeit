"""End-to-end test: one C core exposed as THREE faces.

Proves the combined-target pattern just-makeit is built for — a single C core
(`gain_core.c`, compiled once as an OBJECT library) drives:

  1. a standalone **C binary CLI**      (build/gaintool)
  2. a **Python CLI** / console entry   (python -m gaintool.cli)
  3. a **Python module API**            (from gaintool import Gain)

`gaintool` scales a stream of float32 samples by --gain. All three faces call
the same `gain_step()` and must produce byte-identical output.

What jm gives you vs what you fill in: `jm app` scaffolds the executable target,
the CMake wiring, the console entry, and the [project.scripts] line — but the
argv parsing and the I/O loop are `<<IMPLEMENT>>` stubs. This test asserts the
scaffold, then fills the two executable faces by hand (the gap a future
spec-driven `jm app` should generate; see README.md).

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


def _cmd(args, cwd, env=None):
    r = subprocess.run(
        [str(a) for a in args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise AssertionError(
            f"command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        )
    return r


# Working C main() — the human-filled "gap": argv parse + float32 I/O loop over
# the shared gain_step(). Replaces the jm-generated <<IMPLEMENT>> stub.
_C_MAIN = """\
// gaintool — scale a stream of float32 samples by --gain.
// One C core (gain_core), three faces: this binary, a Python CLI, a module.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "gain/gain_core.h"

int
main(int argc, char *argv[])
{
    float gain = 1.0f;
    const char *in_path = NULL;
    const char *out_path = NULL;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--gain") && i + 1 < argc) {
            gain = strtof(argv[++i], NULL);
        } else if ((!strcmp(argv[i], "--input") || !strcmp(argv[i], "-i"))
                   && i + 1 < argc) {
            in_path = argv[++i];
        } else if ((!strcmp(argv[i], "--output") || !strcmp(argv[i], "-o"))
                   && i + 1 < argc) {
            out_path = argv[++i];
        } else {
            fprintf(stderr,
                    "usage: gaintool [--gain G] [--input FILE] "
                    "[--output FILE]\\n");
            return 2;
        }
    }

    FILE *in = in_path ? fopen(in_path, "rb") : stdin;
    FILE *out = out_path ? fopen(out_path, "wb") : stdout;
    if (!in || !out) {
        fprintf(stderr, "error: cannot open input/output\\n");
        return 1;
    }

    gain_state_t *state = gain_create(gain);
    if (!state) {
        fprintf(stderr, "error: gain_create() failed\\n");
        return 1;
    }

    float x;
    while (fread(&x, sizeof x, 1, in) == 1) {
        float y = gain_step(state, x);
        fwrite(&y, sizeof y, 1, out);
    }

    gain_destroy(state);
    if (in_path) fclose(in);
    if (out_path) fclose(out);
    return 0;
}
"""

# Working Python CLI — same UX, same core (loops the extension's .step()).
_CLI_PY = '''\
"""gaintool command-line interface — the Python face of the shared C core."""

import argparse
import sys

import numpy as np

from . import Gain


def _make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gaintool",
        description="Scale a stream of float32 samples by --gain.",
    )
    p.add_argument("--gain", type=float, default=1.0)
    p.add_argument("--input", "-i", default=None, help="float32 file (default: stdin)")
    p.add_argument("--output", "-o", default=None, help="float32 file (default: stdout)")
    return p


def main() -> None:
    args = _make_parser().parse_args()
    if args.input:
        data = np.fromfile(args.input, dtype=np.float32)
    else:
        data = np.frombuffer(sys.stdin.buffer.read(), dtype=np.float32)

    g = Gain(args.gain)
    out = np.array([g.step(float(x)) for x in data], dtype=np.float32)

    if args.output:
        out.tofile(args.output)
    else:
        sys.stdout.buffer.write(out.tobytes())


if __name__ == "__main__":
    main()
'''


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

    # ── 2. Scaffold all three faces over that one object ─────────────────
    jm_app(proj, target="c", name="gaintool", object_="gain")
    jm_app(proj, target="console", name="gaintool", object_="gain")
    jm_app(proj, target="pep723", name="gaintool", object_="gain")

    app_c = proj / "native" / "src" / "app" / "gaintool.c"
    cli_py = proj / "src" / "gaintool" / "cli.py"
    pep723 = proj / "gaintool.py"
    cmake = (proj / "CMakeLists.txt").read_text()
    pyproject = (proj / "pyproject.toml").read_text()

    # ── 3. Assert what jm scaffolded (plumbing) before we fill the gap ───
    assert app_c.exists() and "<<IMPLEMENT" in app_c.read_text(), (
        "jm should scaffold a C app stub with an <<IMPLEMENT>> marker"
    )
    assert "gain/gain_core.h" in app_c.read_text(), (
        "C stub includes the core header"
    )
    assert "add_executable(gaintool" in cmake, (
        "CMake gets the executable target"
    )
    assert "gain_core" in cmake, (
        "C binary links the shared gain_core OBJECT lib"
    )
    assert cli_py.exists() and "argparse" in cli_py.read_text(), (
        "console CLI scaffolded"
    )
    assert "gaintool.cli:main" in pyproject, (
        "[project.scripts] wired for pip install"
    )
    assert pep723.exists() and "# /// script" in pep723.read_text(), (
        "pep723 face scaffolded"
    )

    # ── 4. Fill the two executable faces (the documented gap) ────────────
    app_c.write_text(_C_MAIN, encoding="utf-8")
    cli_py.write_text(_CLI_PY, encoding="utf-8")

    # ── 5. Build the shared core + C binary + Python extension; run ctest ─
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

    # ── 6. Drive all three faces on identical input; cross-check ─────────
    samples = [1.0, 2.0, 3.0, 4.0, -5.0]
    gain = 2.0
    raw = struct.pack(f"<{len(samples)}f", *samples)
    expected = [gain * s for s in samples]

    def _floats(b: bytes):
        return list(struct.unpack(f"<{len(b) // 4}f", b))

    # Face 1 — standalone C binary (reads stdin, writes stdout).
    exe = (
        proj
        / "build"
        / ("gaintool.exe" if sys.platform == "win32" else "gaintool")
    )
    assert exe.exists(), f"C binary not built: {exe}"
    r = subprocess.run(
        [str(exe), "--gain", str(gain)],
        input=raw,
        capture_output=True,
        cwd=proj,
    )
    assert r.returncode == 0, f"C binary failed: {r.stderr!r}"
    c_out = _floats(r.stdout)

    # Faces 2 & 3 run from src/ — that's where the package and the cmake-built
    # gain.so live. (Running from the project root would let the pep723
    # gaintool.py script shadow the gaintool/ package — a real name-collision
    # gotcha; see README.)
    src = proj / "src"

    # Face 2 — Python CLI (same code path as the installed `gaintool` script).
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

    # Face 3 — Python module API (the extension class directly).
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

    # ── 7. One core ⇒ identical results across all three faces ───────────
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

    # [app] config reflects the last scaffold call.
    app_cfg = C.app_config(C.load(proj))
    assert app_cfg["object"] == "gain", app_cfg


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("three_face: PASSED")
