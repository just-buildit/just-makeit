#define CORR_TAPS 16
#define CORR_LENGTH (CORR_TAPS - 1)
/* Complex samples per step_batch() call.  Same derivation as FIR_BATCH:
 * JM_SIMD_WIDTH_F32/2 ≥ 1 on AVX2/AVX-512; _JM_STEPS_SIMD_ is a no-op
 * on scalar targets so the value there doesn't matter. */
#define CORR_BATCH (JM_SIMD_WIDTH_F32 / 2)

/* No ISA guard needed — _JM_STEPS_SIMD_ only calls this when width > 1.
 * The inner loop is auto-vectorisable; the compiler picks the best ISA. */
JM_FORCEINLINE JM_HOT void sliding_correlator_step_batch(sliding_correlator_state_t *state,
                                                         const float complex        *window,
                                                         float complex              *out) {
    for (int b = 0; b < CORR_BATCH; b++) {
        float complex acc = 0.0f + 0.0f * I;
        for (int k = 0; k < CORR_TAPS; k++)
            acc += conjf(state->ref[k]) * window[b + CORR_LENGTH - k];
        out[b] = acc;
    }
}
