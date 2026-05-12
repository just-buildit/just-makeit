"""End-to-end test: stream_chunker scaffold → implement → build → verify.

Demonstrates variable-size input and variable-size output:
  - push() accepts any number of samples per call (variable input)
  - push() returns 0, 1, or N complete chunks per call (variable output)
  - --variable-output pre-allocates the output buffer at __init__
  - zero-copy view returned; copy before next call if you need to keep it

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/stream_chunker/test.py
"""

import subprocess
import sys
import tempfile
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
    from just_makeit._object import run as jm_object
    from just_makeit._method import run as jm_method

    # 1. Scaffold project skeleton (no objects yet)
    jm_new("my_chunker", root / "my_chunker")
    proj = root / "my_chunker"

    # 2. Add the chunker object with --no-step.
    #    Internal 256-sample accumulation buffer; chunk_size controls output
    #    granularity.  n_buf tracks how many samples are waiting.
    #    --no-step: the primary interface is the push() method, not step().
    jm_object(
        proj,
        "chunker",
        module=None,
        state_vars=[
            ("chunk_size", "int32_t",           "64"),
            ("buf",        "float _Complex[256]", ""),
            ("n_buf",      "int32_t",            "0"),
        ],
        no_step=True,
    )

    # 3. Add push(): variable input → variable output.
    #    --variable-output pre-allocates the output buffer once at __init__
    #    (sized to push_max_out()); each push() call returns a zero-copy view
    #    into that buffer containing however many complete chunks were emitted.
    jm_method(
        root=proj,
        object_name="chunker",
        method_name="push",
        module=None,
        arg_type="float _Complex",
        return_type="float _Complex",
        variable_output=True,
        multi_output=[],
    )

    # Verify stubs were written before patching
    core_c = (proj / "native/src/chunker/chunker_core.c").read_text()
    assert "chunker_push_max_out" in core_c
    assert "chunker_push" in core_c

    # 4. Patch in the real implementation
    _cmd([sys.executable, str(STEPS / "02_patch.py")], cwd=proj)

    # 5. CMake configure + build + CTest
    _cmd(
        [
            "cmake", "-B", "build", "-S", ".",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        cwd=proj,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # 6. Python integration: feed irregular bursts, verify chunk boundaries
    _cmd([sys.executable, str(STEPS / "04_demo.py")], cwd=proj)

    # 7. Verify: push is in the Python extension; step is absent (--no-step)
    ext = (proj / "native/src/chunker/chunker_ext.c").read_text()
    assert "Chunker_push" in ext    # push binding was registered
    assert "Chunker_step" not in ext  # --no-step: no scalar step

    pyi = (proj / "src/my_chunker/chunker.pyi").read_text()
    assert "class Chunker:" in pyi
    assert "def step" not in pyi    # --no-step: no step stub


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("stream_chunker: PASSED")
