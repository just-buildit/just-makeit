"""`jm_simd.h`'s tiers, compiled and run: the width each selects, and sums
that are the scalar sums, exactly.

Each tier CI can select is built with the flags that select it and run on a
host that has it: SSE2 (``x86-64-v2``, the portable x86-64 build), AVX2 +
FMA, AVX-512F (on a CPU that has it) and NEON (aarch64, unconditional).

The SSE2 tier (gh-2651) exists because a portable x86-64 build (no
``-march=native``, so neither AVX2 nor FMA) fell to the one-lane scalar tier,
and every `JM_*` kernel ran a quarter of the width the baseline ISA has. It
sits after AVX2 + FMA, so a build with those keeps eight lanes, and SSE has
no fused multiply-add, so a MAC is a multiply then an add.

`jm_dot_f32` / `jm_dot_f64` run four independent accumulators once
``n >= 4 * W`` (gh-2656), then a one-vector loop, then a scalar tail. Every
length from 0 to ``9 * W + 3`` is checked -- that covers ``4W - 1`` (the
one-vector loop alone), ``4W`` and ``4W + 1`` (the wide loop, then nothing or
a tail element), ``4W + W + 3`` (the wide loop, one more vector, then a
tail) and every other remainder after one or two passes of the wide loop --
plus a long ``1024 + 7``. A slip in a loop bound or in the final combine
drops or repeats elements.

Integer-valued inputs make every sum exact in any association (each product
is at most 48 and no sum exceeds 2^16, far inside float's 24-bit
significand), so the four-accumulator order and the scalar loop's order
agree to the bit and the comparison is equality, not a tolerance.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

import pytest
import _cbuild
from _compilers import NO_AVX512, has_avx512f

HEADER_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "just_makeit"
    / "templates"
    / "c"
    / "inc"
)

# The first line is "W32 W64 hsum32 hsum64" (1 = the tier's horizontal sum
# on one full vector is the scalar sum); every later line names one length
# whose dot product is not the scalar one, so a pass prints exactly one line.
PROGRAM = r"""
#include "jm_simd.h"
#include <stdio.h>

#define N_LONG 1031 /* 1024 + 7: many passes of the wide loop, then a tail */

static float  a[N_LONG], b[N_LONG];
static double c[N_LONG], d[N_LONG];

static void check_f32(int n)
{
    float want = 0.0f;
    for (int i = 0; i < n; i++) want += a[i] * b[i];
    float got = jm_dot_f32(a, b, n);
    if (got != want)
        printf("f32 n=%d got=%.1f want=%.1f\n", n, got, want);
}

static void check_f64(int n)
{
    double want = 0.0;
    for (int i = 0; i < n; i++) want += c[i] * d[i];
    double got = jm_dot_f64(c, d, n);
    if (got != want)
        printf("f64 n=%d got=%.1f want=%.1f\n", n, got, want);
}

int main(void)
{
    /* Small integers of both signs, with periods that do not divide any
     * vector width, so every lane and every accumulator carries a nonzero,
     * distinct partial sum. */
    for (int i = 0; i < N_LONG; i++) {
        a[i] = (float)(i % 17 - 8);
        b[i] = (float)(i % 13 - 6);
        c[i] = (double)(i % 11 - 4);
        d[i] = (double)(i % 7 - 3);
    }
    float  want_h = 0.0f;
    for (int i = 0; i < JM_SIMD_WIDTH_F32; i++) want_h += a[i];
    double want_h64 = 0.0;
    for (int i = 0; i < JM_SIMD_WIDTH_F64; i++) want_h64 += c[i];
    printf("%d %d %d %d\n", JM_SIMD_WIDTH_F32, JM_SIMD_WIDTH_F64,
           JM_HSUM_F32(JM_LOAD_F32(a)) == want_h,
           JM_HSUM_F64(JM_LOAD_F64(c)) == want_h64);
    for (int n = 0; n <= 9 * JM_SIMD_WIDTH_F32 + 3; n++) check_f32(n);
    for (int n = 0; n <= 9 * JM_SIMD_WIDTH_F64 + 3; n++) check_f64(n);
    check_f32(N_LONG);
    check_f64(N_LONG);
    return 0;
}
"""

_ARCH = platform.machine().lower()
_X86 = _ARCH in ("x86_64", "amd64")
_ARM = _ARCH in ("aarch64", "arm64")

#: Each tier: the flags that select it, the f32 / f64 widths it must report,
#: and when this host cannot run it, the allow-listed reason it skips.
TIERS = [
    pytest.param(
        ["-march=x86-64-v2"],
        (4, 2),
        id="sse2",
        marks=pytest.mark.skipif(
            not _X86, reason="the SSE2 tier needs an x86-64 machine"
        ),
    ),
    pytest.param(
        ["-mavx2", "-mfma"],
        (8, 4),
        id="avx2-fma",
        marks=pytest.mark.skipif(
            not _X86, reason="the AVX2 tier needs an x86-64 machine"
        ),
    ),
    pytest.param(
        ["-mavx512f"],
        (16, 8),
        id="avx512f",
        marks=pytest.mark.skipif(
            not (_X86 and has_avx512f()), reason=NO_AVX512
        ),
    ),
    pytest.param(
        [],
        (4, 2),
        id="neon",
        marks=pytest.mark.skipif(
            not _ARM, reason="the NEON tier needs an aarch64 machine"
        ),
    ),
]


def _run(tmp_path: Path, flags: list[str]) -> list[str]:
    src = tmp_path / "t.c"
    src.write_text(PROGRAM)
    exe = _cbuild.build(
        "exe",
        [src],
        _cbuild.output(tmp_path, "t", "exe"),
        flags=["-O2", *flags],
        includes=[HEADER_DIR],
    ).path
    out = subprocess.run(
        [str(exe)], check=True, capture_output=True, text=True
    )
    return out.stdout.splitlines()


@pytest.mark.parametrize(("flags", "widths"), TIERS)
def test_tier_width_and_exact_sums(
    tmp_path: Path, flags: list[str], widths: tuple[int, int]
) -> None:
    """The flags select the tier with these widths -- x86-64-v2 has SSE2 and
    no AVX2, and SSE2 sits after AVX2 + FMA, so it never shadows it -- and
    its horizontal sums, and its dot products at every length the
    four-accumulator loop and its remainders reach, are the scalar sums."""
    head, *bad = _run(tmp_path, flags)
    w32, w64, h, h64 = (int(t) for t in head.split())
    assert (w32, w64) == widths
    assert (h, h64) == (1, 1)
    assert bad == [], "dot products that are not the scalar sum:\n" + (
        "\n".join(bad)
    )
