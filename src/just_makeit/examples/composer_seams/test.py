"""End-to-end test: a `kind = "composer"` module and its straight-C seams.

`composer` was the last of the three object-of-objects kinds with no example
(`handle` has `composites`; `capsule` and `composer` had prose only), and
gh-998's generated bridge header had none at all.

  1. Scaffold the generator a source composes into -- a plain `jm object`.
  2. Write the backing kernel by hand, in a `c_deps` directory.
  3. Declare the composer. Manifest only: there is no `jm composer`.
  4. `jm apply`, and assert the bridge header is what a consumer can use.
  5. Write the two seam bodies -- straight C, including jm's header.
  6. cmake configure + build.
  7. Drive the four OO types and both seams from Python.
  8. Compile and RUN a C consumer that includes only the bridge header.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/composer_seams/test.py
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
STEPS = HERE / ".steps"


def _cmd(args, cwd):
    r = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=600
    )
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        )
    return r


def run(root: Path) -> None:
    from just_makeit._apply import run as jm_apply
    from just_makeit._new import run as jm_new
    from just_makeit._object import run as jm_object

    # ── 1. The generator being composed ──────────────────────────────────
    jm_new("studio", root / "studio")
    proj = root / "studio"
    jm_object(
        proj,
        "clip",
        None,
        state_vars=[("level", "double", "0.0")],
        arg_type="void",
        return_type="float _Complex",
    )
    # A constant-level source: the composed output is then checkable by eye,
    # and the example stays about the seams rather than about a kernel.
    core_h = proj / "native" / "inc" / "clip" / "clip_core.h"
    text = core_h.read_text(encoding="utf-8")
    stub = (
        "    (void)state; /* TODO: implement */\n    return (float _Complex)0;"
    )
    assert stub in text, "clip_step stub not found -- did the scaffold change?"
    core_h.write_text(
        text.replace(stub, "    return (float _Complex)state->level;", 1),
        encoding="utf-8",
    )

    # ── 2. The hand-written backing, in a c_deps directory ───────────────
    (proj / "native" / "inc" / "playlist").mkdir(parents=True, exist_ok=True)
    backing = proj / "native" / "src" / "backing"
    backing.mkdir(parents=True, exist_ok=True)
    shutil.copy(
        STEPS / "02_playlist_core.h",
        proj / "native" / "inc" / "playlist" / "playlist_core.h",
    )
    shutil.copy(STEPS / "02_playlist_core.c", backing / "playlist_core.c")
    shutil.copy(STEPS / "02_CMakeLists.txt", backing / "CMakeLists.txt")

    # ── 3. Declare the composer ──────────────────────────────────────────
    _cmd([sys.executable, str(STEPS / "03_manifest.py")], cwd=proj)

    # ── 4. Apply, and check the header a consumer will include ───────────
    jm_apply(proj)
    bridge = proj / "native" / "inc" / "playlist" / "playlist_bridge.h"
    assert bridge.exists(), "gh-998: no bridge header was written"
    bt = bridge.read_text(encoding="utf-8")
    assert "clip_state_t *clip_from_source(const clip_t *, double);" in bt
    assert "double clip_duration(const clip_t *);" in bt
    # Self-contained: a consumer must not have to work out the include order.
    assert '#include "playlist/playlist_core.h"' in bt
    assert '#include "clip/clip_core.h"' in bt
    assert "PLAYLIST_BRIDGE_H" in bt, "no include guard"
    # And the binding must INCLUDE it rather than re-declare the seams --
    # a second copy of a signature jm owns is the defect gh-998 removed.
    ext = (proj / "native" / "src" / "playlist" / "playlist_ext.c").read_text(
        encoding="utf-8"
    )
    assert '#include "playlist/playlist_bridge.h"' in ext
    assert "extern " not in ext, "the binding still declares a seam itself"

    # ── 5. The seam bodies ───────────────────────────────────────────────
    shutil.copy(STEPS / "05_playlist_bridge.c", backing / "playlist_bridge.c")

    # ── 6. Build ─────────────────────────────────────────────────────────
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
        cwd=proj,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)

    # ── 7. The Python face ───────────────────────────────────────────────
    _cmd([sys.executable, str(STEPS / "07_demo.py")], cwd=proj)

    # ── 8. The C face: only the generated header ─────────────────────────
    # The claim gh-998 exists to make. Compiling proves the header is usable
    # standalone; running proves the seams it declares are the ones linked.
    tests_dir = proj / "native" / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(STEPS / "08_consumer.c", tests_dir / "test_bridge.c")
    # Exact directory names, not a substring test: `clip_core.dir` is also a
    # substring of `test_clip_core.dir` and `bench_clip_core.dir`, and both of
    # those hold an object defining `main`.
    wanted = {"backing_core.dir", "clip_core.dir"}
    picked = [
        p for p in (proj / "build").rglob("*.o") if wanted & set(p.parts)
    ]
    objs = sorted(str(p) for p in picked)
    assert objs, "no backing/clip objects were built"
    # Guard on the PATH COMPONENT, never on the whole path: under pytest the
    # tmpdir is itself named `test_example_composer_seams_0`, so a substring
    # test for "test_" matches every object and reports on nothing it aimed
    # at. Same anchoring mistake the `wanted` set above exists to avoid.
    assert all(
        not ({"test_clip_core.dir", "bench_clip_core.dir"} & set(p.parts))
        for p in picked
    ), objs
    cc = shutil.which("cc") or shutil.which("gcc")
    assert cc, "no C compiler"
    exe = proj / "build" / "test_bridge"
    _cmd(
        [
            cc,
            "-I",
            "native/inc",
            str(tests_dir / "test_bridge.c"),
            *objs,
            "-lm",
            "-o",
            str(exe),
        ],
        cwd=proj,
    )
    out = _cmd([str(exe)], cwd=proj)
    assert "bridge consumer: PASSED" in out.stdout, out.stdout


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("composer_seams: PASSED")
