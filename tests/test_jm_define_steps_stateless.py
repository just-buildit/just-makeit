"""`JM_DEFINE_STEPS` with `LENGTH = 0` compiles without a `state->delay`.

The macro's SIMD path copies the delay-line history around each batch through
`state->delay`. A stateless object has no delay line, and until the history
copy was selected on LENGTH it had to declare a placeholder member anyway,
because the copy named the member even in a loop that ran zero times. That
was true of every aarch64 build (NEON is unconditional) and became true of
every x86-64 build when `jm_simd.h` gained its SSE2 tier -- so an object that
compiled on a portable x86-64 build stopped compiling on the upgrade.

These compile the RENDERED `jm_perf.h` (and the `jm_simd.h` it includes) with
the system C compiler under a flag set that selects a SIMD tier, then run it:

- a stateless object, LENGTH spelled `0` and as a macro expanding to `0`,
  with no `delay` member at all, and its `steps()` equal to `step()` sample
  by sample;
- a two-sample delay line, LENGTH spelled `2` and `(2)` (the parenthesised
  spelling takes the other branch of the zero test), whose `steps()` must
  carry the history across batches and chunks exactly as `step()` does.

The width the build selected is asserted too, so on x86-64 and aarch64 the
SIMD path, not the scalar tail, is what produced the answer.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

import _cbuild

from just_makeit import _incpath as INC
from just_makeit import _render as R

PROGRAM = r"""
#include <stdio.h>
#include <string.h>
#include "jm_perf.h"

#define BATCH JM_SIMD_WIDTH_F32
#define CHUNK 16
#define TWO_LENGTH 0

/* Stateless: no delay member anywhere. */
typedef struct { float g; } gain_state_t;

static float gain_step(gain_state_t *s, float x) { return s->g * x; }
static void gain_step_batch(gain_state_t *s, const float *x, float *y)
{
    for (int k = 0; k < BATCH; k++) y[k] = s->g * x[k];
}
static JM_DEFINE_STEPS(gain, gain_state_t, float, 0, BATCH, CHUNK)

/* The same, LENGTH through a macro that expands to 0. */
typedef struct { float g; } gain2_state_t;

static float gain2_step(gain2_state_t *s, float x) { return s->g * x; }
static void gain2_step_batch(gain2_state_t *s, const float *x, float *y)
{
    for (int k = 0; k < BATCH; k++) y[k] = s->g * x[k];
}
static JM_DEFINE_STEPS(gain2, gain2_state_t, float, TWO_LENGTH, BATCH, CHUNK)

/* y[n] = x[n] + x[n-1] + x[n-2]; delay[0] is the newest sample. */
typedef struct { float delay[2]; } sum3_state_t;

static float sum3_step(sum3_state_t *s, float x)
{
    float y = x + s->delay[0] + s->delay[1];
    s->delay[1] = s->delay[0];
    s->delay[0] = x;
    return y;
}
/* x points into the scratch buffer: x[0..1] is the history, oldest first. */
static void sum3_step_batch(sum3_state_t *s, const float *x, float *y)
{
    (void)s;
    for (int k = 0; k < BATCH; k++) y[k] = x[k + 2] + x[k + 1] + x[k];
}
static JM_DEFINE_STEPS(sum3, sum3_state_t, float, 2, BATCH, CHUNK)

typedef struct { float delay[2]; } sum3p_state_t;

static float sum3p_step(sum3p_state_t *s, float x)
{
    return sum3_step((sum3_state_t *)s, x);
}
static void sum3p_step_batch(sum3p_state_t *s, const float *x, float *y)
{
    sum3_step_batch((sum3_state_t *)s, x, y);
}
static JM_DEFINE_STEPS(sum3p, sum3p_state_t, float, (2), BATCH, CHUNK)

#define N 53

int main(void)
{
    float in[N], got[N], want[N];
    for (int i = 0; i < N; i++) in[i] = (float)(i * 3 - 20);

    int ok_gain, ok_gain2, ok_sum3, ok_sum3p;
    {
        gain_state_t a = {2.0f}, b = {2.0f};
        gain_steps(&a, in, got, N);
        for (int i = 0; i < N; i++) want[i] = gain_step(&b, in[i]);
        ok_gain = memcmp(got, want, sizeof got) == 0;
    }
    {
        gain2_state_t a = {-3.0f}, b = {-3.0f};
        gain2_steps(&a, in, got, N);
        for (int i = 0; i < N; i++) want[i] = gain2_step(&b, in[i]);
        ok_gain2 = memcmp(got, want, sizeof got) == 0;
    }
    {
        /* Two calls, so history also crosses a steps() boundary. */
        sum3_state_t a = {{5.0f, 7.0f}}, b = {{5.0f, 7.0f}};
        sum3_steps(&a, in, got, 21);
        sum3_steps(&a, in + 21, got + 21, N - 21);
        for (int i = 0; i < N; i++) want[i] = sum3_step(&b, in[i]);
        ok_sum3 = memcmp(got, want, sizeof got) == 0
                  && memcmp(&a, &b, sizeof a) == 0;
    }
    {
        sum3p_state_t a = {{5.0f, 7.0f}}, b = {{5.0f, 7.0f}};
        sum3p_steps(&a, in, got, 21);
        sum3p_steps(&a, in + 21, got + 21, N - 21);
        for (int i = 0; i < N; i++) want[i] = sum3p_step(&b, in[i]);
        ok_sum3p = memcmp(got, want, sizeof got) == 0
                   && memcmp(&a, &b, sizeof a) == 0;
    }
    printf("%d %d %d %d %d\n", JM_SIMD_WIDTH_F32, ok_gain, ok_gain2,
           ok_sum3, ok_sum3p);
    return 0;
}
"""

# The flags that select a SIMD tier, per machine: x86-64-v2 is SSE2 without
# AVX2 (the portable build the SSE2 tier exists for); NEON is unconditional
# on aarch64. Any other machine builds the scalar tier, which never names
# state->delay, and the program still has to give the scalar answer.
_ARCH = platform.machine().lower()
_FLAGS = ["-march=x86-64-v2"] if _ARCH in ("x86_64", "amd64") else []
_SIMD = _ARCH in ("x86_64", "amd64", "aarch64", "arm64")


def _headers(root: Path) -> Path:
    """Render jm's two perf headers into *root*, flat, as jm writes them."""
    root.mkdir()
    owner = {"project": {"name": "p", "schema": "7"}}
    ctx = {"package": "p", **INC.ctx_slots(owner)}
    (root / "jm_perf.h").write_text(R.render(R.JM_PERF_H, ctx))
    (root / "jm_simd.h").write_text(R.render(R.JM_SIMD_H, ctx))
    return root


def test_stateless_steps_need_no_delay_member(tmp_path: Path) -> None:
    inc = _headers(tmp_path / "inc")
    src = tmp_path / "t.c"
    src.write_text(PROGRAM)
    exe = _cbuild.build(
        "exe",
        [src],
        _cbuild.output(tmp_path, "t", "exe"),
        flags=["-O2", "-std=c99", *_FLAGS],
        includes=[inc],
    ).path
    out = subprocess.run(
        [str(exe)], check=True, capture_output=True, text=True
    )
    width, gain, gain2, sum3, sum3p = (int(t) for t in out.stdout.split())
    if _SIMD:
        assert width > 1, "the flags selected no SIMD tier: nothing proven"
    assert (gain, gain2) == (1, 1), "stateless steps() != step()"
    assert (sum3, sum3p) == (1, 1), "the delay-line history was lost"
