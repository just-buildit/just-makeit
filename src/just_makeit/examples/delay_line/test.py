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
from just_makeit._pyfmt import flatten_prose


def _cmd(args, cwd):
    r = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=600
    )
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
_STEP_PATCH_OLD = "    (void)state; /* TODO: implement using state variables */\n    return (float)x;"
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
        raise AssertionError(
            f"step() stub not found in {core_h} — template changed?"
        )
    core_h.write_text(
        text.replace(_STEP_PATCH_OLD, _STEP_PATCH_NEW), encoding="utf-8"
    )


# Enrich the sacred header's Doxygen so the generated .pyi class docstring
# reads as a real sentence instead of jm's generic "<Type> component."
# fallback.  create()'s @brief becomes the class SUMMARY; a follow-up
# `jm apply` re-derives the .pyi from these comments.  This is an opaque-state
# object with no custom `jm method`, so there is no runnable doctest — the
# enrichment is the class summary (plus a real @param for the C API docs).
_DOXY_SUMMARY = (
    "Create a circular delay line: a ring buffer that delays each input "
    "sample by a runtime-configurable number of samples."
)
_DOXY_BRIEF_OLD = "@brief Create a delay_line instance."
_DOXY_BRIEF_NEW = f"@brief {_DOXY_SUMMARY}"

# `length` is a scalar state field that doubles as a constructor argument, so
# the .pyi's Parameters section is derived from the manifest (generic
# "length state variable." text), not from this @param — but the header is the
# single source of truth for the Doxygen C API docs, so give it a real
# description there too.
_DOXY_PARAM_DESC = (
    "Delay length in samples; sizes the heap-allocated ring buffer "
    "(chosen at construction, preserved across reset)."
)
_DOXY_PARAM_OLD = "@param length  Initial length (default: 16)."
_DOXY_PARAM_NEW = f"@param length  {_DOXY_PARAM_DESC}"


def _enrich_doxygen(core_h: Path) -> None:
    text = core_h.read_text(encoding="utf-8")
    if _DOXY_BRIEF_NEW in text:
        return  # already enriched
    for old in (_DOXY_BRIEF_OLD, _DOXY_PARAM_OLD):
        if old not in text:
            raise AssertionError(
                f"scaffold Doxygen not found in {core_h}: {old!r} "
                "— template changed?"
            )
    text = text.replace(_DOXY_BRIEF_OLD, _DOXY_BRIEF_NEW, 1)
    text = text.replace(_DOXY_PARAM_OLD, _DOXY_PARAM_NEW, 1)
    core_h.write_text(text, encoding="utf-8")


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

from delay_line_demo import DelayLine


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
    proj = root / "delay_line_demo"
    jm_new("delay_line_demo", proj)

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

    # 4b. Enrich the sacred header with a real Doxygen class summary, then
    #     regenerate the glue so the .pyi class docstring reads as a real
    #     sentence instead of jm's generic "DelayLine component." fallback.
    #     `jm apply` re-derives the .pyi from the edited header; the sacred
    #     header itself (including our step() patch below) is left untouched.
    _enrich_doxygen(core_h)
    jm_apply(proj)

    pyi = (proj / "src" / "delay_line_demo" / "delay_line.pyi").read_text(
        encoding="utf-8"
    )
    # gh-744: the summary wraps when it does not fit on one line.
    assert _DOXY_SUMMARY in flatten_prose(pyi), (
        "enriched class summary missing from .pyi:\n" + pyi[:400]
    )
    assert "DelayLine component." not in pyi, (
        "generic class-summary fallback still present in .pyi"
    )
    # The @param enrichment lives in the C header (Doxygen C API docs); the
    # .pyi Parameters are manifest-derived so it does not surface there.
    h_enriched = core_h.read_text(encoding="utf-8")
    assert _DOXY_PARAM_DESC in h_enriched, (
        "enriched @param missing from header"
    )

    # 5. Patch step() to implement the ring-delay.
    _patch_step(core_h)

    # 6. Replace the auto-generated pytest with the real one.
    py_test = proj / "src" / "delay_line_demo" / "tests" / "test_delay_line.py"
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
