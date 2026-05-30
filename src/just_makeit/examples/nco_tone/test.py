"""End-to-end test: NCO tone generator backed by doppler's nco_state_t.

Demonstrates:
  - [project] find_packages = ["Doppler"]  — managed external-deps block
  - [tone] extra_link_libs = ["doppler::doppler_lib"]  — standalone linking
  - opaque state (nco_state_t*) with create_impl / destroy_impl
  - jm apply keeping the find_package() call alive across re-runs

doppler must be installed (or its build tree discoverable).  If it is not
found, the test exits 0 with a clear skip message so CI machines without
doppler installed do not fail.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/nco_tone/test.py [--doppler-prefix PATH]
"""

import subprocess
import sys
import tempfile
from pathlib import Path


def _cmake_gen():
    return ["-G", "MinGW Makefiles"] if sys.platform == "win32" else []


def _cmd(args, cwd, env=None):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\n"
            f"stderr:\n{r.stderr}"
        )
    return r


def _find_doppler_prefix() -> str | None:
    """Return the doppler prefix to pass to --doppler-prefix.

    Searches for doppler-config.cmake in common install locations and the
    local doppler build tree.  Returns the prefix (i.e. the directory one
    level above lib/cmake/doppler/) or None when not found."""
    candidates = [
        Path("/usr/local"),
        Path("/usr"),
        Path.home() / "doppler" / "build",
    ]
    for prefix in candidates:
        for rel in (
            "doppler-config.cmake",
            "lib/cmake/doppler/doppler-config.cmake",
            "lib64/cmake/doppler/doppler-config.cmake",
        ):
            if (prefix / rel).exists():
                return str(prefix)
    return None


# ── TOML fragment ─────────────────────────────────────────────────────────────
#
# [project] find_packages must live in the manifest, not a fragment.
# We write it directly to just-makeit.toml after jm new.
#
# extra_link_libs names the imported target created by doppler's cmake config.
# mutable = true because step() calls nco_steps_u32() which advances state.

_FRAGMENT = '''\
[tone]
arg_type     = "void"
return_type  = "float _Complex"
mutable      = "true"
extra_link_libs = ["doppler::doppler_lib"]
create_impl  = """
obj->nco = nco_create(norm_freq, 0);
if (!obj->nco) { free(obj); return NULL; }
"""
destroy_impl = """
nco_destroy(state->nco);
"""

[[tone.state]]
name    = "norm_freq"
type    = "double"
default = "0.0"

[[tone.state]]
name   = "nco"
type   = "nco_state_t *"
opaque = true
'''

# step() body: advance NCO one sample, map phase → complex exponential.
_STEP_OLD = (
    "    (void)state; /* TODO: implement */\n    return (float complex)0;"
)
_STEP_NEW = """\
    uint32_t phase;
    nco_steps_u32(state->nco, 1, &phase);
    /* phase ∈ [0, 2^32) → angle ∈ [0, 2π) */
    float angle = (float)phase * (float)(2.0 * 3.14159265358979323846 / 4294967296.0);
    return cosf(angle) + I * sinf(angle);"""


def _patch_step(core_h: Path) -> None:
    text = core_h.read_text(encoding="utf-8")
    if _STEP_NEW.split("\n")[0].strip() in text:
        return  # already patched
    if _STEP_OLD not in text:
        raise AssertionError(f"step() stub not found in {core_h}")
    core_h.write_text(text.replace(_STEP_OLD, _STEP_NEW), encoding="utf-8")


def run(root: Path, doppler_prefix: str | None = None) -> None:
    if doppler_prefix is None:
        doppler_prefix = _find_doppler_prefix()
    if doppler_prefix is None:
        print(
            "nco_tone: SKIP — doppler not found (pass --doppler-prefix PATH)"
        )
        return

    from just_makeit import _config as C
    from just_makeit._apply import run as jm_apply
    from just_makeit._new import run as jm_new

    # 1. Empty project.
    proj = root / "nco_tone_demo"
    jm_new("nco_tone_demo", proj)

    # 2. Add find_packages to [project] in the manifest.
    # [project] must live in the manifest, not in a fragment.
    cfg = C.load(proj)
    cfg["project"]["find_packages"] = ["Doppler"]
    C.save(proj, cfg)

    # 3. Fragment: declares the tone component with opaque NCO state.
    fragment = root / "tone.toml"
    fragment.write_text(_FRAGMENT, encoding="utf-8")
    jm_apply(proj, fragment=fragment)

    # 3. Verify the top CMakeLists has the external-deps sentinel block.
    cmake_text = (proj / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "# ── External deps" in cmake_text, cmake_text
    assert "find_package(Doppler REQUIRED)" in cmake_text, cmake_text
    assert "# ── End external deps" in cmake_text, cmake_text

    # 4. Verify a second jm apply is a no-op (idempotent).
    jm_apply(proj)
    cmake_text2 = (proj / "CMakeLists.txt").read_text(encoding="utf-8")
    assert cmake_text2 == cmake_text, (
        "jm apply changed CMakeLists.txt on second run"
    )

    # 5. Verify the component CMakeLists links doppler::doppler_lib.
    comp_cmake = (
        proj / "native" / "src" / "tone" / "CMakeLists.txt"
    ).read_text(encoding="utf-8")
    assert "doppler::doppler_lib" in comp_cmake, comp_cmake

    # 6. Verify the generated header has the opaque nco field.
    core_h = proj / "native" / "inc" / "tone" / "tone_core.h"
    h = core_h.read_text(encoding="utf-8")
    assert "nco_state_t * nco;" in h, h

    # 7. Add the doppler nco include to tone_core.h and patch step().
    # The nco include goes after clib_common.h (which is always first in
    # jm-generated headers).  The step body uses cosf/sinf so <math.h> is
    # needed too.
    h_text = core_h.read_text(encoding="utf-8")
    if '#include "nco/nco_core.h"' not in h_text:
        h_text = h_text.replace(
            '#include "clib_common.h"',
            '#include "clib_common.h"\n#include "nco/nco_core.h"\n#include <math.h>',
            1,
        )
        core_h.write_text(h_text, encoding="utf-8")
    _patch_step(core_h)

    # 8. cmake configure + build + ctest.
    # cmake doesn't search lib64/cmake on all platforms, so resolve
    # Doppler_DIR explicitly from the given prefix.
    prefix_path = Path(doppler_prefix)
    doppler_dir = None
    for rel in (
        "lib/cmake/doppler",
        "lib64/cmake/doppler",
        ".",  # raw build dir
    ):
        candidate = prefix_path / rel / "doppler-config.cmake"
        if candidate.exists():
            doppler_dir = str(prefix_path / rel)
            break
    if doppler_dir is None:
        doppler_dir = doppler_prefix  # fallback: let cmake search

    import os

    env = os.environ.copy()
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
            f"-DDoppler_DIR={doppler_dir}",
        ],
        cwd=proj,
        env=env,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # 9. Quick Python smoke-test: tone at 0.25 cycles/sample → quarter-circle steps.
    sys.path.insert(0, str(proj / "src"))
    try:
        import importlib

        pkg = importlib.import_module("nco_tone_demo")
        tone = pkg.Tone(norm_freq=0.25)
        samples = [tone.step() for _ in range(4)]
    finally:
        sys.path.pop(0)
        import sys as _sys

        for key in list(_sys.modules):
            if "nco_tone_demo" in key:
                del _sys.modules[key]

    # Four quarter-circle steps: 1, j, -1, -j (within float precision).
    expected = [1 + 0j, 0 + 1j, -1 + 0j, 0 - 1j]
    for i, (got, want) in enumerate(zip(samples, expected)):
        assert abs(got - want) < 1e-6, f"sample[{i}]: got {got}, want {want}"

    print("nco_tone: PASSED")


if __name__ == "__main__":
    doppler_prefix = None
    args = sys.argv[1:]
    if "--doppler-prefix" in args:
        idx = args.index("--doppler-prefix")
        doppler_prefix = args[idx + 1]

    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp), doppler_prefix=doppler_prefix)
