"""`jm_simd.h`'s SSE2 tier: four float lanes on every x86-64 build.

Before it, a portable x86-64 build (no `-march=native`, so neither AVX2 nor
FMA) fell to the one-lane scalar tier, and every `JM_*` kernel ran a quarter
of the width the baseline ISA has. The tier sits after AVX2 + FMA, so a build
with those keeps eight lanes, and SSE has no fused multiply-add, so a MAC is
a multiply then an add.

These compile the shipped header with the system C compiler and run it:
the tier a flag set selects, and that the tier's horizontal sums and the dot
product it drives give the scalar answer. Integer-valued inputs make every sum
exact, so the comparison is equality, not a tolerance.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

import pytest
import _cbuild

HEADER_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "just_makeit"
    / "templates"
    / "c"
    / "inc"
)

PROGRAM = r"""
#include "jm_simd.h"
#include <stdio.h>

int main(void)
{
    float  a[11], b[11];
    double c[5],  d[5];
    float  want_dot = 0.0f;
    for (int i = 0; i < 11; i++) {
        a[i] = (float)(i + 1);
        b[i] = (float)(2 * i - 7);
        want_dot += a[i] * b[i];
    }
    double want_dot64 = 0.0;
    for (int i = 0; i < 5; i++) {
        c[i] = (double)(3 * i + 1);
        d[i] = (double)(i - 2);
        want_dot64 += c[i] * d[i];
    }
    /* The tier's own horizontal sum on one full vector. */
    float  want_h = 0.0f;
    for (int i = 0; i < JM_SIMD_WIDTH_F32; i++) want_h += a[i];
    double want_h64 = 0.0;
    for (int i = 0; i < JM_SIMD_WIDTH_F64; i++) want_h64 += c[i];
    float  got_h   = JM_HSUM_F32(JM_LOAD_F32(a));
    double got_h64 = JM_HSUM_F64(JM_LOAD_F64(c));
    printf("%d %d %d %d %d\n", JM_SIMD_WIDTH_F32,
           jm_dot_f32(a, b, 11) == want_dot,
           jm_dot_f64(c, d, 5) == want_dot64,
           got_h == want_h, got_h64 == want_h64);
    return 0;
}
"""


pytestmark = pytest.mark.skipif(
    platform.machine().lower() not in ("x86_64", "amd64"),
    reason="the SSE2 tier needs an x86-64 machine",
)


def _run(tmp_path: Path, flags: list[str]) -> list[int]:
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
    return [int(t) for t in out.stdout.split()]


def test_a_portable_build_runs_four_lanes(tmp_path: Path) -> None:
    """x86-64-v2 has SSE2 and no AVX2: the SSE2 tier, four float lanes, and
    its sums are the scalar sums."""
    width, dot, dot64, h, h64 = _run(tmp_path, ["-march=x86-64-v2"])
    assert width == 4
    assert (dot, dot64, h, h64) == (1, 1, 1, 1)


def test_avx2_with_fma_keeps_eight_lanes(tmp_path: Path) -> None:
    """The SSE2 tier sits after AVX2 + FMA, so it never shadows it."""
    width, dot, dot64, h, h64 = _run(tmp_path, ["-mavx2", "-mfma"])
    assert width == 8
    assert (dot, dot64, h, h64) == (1, 1, 1, 1)
