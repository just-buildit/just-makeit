/**
 * clib_common.h — common C99 types for /*<<package>>*/.
 */
#ifndef /*<<PACKAGE>>*/_CLIB_COMMON_H
#define /*<<PACKAGE>>*/_CLIB_COMMON_H

#include <complex.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/*
 * gh-1148 / gh-1246: why nothing here rewrites the `complex` keyword.
 *
 * In C99 <complex.h> defines `complex` as a macro for `_Complex`. In C++ the
 * same include maps to <complex>, where `complex` is `std::complex` and the
 * macro does not exist -- so a prototype written `float complex x` does not
 * parse from C++, and every complex-typed component header was uncompilable
 * while advertising an `extern "C"` block inviting exactly that.
 *
 * gh-1148 fixed that here, with `#undef complex` / `#define complex _Complex`
 * under __cplusplus. That made the headers parse and broke the callers: a
 * macro cannot be scoped, so it leaked into the consumer's whole translation
 * unit and made `std::complex` unusable in it -- in BOTH include orders. The
 * one thing a C++11 application linking a complex-valued C library reaches
 * for was the one thing it could no longer write.
 *
 * The spelling is fixed at the source instead. jm stores `_Complex` (it is
 * what `_CTYPE_META` is keyed on) and now emits `_Complex` everywhere too, so
 * there is nothing left to rewrite and no macro to leak. `complex` typed by
 * an author is still accepted -- as an INPUT alias in `_types.py`, resolved
 * to `_Complex` before anything is rendered.
 *
 * The TYPE crosses the boundary and the ABI is unchanged, so a C++11
 * translation unit can include these headers and call the C core. C99's
 * complex ARITHMETIC vocabulary does not cross -- `I`, `_Complex_I`,
 * `creal()`, `cimag()` are C only. A C++ caller uses GNU `__real__` /
 * `__imag__`, or converts at the boundary. That is a fact about the two
 * languages, not something jm can paper over, and pretending otherwise by
 * defining `I` here would hand C++ code a macro named `I` -- which collides
 * with essentially everything.
 *
 * `_Complex` in C++ is a GNU/Clang extension. MSVC cannot do this at all;
 * jm's Windows support is MinGW-only and opt-in already (gh-213), so that
 * costs nothing new.
 */

#endif /* /*<<PACKAGE>>*/_CLIB_COMMON_H */
