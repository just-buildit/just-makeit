"""gh-1246: a C++11 caller can use `std::complex` and link the real library.

gh-1148 made every generated header parse as C++ by restoring C99's spelling
under `__cplusplus`, with `#undef complex` / `#define complex _Complex` in
`clib_common.h`. A macro cannot be scoped, so it did not stop at jm's headers:
it leaked into the consumer's whole translation unit and rewrote the `complex`
in `std::complex`, which is the one name a C++11 application linking a
complex-valued C library actually reaches for::

    error: template argument 1 is invalid
        std::vector<std::complex<float> > v;
                                        ^
    clib_common.h:39:17: error: expected unqualified-id before '_Complex'

Include order is irrelevant, and pinning that is half the point of this file —
a macro defined by jm's header poisons everything after it, and a macro
defined before jm's header is what `#undef complex` then destroys. Both orders
are tested because "put the include first" is the plausible wrong fix.

jm now emits the `_Complex` spelling everywhere (it is already what
`_CTYPE_META` is keyed on), so there is no macro and nothing to leak.

Three properties, and the gate needs all of them AT ONCE — gh-1148's two gates
each have some, which is why the pair could not see this:

1. a C++11 TU that includes a jm header **and** compiles a `std::complex`.
   gh-1148's sweep compiles each header alone, where the macro is harmless;
   its linked caller is written in `float _Complex` + `__real__`/`__imag__`.
2. **both** include orders.
3. linking the **real built** `lib<pkg>.a`, not a hand-compiled `_core.o`.
   Per `_libwiring.py` a component reaches the combined library only through
   an explicit `target_sources` line, and the C consumer is the ONLY observer
   of whether it got there — the whole Python suite passes with the symbol in
   no shipped library at all.

`cplx_step` is inline in the header, so the linked test deliberately calls
`cplx_create` / `cplx_steps` / `cplx_destroy` instead: those live in the
library, so the link is load-bearing rather than decorative.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from just_makeit import _cli_object
from just_makeit._new import run as new_run


def _skip_reason() -> str | None:
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    if not any(shutil.which(c) for c in ("c++", "g++", "clang++")):
        return "no C++ compiler found"
    return None


_SKIP = _skip_reason()
_CXX = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")

# jm's header first, then <complex> -- the macro poisons what follows.
_JM_FIRST = '#include "cplx/cplx_core.h"\n#include <complex>\n'
# <complex> first -- `#undef complex` then destroys what is already there.
_JM_SECOND = '#include <complex>\n#include "cplx/cplx_core.h"\n'

_USES_STD_COMPLEX = """\
#include <vector>
int main() {
    std::vector<std::complex<float> > v;
    v.push_back(std::complex<float>(3.0f, 4.0f));
    return (int)v.at(0).real() - 3;
}
"""


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """A built project with one complex component, and its real C library."""
    if _SKIP:
        pytest.skip(_SKIP)

    root = tmp_path_factory.mktemp("gh1246") / "cxxlib"
    new_run("cxxlib", root)

    # _cli_object.run resolves the project from the cwd. The default arg_type
    # is `float _Complex`, which is the shape that carries the defect.
    cwd = os.getcwd()
    try:
        os.chdir(root)
        _cli_object.run(["cplx", "--state", "gain:double:1.0"])
    finally:
        os.chdir(cwd)

    build = root / "build"
    cfg = subprocess.run(
        ["cmake", "-S", str(root), "-B", str(build)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert cfg.returncode == 0, f"cmake configure failed:\n{cfg.stderr}"

    bld = subprocess.run(
        ["cmake", "--build", str(build)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert bld.returncode == 0, f"build failed:\n{bld.stdout}\n{bld.stderr}"

    static = build / "libcxxlib.a"
    assert static.is_file(), (
        "the project's combined static C library was not built; "
        f"build/ holds: {sorted(p.name for p in build.iterdir())}"
    )
    return root, build, static


class TestACxx11TuCanUseStdComplex:
    """Property 1 and 2: it compiles, in either include order."""

    @pytest.mark.parametrize(
        "order, includes",
        [("jm_header_first", _JM_FIRST), ("std_complex_first", _JM_SECOND)],
        ids=["jm-header-first", "std-complex-first"],
    )
    def test_std_complex_survives_the_jm_include(
        self, built, tmp_path, order, includes
    ):
        root, _, __ = built
        src = tmp_path / f"{order}.cpp"
        src.write_text(includes + _USES_STD_COMPLEX, encoding="utf-8")
        r = subprocess.run(
            [
                _CXX,
                "-std=c++11",
                "-Wall",
                "-c",
                str(src),
                f"-I{root / 'native' / 'inc'}",
                "-o",
                str(tmp_path / f"{order}.o"),
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert r.returncode == 0, (
            f"a C++11 TU using std::complex failed to compile with the jm "
            f"header included {order.replace('_', ' ')}:\n{r.stderr}"
        )


class TestItLinksTheRealLibraryAndRuns:
    """Property 3: the artifact a C++ consumer actually links.

    Compiling is not linking and linking is not running, so this does all
    three. It calls the library-resident lifecycle functions rather than the
    header-inline `step()`, so an unwired core fails here as `undefined
    reference` — which is the only place it can be observed.
    """

    def test_a_cxx11_app_links_libcxxlib_and_runs(self, built, tmp_path):
        root, _, static = built
        app = tmp_path / "app.cpp"
        app.write_text(
            '#include "cplx/cplx_core.h"\n'
            "#include <complex>\n"
            "#include <vector>\n"
            "#include <cstdio>\n"
            "int main() {\n"
            "    std::vector<std::complex<float> > xs;\n"
            "    xs.push_back(std::complex<float>(3.0f, 4.0f));\n"
            "    cplx_state_t *o = cplx_create(2.0);\n"
            "    if (!o) return 1;\n"
            "    float _Complex in[1], out[1];\n"
            "    __real__ in[0] = xs.at(0).real();\n"
            "    __imag__ in[0] = xs.at(0).imag();\n"
            "    cplx_steps(o, in, out, 1);\n"
            "    std::complex<float> y(__real__ out[0], __imag__ out[0]);\n"
            '    std::printf("%.1f %.1f\\n", (double)y.real(),\n'
            "                (double)y.imag());\n"
            "    cplx_destroy(o);\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        exe = tmp_path / "app"
        link = subprocess.run(
            [
                _CXX,
                "-std=c++11",
                "-Wall",
                str(app),
                f"-I{root / 'native' / 'inc'}",
                str(static),
                "-lm",
                "-o",
                str(exe),
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert link.returncode == 0, (
            f"a C++11 app failed to build against the real library:\n"
            f"{link.stderr}"
        )
        out = subprocess.run(
            [str(exe)], capture_output=True, text=True, timeout=120
        )
        assert out.returncode == 0, out.stderr
        # The scaffolded steps() is a passthrough, so the value must survive
        # the crossing unchanged. A header that parsed while the ABI
        # disagreed would link and print garbage.
        assert out.stdout.strip() == "3.0 4.0", out.stdout


class TestTheLeakingMacroIsGone:
    """No compiler needed, which is the point.

    The compiled tests above skip wherever a C++ compiler is absent. This one
    names the exact mechanism, so a reader restoring the `#define` for the
    gh-1148 reason is told here rather than by a downstream's build.
    """

    def test_clib_common_defines_no_complex_macro(self):
        """Anchored to a real directive, not to the substring.

        The template CARRIES the removed mechanism in its own comment, to
        explain why it must not come back — so a bare
        ``"#define complex" in CLIB_COMMON_H`` matches the prose and fails on
        a correct file. The directive has to be matched at the start of a
        line; the comment lines begin with ``*``.
        """
        import re

        from just_makeit._render import CLIB_COMMON_H

        directive = re.compile(r"^\s*#\s*(define|undef)\s+complex\b", re.M)
        hit = directive.search(CLIB_COMMON_H)
        assert hit is None, (
            "clib_common.h defines a `complex` macro again "
            f"({hit.group(0).strip()!r}). It cannot be scoped, so it leaks "
            "into every C++ consumer and breaks std::complex (gh-1246). jm "
            "emits the `_Complex` spelling instead; there is nothing left to "
            "rewrite."
        )

    def test_the_emitters_spell_complex_the_c_plus_plus_safe_way(self):
        """The spelling is a property of what jm renders, not of a helper.

        `_ctype_display` used to rewrite `_Complex` back to the `<complex.h>`
        macro on the way out, across 104 call sites. It is gone; this fails
        if any equivalent comes back.
        """
        from just_makeit import _types

        assert not hasattr(_types, "_ctype_display")
