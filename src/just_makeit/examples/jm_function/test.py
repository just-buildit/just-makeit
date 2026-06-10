"""End-to-end test: jm function — module-level C functions exposed to Python.

Exercises:
  - jm new / jm module / jm object  (project + module + gain object scaffold)
  - jm function linear_to_db        (regular C function in its own .c file)
  - jm function clamp --inline      (static inline in _core.h, no .c file)
  - struct assertions on generated files before building
  - .steps/02_patch.py implements all three stubs
  - cmake configure + build + CTest
  - Python smoke test: Gain.step(), linear_to_db(), clamp()
  - TOML config records both functions under [module.utils]

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/jm_function/test.py
"""

import subprocess
import sys
import tempfile

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib
from pathlib import Path

HERE = Path(__file__).parent
STEPS = HERE / ".steps"


def _cmd(args, cwd):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\n"
            f"stderr:\n{r.stderr}"
        )
    return r


def run(root: Path) -> None:
    from just_makeit._new import run as jm_new
    from just_makeit._module import run as jm_module
    from just_makeit._object import run as jm_object
    from just_makeit._function import run as jm_function

    dest = root / "my_utils"

    # ── 1. Scaffold project + module + gain object. ───────────────────────
    # jm_new creates the shell; jm_module adds the utils subpackage;
    # jm_object adds the Gain DSP type inside that module.
    jm_new("my_utils", dest)
    jm_module(dest, "utils")
    jm_object(
        dest,
        "gain",
        module="utils",
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )

    # ── 2. Add module-level functions. ───────────────────────────────────
    # linear_to_db: regular C function — stub written to its own sacred
    # native/src/utils/linear_to_db.c, declaration injected into _core.h,
    # Python wrapper generated in utils_ext.c.
    jm_function(
        dest,
        "linear_to_db",
        "utils",
        params=[("x", "float")],
        return_type="float",
        doc="Convert linear amplitude to dB (20*log10(x)).",
    )

    # clamp: static inline — full body stub injected into _core.h only.
    # No _core.c entry; the compiler sees the body at every call site.
    jm_function(
        dest,
        "clamp",
        "utils",
        params=[("x", "float"), ("lo", "float"), ("hi", "float")],
        return_type="float",
        inline=True,
    )

    # ── 3. Structural assertions (pre-build). ────────────────────────────
    ext = (dest / "native/src/utils/utils_ext.c").read_text(encoding="utf-8")
    assert "_bind_linear_to_db" in ext, (
        "utils_ext.c missing _bind_linear_to_db"
    )
    assert "_bind_clamp" in ext, "utils_ext.c missing _bind_clamp"

    header = (dest / "native/inc/utils/utils_core.h").read_text(
        encoding="utf-8"
    )
    assert "linear_to_db" in header, (
        "utils_core.h missing linear_to_db declaration"
    )
    assert "clamp" in header, "utils_core.h missing clamp inline body"

    # linear_to_db is a regular function: stub lives in its own .c file,
    # which includes the module header and carries the single definition.
    fn_c = dest / "native/src/utils/linear_to_db.c"
    assert fn_c.exists(), "native/src/utils/linear_to_db.c was not created"
    fn_c_text = fn_c.read_text(encoding="utf-8")
    assert "linear_to_db(float x)" in fn_c_text, (
        "linear_to_db.c missing the function stub"
    )
    assert '#include "utils/utils_core.h"' in fn_c_text, (
        "linear_to_db.c must include the module header"
    )

    # The shared _core.c stays the bare scaffold — functions never land there.
    core_c = (dest / "native/src/utils/utils_core.c").read_text(
        encoding="utf-8"
    )
    assert "linear_to_db" not in core_c, (
        "utils_core.c must not contain the linear_to_db stub"
    )

    # clamp is inline: no .c file at all (only the static inline in _core.h).
    assert not (dest / "native/src/utils/clamp.c").exists(), (
        "inline clamp must not get its own .c file"
    )

    with (dest / "just-makeit.toml").open("rb") as f:
        cfg = tomllib.load(f)
    fn_names = [
        fn["name"] for fn in cfg["module"]["utils"].get("functions", [])
    ]
    assert "linear_to_db" in fn_names, (
        "TOML missing linear_to_db in [module.utils].functions"
    )
    assert "clamp" in fn_names, (
        "TOML missing clamp in [module.utils].functions"
    )

    # ── 4. Patch stubs with real implementations. ────────────────────────
    _cmd([sys.executable, str(STEPS / "02_patch.py")], cwd=dest)

    # ── 5. CMake configure + build + CTest. ──────────────────────────────
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
        cwd=dest,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=dest)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=dest)

    # ── 6. Python smoke test. ────────────────────────────────────────────
    # Run in a subprocess so the freshly built .so is imported cleanly
    # without any stale module state from this process.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys, math
sys.path.insert(0, 'src')
from my_utils.utils import Gain, linear_to_db, clamp

# Gain: state->gain * x  (gain=2.0, x=1.0 => 2.0)
g = Gain(gain=2.0)
assert abs(g.step(1.0) - 2.0) < 1e-6, f"Gain: {g.step(1.0)}"

# linear_to_db: 1.0 => 0 dB, 10.0 => 20 dB
assert abs(linear_to_db(1.0)) < 0.01, (
    f"linear_to_db(1.0)={linear_to_db(1.0)}"
)
assert abs(linear_to_db(10.0) - 20.0) < 0.01, (
    f"linear_to_db(10.0)={linear_to_db(10.0)}"
)

# clamp: above hi, below lo, in range
assert clamp(5.0, 0.0, 3.0) == 3.0, f"clamp(5,0,3)={clamp(5.0,0.0,3.0)}"
assert clamp(-1.0, 0.0, 3.0) == 0.0, (
    f"clamp(-1,0,3)={clamp(-1.0,0.0,3.0)}"
)
assert clamp(1.5, 0.0, 3.0) == 1.5, (
    f"clamp(1.5,0,3)={clamp(1.5,0.0,3.0)}"
)

print("jm_function: all Python checks passed")
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

    # ── 7. Type stub sanity check. ───────────────────────────────────────
    pyi = (dest / "src" / "my_utils" / "utils" / "utils.pyi").read_text()
    assert "class Gain:" in pyi, "utils.pyi missing Gain class"
    assert "linear_to_db" in pyi, "utils.pyi missing linear_to_db"
    assert "clamp" in pyi, "utils.pyi missing clamp"


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("jm_function: PASSED")
