/**
 * jm_perf.h — compiler-hint macros for /*<<package>>*/.
 *
 * All macros expand to safe no-ops on unknown compilers.
 * Include freely; zero runtime cost.
 */
#ifndef JM_PERF_H
#define JM_PERF_H

/* Hint that x is almost always true; guides branch-predictor, reducing misprediction stalls. */
#define JM_LIKELY(x)     _JM_LIKELY_(x)
/* Hint that x is almost never true; keeps cold-path code out of the L1 instruction cache. */
#define JM_UNLIKELY(x)   _JM_UNLIKELY_(x)
/* Assert that a pointer does not alias any other; lets the compiler reorder/vectorise freely. */
#define JM_RESTRICT      _JM_RESTRICT_
/* Override inlining heuristics and force inlining; eliminates call overhead on hot functions. */
#define JM_FORCEINLINE   _JM_FORCEINLINE_
/* Align a variable or struct member to n bytes; required for safe SIMD load/store operations. */
#define JM_ALIGNED(n)    _JM_ALIGNED_(n)
/* Mark a function as performance-critical; compiler may place it in a hot section and optimise more aggressively. */
#define JM_HOT           _JM_HOT_

/* GCC / Clang */
#if defined(__GNUC__) || defined(__clang__)
#  define _JM_LIKELY_(x)     __builtin_expect(!!(x), 1)
#  define _JM_UNLIKELY_(x)   __builtin_expect(!!(x), 0)
#  define _JM_RESTRICT_      restrict
#  define _JM_FORCEINLINE_   __attribute__((always_inline)) inline
#  define _JM_ALIGNED_(n)    __attribute__((aligned(n)))
#  define _JM_HOT_           __attribute__((hot))

/* MSVC */
#elif defined(_MSC_VER)
#  define _JM_LIKELY_(x)     (x)
#  define _JM_UNLIKELY_(x)   (x)
#  define _JM_RESTRICT_      __restrict
#  define _JM_FORCEINLINE_   __forceinline
#  define _JM_ALIGNED_(n)    __declspec(align(n))
#  define _JM_HOT_

/* Unknown / strict C99 — safe no-ops */
#else
#  define _JM_LIKELY_(x)     (x)
#  define _JM_UNLIKELY_(x)   (x)
#  define _JM_RESTRICT_      restrict
#  define _JM_FORCEINLINE_   inline
#  define _JM_ALIGNED_(n)
#  define _JM_HOT_
#endif

/* Loop-unroll directive: JM_UNROLL(8) before a for loop instructs GCC/Clang
 * to unroll it exactly n times regardless of the compiler's own cost model.
 * Unlike advisory hints (JM_HOT, JM_LIKELY), this is obeyed unconditionally —
 * a large n on a non-trivial body will bloat code size and hurt icache.
 * Use only on tight, well-measured inner loops with a known iteration count. */
#define JM_UNROLL(n)     _JM_UNROLL_(n)

/* Inform the compiler that ptr is aligned to n bytes; enables SIMD
 * loads/stores without alignment penalties on older ISAs. */
#define JM_ASSUME_ALIGNED(ptr, n)  _JM_ASSUME_ALIGNED_(ptr, n)

/* Software prefetch: rw=0 for read, rw=1 for write; locality 0-3
 * (0=NTA, 3=L1).  No-op on unknown compilers. */
#define JM_PREFETCH(ptr, rw, loc)  _JM_PREFETCH_(ptr, rw, loc)

#if defined(__GNUC__) || defined(__clang__)
#  define _JM_STRINGIFY_(x)           #x
#  define _JM_UNROLL_(n)              _Pragma(_JM_STRINGIFY_(GCC unroll n))
#  define _JM_ASSUME_ALIGNED_(p, n)   __builtin_assume_aligned(p, n)
#  define _JM_PREFETCH_(p, rw, loc)   __builtin_prefetch(p, rw, loc)
#else
#  define _JM_UNROLL_(n)
#  define _JM_ASSUME_ALIGNED_(p, n)   (p)
#  define _JM_PREFETCH_(p, rw, loc)
#endif

/* x86 SIMD intrinsics (SSE through AVX-512) */
#if defined(__x86_64__) || defined(_M_X64) || defined(__i386__) || defined(_M_IX86)
#  include <immintrin.h>
#endif

/* Width-portable SIMD operation macros (JM_VEC_F32, JM_MAC_F32, etc.) */
#include "jm_simd.h"

/* ── JM_DEFINE_STEPS ────────────────────────────────────────────────────────
 *
 * Stamps out <fn>_steps() — the outer dispatch loop — from three separate
 * concerns:
 *
 *   fn        - name prefix; calls fn##_step() and fn##_step_batch()
 *   state_t   - state struct type
 *   sample_t  - per-sample type  (e.g. float complex)
 *   LENGTH    - history depth: samples held in state->delay[]  [algorithm]
 *   BATCH     - SIMD width in samples                          [parallelism]
 *   CHUNK     - samples per scratch-buffer fill                [tuning]
 *
 * Convention: state->delay[0..LENGTH-1] is the delay line, delay[0] = newest.
 * LENGTH, BATCH, and CHUNK must be integer constant expressions (no VLA).
 *
 * Usage (16-tap FIR: TAPS=16, LENGTH=TAPS-1=15):
 *   JM_DEFINE_STEPS(fir_filter, fir_filter_state_t, float complex,
 *                   FIR_LENGTH, FIR_BATCH, FIR_CHUNK)
 *
 * Controllable per-call overrides (gh-240): when a state field is
 * `controllable = true`, the generated fn_step() gains a trailing scalar
 * parameter (e.g. `fn_step(state, x, float gain)`) and fn_steps() must accept
 * and forward it. Use JM_DEFINE_STEPS_EX with two paren-wrapped suffixes — the
 * parameter list and the matching argument list — so the macro threads the
 * control into fn_steps()'s signature, the scalar tail call, and the SIMD batch
 * call alike. Your hand-written fn_step_batch() takes the same trailing param:
 *   JM_DEFINE_STEPS_EX(fir_filter, fir_filter_state_t, float complex,
 *                      FIR_LENGTH, FIR_BATCH, FIR_CHUNK,
 *                      (, float gain), (, gain))
 *   //  -> void fir_filter_steps(state, in, out, n, float gain)
 *   //     fir_filter_step(state, in[_i], gain)
 *   //     fir_filter_step_batch(state, _scratch + _p, output + _i + _p, gain)
 * Plain JM_DEFINE_STEPS forwards to EX with empty suffixes (byte-identical to
 * the pre-gh-240 expansion), so non-controllable perf code is unchanged.
 */

/* Strip one layer of parens from a paren-wrapped token so a comma-containing
 * suffix can be passed as a single macro argument: _JM_EVAL_ (, float g) yields
 * `, float g`; _JM_EVAL_ () yields nothing. */
#define _JM_EVAL_(...) __VA_ARGS__

#if JM_SIMD_WIDTH_F32 > 1
#  define _JM_STEPS_SIMD_(fn, st, samp, LENGTH, BATCH, CHUNK, CARGS)         \
    {                                                                          \
        samp _scratch[(LENGTH) + (CHUNK)];                                    \
        while (_i + (BATCH) <= n) {                                           \
            size_t _blk  = (n - _i < (CHUNK)) ? (n - _i) : (CHUNK);          \
            size_t _main = _blk & ~(size_t)((BATCH) - 1);                    \
            for (int _j = 0; _j < (LENGTH); _j++)                             \
                _scratch[_j] = state->delay[(LENGTH) - 1 - _j];              \
            memcpy(_scratch + (LENGTH), input + _i, _blk * sizeof(samp));    \
            for (size_t _p = 0; _p < _main; _p += (BATCH))                   \
                fn##_step_batch(state, _scratch + _p,                        \
                                output + _i + _p _JM_EVAL_ CARGS);           \
            for (int _j = 0; _j < (LENGTH); _j++)                             \
                state->delay[_j] = _scratch[_main + (LENGTH) - 1 - _j];     \
            _i += _main;                                                      \
        }                                                                      \
    }
#else
#  define _JM_STEPS_SIMD_(fn, st, samp, LENGTH, BATCH, CHUNK, CARGS)  /* scalar: no batching */
#endif

/* Full form: CPARAMS / CARGS are paren-wrapped control suffixes (see header
 * comment). Both () for the no-control case. */
#define JM_DEFINE_STEPS_EX(fn, state_t, sample_t, LENGTH, BATCH, CHUNK,      \
                           CPARAMS, CARGS)                                    \
void fn##_steps(                                                               \
        state_t            *state,                                             \
        const sample_t     *input,                                             \
        sample_t           *output,                                            \
        size_t              n _JM_EVAL_ CPARAMS)                              \
{                                                                              \
    size_t _i = 0;                                                             \
    _JM_STEPS_SIMD_(fn, state_t, sample_t, LENGTH, BATCH, CHUNK, CARGS)       \
    for (; _i < n; _i++)                                                       \
        output[_i] = fn##_step(state, input[_i] _JM_EVAL_ CARGS);            \
}

#define JM_DEFINE_STEPS(fn, state_t, sample_t, LENGTH, BATCH, CHUNK)         \
    JM_DEFINE_STEPS_EX(fn, state_t, sample_t, LENGTH, BATCH, CHUNK, (), ())

#endif /* JM_PERF_H */
