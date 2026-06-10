"""End-to-end test for the stream_blockwise example — keeps the README honest.

Runs the exact walkthrough the README teaches: scaffold the streamable drainer
(matching .steps/01_scaffold.sh), fill in run()/run_max_out() with the bodies
the README shows, build, then run .steps/03_demo.py against the built
extension. The demo — the user-facing API usage most likely to break — is the
same file the README embeds and is executed here, so it can never go stale.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/stream_blockwise/test.py
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


# The generated stubs, and the bodies the README's section 2 shows.
_MAX_OUT_STUB = "    (void)state;\n    return 0; /* placeholder */"
_MAX_OUT_BODY = "    return (size_t)state->total;"
_RUN_STUB = (
    "    (void)state;\n"
    "    (void)n;\n"
    "    (void)out;\n"
    "    return 0; /* placeholder */"
)
_RUN_BODY = (
    "    int32_t avail = state->total - state->pos;\n"
    "    if (avail < 0)\n"
    "        avail = 0;\n"
    "    size_t k = (size_t)avail < n ? (size_t)avail : n;\n"
    "    for (size_t i = 0; i < k; i++)\n"
    "        out[i] = (float complex)(float)(state->pos + (int32_t)i);\n"
    "    state->pos += (int32_t)k;\n"
    "    return k;"
)


def run(root: Path) -> None:
    from just_makeit._new import run as jm_new
    from just_makeit._object import run as jm_object

    # 1. Scaffold — mirrors .steps/01_scaffold.sh. --variable-output adds the
    #    run(n) producer; --streamable picks it as the stream source.
    proj = root / "stream_blockwise_demo"
    jm_new("stream_blockwise_demo", proj)
    jm_object(
        proj,
        "drainer",
        module=None,
        arg_type="void",
        return_type="float _Complex",
        mutable=True,
        streamable=True,
        variable_output=True,
        state_vars=[("total", "int32_t", "20"), ("pos", "int32_t", "0")],
    )

    ext = (proj / "native" / "src" / "drainer" / "drainer_ext.c").read_text()
    assert "DrainerStreamIter" in ext
    assert "Drainer_stream" in ext
    # The producer is the variable_output method, not the built-in steps.
    assert 'PyObject_CallMethod(it->src, "run", "n"' in ext
    pyi = (proj / "src" / "stream_blockwise_demo" / "drainer.pyi").read_text()
    assert "def stream(" in pyi
    assert "def __iter__(self) -> Iterator[NDArray[np.complex64]]:" in pyi

    # 2. Fill in run_max_out() and run() with the bodies the README shows.
    core_c = proj / "native" / "src" / "drainer" / "drainer_core.c"
    text = core_c.read_text(encoding="utf-8")
    for stub, body in (
        (_MAX_OUT_STUB, _MAX_OUT_BODY),
        (_RUN_STUB, _RUN_BODY),
    ):
        assert stub in text, "run() stub not found — template changed?"
        text = text.replace(stub, body)
    core_c.write_text(text, encoding="utf-8")

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
    print("stream_blockwise: PASSED")
