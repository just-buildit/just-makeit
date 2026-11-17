- **`jm_dot_f32` / `jm_dot_f64` keep four accumulators, so a long dot
    product is no longer latency-bound** (gh-2656). With one vector
    accumulator every multiply-add waited on the previous add, at every
    tier. From `n >= 4 * JM_SIMD_WIDTH` the main loop now runs four
    independent chains, combined pairwise at the end, then the one-vector
    loop and the scalar tail; a shorter dot runs the old single-accumulator
    loop only. Measured at `n = 4096`, `-O2`: about 3x faster on SSE2 and
    AVX2, 1.4-1.6x on AVX-512, 3.8x on NEON. The sum is associated
    differently, so a result can differ from the previous release's in the
    last place, as the SSE2 tier's unfused `JM_FMA` could (gh-2651).
