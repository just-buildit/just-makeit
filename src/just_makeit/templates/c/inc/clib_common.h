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
 * gh-1148: keep C99's `complex` spelling meaning what it means in C99, so the
 * `extern "C"` guards in every generated header actually work.
 *
 * In C99 <complex.h> defines `complex` as a macro for `_Complex`. In C++ the
 * same include maps to <complex>, where `complex` is `std::complex` and the
 * macro does not exist -- so a prototype written `float complex x` is a
 * syntax error, and every complex-typed component header was uncompilable
 * from C++ while advertising an `extern "C"` block inviting exactly that.
 *
 * This is the whole of the C++ story and deliberately so: the TYPE crosses
 * the boundary and the ABI is unchanged, so a C++11 translation unit can
 * include these headers and call the C core. C99's complex ARITHMETIC
 * vocabulary does not cross -- `I`, `_Complex_I`, `creal()`, `cimag()` are C
 * only. A C++ caller uses GNU `__real__` / `__imag__`, or converts at the
 * boundary. That is a fact about the two languages, not something jm can
 * paper over, and pretending otherwise by defining `I` here would hand C++
 * code a macro named `I` -- which collides with essentially everything.
 *
 * `_Complex` in C++ is a GNU/Clang extension. MSVC cannot do this at all;
 * jm's Windows support is MinGW-only and opt-in already (gh-213), so that
 * costs nothing new.
 */
#ifdef __cplusplus
#undef complex
#define complex _Complex
#endif

#endif /* /*<<PACKAGE>>*/_CLIB_COMMON_H */
