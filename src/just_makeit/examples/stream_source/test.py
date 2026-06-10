"""End-to-end test for the stream_source example — keeps the README honest.

Runs the exact walkthrough the README teaches: scaffold the streamable ramp
source (matching .steps/01_scaffold.sh), implement step() with the body the
README shows, build, then run .steps/03_demo.py against the built extension.
The demo — the user-facing API usage most likely to break — is the same file
the README embeds and is executed here, so it can never go stale.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/stream_source/test.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
STEPS = HERE / ".steps"


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


# The generated step() stub, and the body the README's section 2 shows.
_STEP_STUB = "    (void)state; /* TODO: implement */\n    return (float)0;"
_STEP_BODY = (
    "    const float out = state->value;\n"
    "    state->value += state->step_inc;\n"
    "    return out;"
)


def run(root: Path) -> None:
    from just_makeit._new import run as jm_new
    from just_makeit._object import run as jm_object

    # 1. Scaffold — mirrors .steps/01_scaffold.sh (CLI flags == API kwargs).
    proj = root / "stream_source_demo"
    jm_new("stream_source_demo", proj)
    jm_object(
        proj,
        "ramp",
        module=None,
        arg_type="void",
        return_type="float",
        mutable=True,
        streamable=True,
        stream_block_default=256,
        state_vars=[("value", "float", "0.0f"), ("step_inc", "float", "1.0f")],
    )

    # The one flag did the work: a C iterator + stream()/__iter__ in the ext,
    # driving the built-in steps() for a source.
    ext = (proj / "native" / "src" / "ramp" / "ramp_ext.c").read_text()
    assert "RampStreamIter" in ext
    assert "Ramp_stream" in ext
    assert 'PyObject_CallMethod(it->src, "steps", "n"' in ext
    pyi = (proj / "src" / "stream_source_demo" / "ramp.pyi").read_text()
    assert "def stream(" in pyi
    assert "def __iter__(self) -> Iterator[NDArray[np.float32]]:" in pyi

    # 2. Implement step() with the body the README shows.
    core_h = proj / "native" / "inc" / "ramp" / "ramp_core.h"
    text = core_h.read_text(encoding="utf-8")
    assert _STEP_STUB in text, "step() stub not found — template changed?"
    core_h.write_text(text.replace(_STEP_STUB, _STEP_BODY), encoding="utf-8")

    # 3. Build (cmake + ctest runs the generated C smoke test).
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

    # 4. Run the README's demo verbatim against the freshly built extension.
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(proj / "src") + os.pathsep + env.get("PYTHONPATH", "")
    )
    _cmd([sys.executable, str(STEPS / "03_demo.py")], cwd=proj, env=env)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("stream_source: PASSED")
