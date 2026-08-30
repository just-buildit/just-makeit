"""gh-1205: `JM_RESTRICT` is spelled for C++ too.

gh-1148 removed the `extern "C"` wrapper from the umbrella header so a C++
translation unit could include jm's headers. `jm_perf.h` still expanded
`JM_RESTRICT` to the C99 keyword `restrict`, which is not a C++ keyword — so a
`perf = "true"` project's `jm_simd.h` prototypes did not parse, and gh-1148
invited C++ callers into headers that could not be included:

    jm_simd.h:207:36: error: expected ',' or '...' before 'a'
      207 |         const float  * JM_RESTRICT a,

`jm_simd.h`'s own fallback was already right — it reached for `__restrict__`
when nothing had defined the macro. It is only reached when `jm_perf.h` is not
included first, which is not the shipped arrangement, so the correct spelling
sat one `#ifndef` away from the broken one the whole time.

Why the existing sweep did not catch it
---------------------------------------
`test_gh1148_cxx_includable_headers.py` walks **every** header under
`native/inc` with no exemption list, which is the right shape — and it passed,
because its fixture had no `--perf` object and therefore never generated the
two headers that were broken. A sweep is only as good as the tree it walks.

The fix there is the fixture, not a second sweep: that file now scaffolds a
`--perf` object, so its existing test covers these headers. Reverting the macro
turns it red, which is the gate that matters.

What is left here is the part a compile on one box cannot show: that the
spelling is right for the compilers this machine is not.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _render as R  # noqa: E402


def _perf_h() -> str:
    return R.render(R.JM_PERF_H, {"package": "p", "PACKAGE": "P"})


class TestTheCxxSpelling:
    def test_cplusplus_is_answered_before_the_compiler_table(self) -> None:
        """`restrict` is a LANGUAGE question, not a compiler one, so it is
        settled first. Leaving it in the per-compiler table is what produced a
        GCC branch that was right for C and wrong for C++."""
        h = _perf_h()
        assert h.index("#if defined(__cplusplus)") < h.index(
            "/* GCC / Clang */"
        ), h

    def test_gcc_and_clang_get_double_underscore_restrict(self) -> None:
        h = _perf_h()
        assert "#    define JM_RESTRICT_IMPL    __restrict__" in h, h

    def test_msvc_gets_its_own(self) -> None:
        """MSVC spells it `__restrict` in C++ — the one compiler this box
        cannot check by compiling."""
        h = _perf_h()
        block = h[h.index("#if defined(__cplusplus)") :]
        block = block[: block.index("/* GCC / Clang */")]
        assert "_MSC_VER" in block, block
        assert "#    define JM_RESTRICT_IMPL    __restrict\n" in block, block

    def test_c_still_gets_the_c99_keyword(self) -> None:
        """The point is to add C++, not to give up the C99 optimisation hint
        — `__restrict__` would work on GCC but is not what a C project should
        be handed."""
        h = _perf_h()
        assert "#  define JM_RESTRICT_IMPL      restrict" in h, h


class TestThereIsOneAnswer:
    def test_the_compiler_table_no_longer_defines_it(self) -> None:
        """It was defined three times — GCC, MSVC, and a strict-C99
        fallback — and the language question cuts across all three. Three
        definitions that must agree is the shape that drifts; now the
        `__cplusplus` block is the only one."""
        h = _perf_h()
        defs = re.findall(r"^#\s*define JM_RESTRICT_IMPL", h, re.M)
        assert len(defs) == 3, f"expected the three inside one block: {defs}"
        for marker in ("/* GCC / Clang */", "/* MSVC */"):
            block = h[h.index(marker) :]
            block = block[: block.index("#  define JM_HOT_IMPL")]
            assert "JM_RESTRICT_IMPL" not in block, (marker, block)


class TestTheSimdFallbackStillAgrees:
    def test_it_was_right_all_along(self) -> None:
        """`jm_simd.h` defines the macro itself when nothing else has. It
        already reached for `__restrict__`, which is why the two headers
        disagreed — and it has to keep doing so, or the standalone include
        path breaks the way the paired one did."""
        simd = R.render(R.JM_SIMD_H, {"package": "p", "PACKAGE": "P"})
        assert "#ifndef JM_RESTRICT" in simd, simd
        assert "#    define JM_RESTRICT __restrict__" in simd, simd
