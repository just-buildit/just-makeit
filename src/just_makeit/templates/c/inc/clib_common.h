/**
 * clib_common.h — common C99 types for /*<<package>>*/.
 */
#ifndef /*<<PACKAGE>>*/_CLIB_COMMON_H
#define /*<<PACKAGE>>*/_CLIB_COMMON_H

#if defined(_MSC_VER) && !defined(__cplusplus)
/* gh-1368: the MSVC ABI. See the note at the end of this file. */
#if !defined(__clang__)
#error "C99 _Complex is required; on Windows build with clang-cl, not cl.exe"
#endif
/* The UCRT's own <complex.h> FIRST, so its `_Fcomplex` declarations are
 * already parsed -- and its include guard already set -- before the C99 names
 * are remapped below. Then any later #include <complex.h>, from numpy's
 * npy_common.h say, is a no-op instead of re-declaring `crealf(_Fcomplex)`
 * through the macros. */
#include <complex.h>
#include <math.h>
/* `I` only: `_Complex_I` is reserved to the implementation (gh-944), and
 * nothing jm writes spells it. Nor is `complex` redefined, although the UCRT
 * maps it to its `struct _complex`: jm spells `_Complex` everywhere, and a
 * `complex` macro here would leak into C++ consumers (gh-1246). */
#undef I
#define I (__extension__ 1.0fi)
#define crealf __builtin_crealf
#define cimagf __builtin_cimagf
#define conjf __builtin_conjf
#define creal __builtin_creal
#define cimag __builtin_cimag
#define conj __builtin_conj
static inline float
jm_cabsf(float _Complex z)
{
    return hypotf(__builtin_crealf(z), __builtin_cimagf(z));
}
static inline float
jm_cargf(float _Complex z)
{
    return atan2f(__builtin_cimagf(z), __builtin_crealf(z));
}
static inline float _Complex
jm_cexpf(float _Complex z)
{
    float e = expf(__builtin_crealf(z));
    return __builtin_complex(e * cosf(__builtin_cimagf(z)),
                             e * sinf(__builtin_cimagf(z)));
}
static inline double
jm_cabs(double _Complex z)
{
    return hypot(__builtin_creal(z), __builtin_cimag(z));
}
static inline double
jm_carg(double _Complex z)
{
    return atan2(__builtin_cimag(z), __builtin_creal(z));
}
static inline double _Complex
jm_cexp(double _Complex z)
{
    double e = exp(__builtin_creal(z));
    return __builtin_complex(e * cos(__builtin_cimag(z)),
                             e * sin(__builtin_cimag(z)));
}
#define cabsf jm_cabsf
#define cargf jm_cargf
#define cexpf jm_cexpf
#define cabs jm_cabs
#define carg jm_carg
#define cexp jm_cexp
#else
#include <complex.h>
#endif
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
 * `_Complex` in C++ is a GNU/Clang extension. MSVC's cl.exe cannot do this at
 * all; clang-cl, the Windows compiler jm supports (gh-1368), is Clang and
 * can.
 */

/*
 * gh-1368: why the MSVC ABI gets its own complex surface above.
 *
 * MSVC's cl.exe has no `_Complex` at all, hence the #error. clang-cl has the
 * keyword while targeting the MSVC ABI, so the LANGUAGE half is free; the
 * LIBRARY half is not. The UCRT's <complex.h> is built on `_Fcomplex` /
 * `_Dcomplex` STRUCTS -- `crealf` takes one, and `I` is one -- so jm's own
 * `crealf(v)` and `x * I` were 154 compile errors on the first clang-cl run.
 * The C99 names are therefore mapped onto clang builtins, and `cabsf` /
 * `cargf` / `cexpf` onto REAL-valued libm, so no struct-typed CRT function is
 * ever called. That is the surface doppler measured for its own dp_complex.h:
 * a kernel through it needs only plain-float UCRT exports.
 *
 * `#include "clib_common.h"` is the one way generated code reaches complex
 * math: every jm-written file includes this header rather than <complex.h>,
 * so the mapping cannot be skipped by include order. Anything past this
 * surface (csqrtf, cpowf, ...) still has the UCRT's struct signature under
 * clang-cl and fails to compile, rather than calling the wrong ABI.
 */

#endif /* /*<<PACKAGE>>*/_CLIB_COMMON_H */
