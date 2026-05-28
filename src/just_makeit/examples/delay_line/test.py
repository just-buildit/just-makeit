"""End-to-end test: delay_line — circular delay with runtime length.

A real DSP use case for opaque state fields.  A ring-buffer delay line
whose length is a constructor argument, so the storage cannot be sized
at code-generation time and must live on the heap.

  • `length` is a regular scalar field — the user passes it to
    `DelayLine(length=64)`.  It also acts as a "config" field that
    survives reset() (managed via reset_impl).
  • `idx` is a regular scalar field — the current write position, set
    to 0 at construction and reset.
  • `taps` is **opaque** — a `float *` heap buffer of `length` floats,
    allocated in create_impl, freed in destroy_impl, zeroed on reset.

Step semantics: standard sample-delay-by-N.

  on input x:
      output     = taps[idx]      # value from N samples ago
      taps[idx]  = x              # overwrite with new sample
      idx        = (idx + 1) % length
      return output

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/delay_line/test.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path


def _cmake_gen():
    return ["-G", "MinGW Makefiles"] if sys.platform == "win32" else []


def _cmd(args, cwd):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\n"
            f"stderr:\n{r.stderr}"
        )
    return r


# The whole component in one TOML fragment.
#
# create_impl: copy the constructor args into the struct, then calloc
# the ring buffer.  On allocation failure, free obj and return NULL —
# the standard ownership pattern.
#
# reset_impl: zero idx and clear the ring buffer, but PRESERVE length
# (it was set at construction and is a config field, not runtime state).
#
# destroy_impl: free the ring buffer.  The trailing free(state) is
# appended automatically.
_DELAY_LINE_FRAGMENT = '''\
# Circular delay line with runtime-configurable length.
[delay_line]
arg_type     = "float"
return_type  = "float"
mutable      = "true"
create_impl  = """
obj->length = length;
obj->idx    = idx;
obj->taps   = calloc(length, sizeof(*obj->taps));
if (!obj->taps) { free(obj); return NULL; }
"""
reset_impl   = """
state->idx = 0;
memset(state->taps, 0, sizeof(*state->taps) * state->length);
/* length is preserved — set at construction, not runtime state */
"""
destroy_impl = """
free(state->taps);
"""

[[delay_line.state]]
name    = "length"
type    = "uint32_t"
default = "16"

[[delay_line.state]]
name    = "idx"
type    = "uint32_t"
default = "0"

[[delay_line.state]]
name   = "taps"
type   = "float *"
opaque = true
'''


# Patch step() — the real DSP one-liner.  Idempotent on re-run.
_STEP_PATCH_OLD = (
    "    (void)state; /* TODO: implement using state variables */\n    return (float)x;"
)
_STEP_PATCH_NEW = (
    "    const float out = state->taps[state->idx];\n"
    "    state->taps[state->idx] = x;\n"
    "    state->idx = (state->idx + 1U) % state->length;\n"
    "    return out;"
)


def _patch_step(core_h: Path) -> None:
    text = core_h.read_text(encoding="utf-8")
    if "const float out = state->taps" in text:
        return  # already patched
    if _STEP_PATCH_OLD not in text:
        raise AssertionError(f"step() stub not found in {core_h} — template changed?")
    core_h.write_text(text.replace(_STEP_PATCH_OLD, _STEP_PATCH_NEW), encoding="utf-8")


# Hand-written C smoke test that matches our reset semantics (length is
# preserved across reset because it's the configured ring-buffer size).
_C_TEST_BODY = """\
#include "delay_line/delay_line_core.h"
#include <stdio.h>
#include <stdlib.h>

#define CHECK(cond) \\
    do { if (!(cond)) { \\
        fprintf(stderr, "FAIL %s:%d  %s\\n", __FILE__, __LINE__, #cond); \\
        _fails++; \\
    } } while (0)

int main(void)
{
    int _fails = 0;
    const uint32_t N = 8;
    delay_line_state_t *obj = delay_line_create(N, 0);
    CHECK(obj != NULL);
    if (!obj) return 1;

    CHECK(delay_line_get_length(obj) == N);
    CHECK(delay_line_get_idx(obj) == 0);

    /* First N outputs should be the initial zeros in the ring buffer. */
    for (uint32_t i = 0; i < N; i++) {
        float y = delay_line_step(obj, (float)(i + 1));
        CHECK(y == 0.0f);
    }
    /* Now the (i+N+1)th input drops out — output should equal the (i+1)th
     * input we sent above. */
    for (uint32_t i = 0; i < N; i++) {
        float y = delay_line_step(obj, 0.0f);
        CHECK(y == (float)(i + 1));
    }

    /* reset() must zero the buffer + idx but preserve length. */
    delay_line_reset(obj);
    CHECK(delay_line_get_length(obj) == N);   /* preserved! */
    CHECK(delay_line_get_idx(obj) == 0);
    for (uint32_t i = 0; i < N; i++) {
        float y = delay_line_step(obj, (float)(i + 1));
        CHECK(y == 0.0f);
    }

    delay_line_destroy(obj);
    if (_fails) {
        fprintf(stderr, "test_delay_line_core FAILED (%d)\\n", _fails);
        return 1;
    }
    printf("test_delay_line_core PASSED\\n");
    return 0;
}
"""


# Replace the auto-generated pytest with one that actually validates the
# delay semantics — feed N+M samples, verify the first N out are zero
# and the rest match the input M samples back.
_PYTEST_BODY = '''"""End-to-end test for the delay_line component."""

import numpy as np

from demo import DelayLine


def test_delay_by_n():
    """A delay line of length N must delay every sample by exactly N."""
    N = 8
    obj = DelayLine(length=N)
    xs = np.arange(20, dtype=np.float32)
    ys = np.array([obj.step(float(x)) for x in xs], dtype=np.float32)
    # First N outputs are zero (buffer initially empty).
    assert np.all(ys[:N] == 0.0), ys[:N]
    # Remaining outputs equal the input N samples earlier.
    assert np.allclose(ys[N:], xs[: len(xs) - N]), (ys, xs)


def test_reset_preserves_length_and_zeros_buffer():
    """reset() must restore zero output but keep the configured length."""
    N = 4
    obj = DelayLine(length=N)
    for x in range(10):
        obj.step(float(x))
    # After 10 steps, buffer is fully populated.
    obj.reset()
    # The first N outputs after reset should be zero — buffer was cleared.
    ys = np.array([obj.step(0.0) for _ in range(N)], dtype=np.float32)
    assert np.all(ys == 0.0), ys
    # And length is still N — the next non-zero input shows up N steps later.
    obj.reset()
    out = [obj.step(1.0)] + [obj.step(0.0) for _ in range(N - 1)] + [obj.step(0.0)]
    assert out[N] == 1.0, out


def test_custom_length():
    """length is a real constructor arg — different values give different delays."""
    for N in (1, 3, 32):
        obj = DelayLine(length=N)
        # Sample N+1 should equal sample 0 (i.e., 0.0).
        ys = [obj.step(float(i)) for i in range(N + 1)]
        # After N+1 samples, the very first input (0.0) hasn't fallen off
        # yet for the (N+1)th output — the (N+1)th output should be the
        # 1st input.
        assert ys[N] == 0.0, (N, ys)
'''


def run(root: Path) -> None:
    from just_makeit._apply import run as jm_apply
    from just_makeit._new import run as jm_new

    # 1. Empty project.
    proj = root / "demo"
    jm_new("demo", proj)

    # 2. Author the whole component in one fragment.
    fragment = root / "delay_line.toml"
    fragment.write_text(_DELAY_LINE_FRAGMENT, encoding="utf-8")

    # 3. Materialize.
    jm_apply(proj, fragment=fragment)

    # 4. Inspect generated artefacts before patching/building.
    core_h = proj / "native" / "inc" / "delay_line" / "delay_line_core.h"
    core_c = proj / "native" / "src" / "delay_line" / "delay_line_core.c"
    h = core_h.read_text(encoding="utf-8")
    c = core_c.read_text(encoding="utf-8")

    # Struct contains: scalar length, scalar idx, opaque taps pointer.
    assert "uint32_t length;" in h
    assert "uint32_t idx;" in h
    assert "float * taps;" in h

    # Scalars get auto-getters/setters; opaque field does NOT.
    assert "delay_line_get_length" in h
    assert "delay_line_get_idx" in h
    assert "delay_line_get_taps" not in h
    assert "delay_line_set_taps" not in h

    # Constructor exposes length and idx as args, but not taps.
    assert "delay_line_create(uint32_t length, uint32_t idx)" in c

    # Lifecycle bodies wired through (TOML's spacing is preserved verbatim).
    assert "obj->taps" in c and "calloc(length" in c
    assert "free(state->taps);" in c
    assert "memset(state->taps, 0," in c

    # destroy_impl runs before free(state).
    body_pos = c.index("free(state->taps);")
    free_pos = c.index("free(state);")
    assert body_pos < free_pos

    # 5. Patch step() to implement the ring-delay.
    _patch_step(core_h)

    # 6. Replace the auto-generated pytest with the real one.
    py_test = proj / "src" / "demo" / "tests" / "test_delay_line.py"
    py_test.write_text(_PYTEST_BODY, encoding="utf-8")

    # 7. The auto-generated C test asserts that reset() restores `length`
    #    to its default — but our reset_impl deliberately preserves it
    #    (it's a runtime-configured config field, not state).  Override
    #    the C test with one that matches the actual semantics.
    c_test = proj / "native" / "tests" / "test_delay_line_core.c"
    c_test.write_text(_C_TEST_BODY, encoding="utf-8")

    # 8. cmake + build + ctest.
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


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("delay_line: PASSED")
