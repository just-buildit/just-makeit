#define CORR_TAPS   16
#define CORR_LENGTH (CORR_TAPS - 1)
#define CORR_BATCH  8

#ifdef __AVX512F__
JM_FORCEINLINE JM_HOT void
sliding_correlator_step_batch(
    sliding_correlator_state_t *state,
    const float complex        *window,
    float complex              *out)
{
    for (int b = 0; b < CORR_BATCH; b++) {
        float complex acc = 0.0f + 0.0f * I;
        for (int k = 0; k < CORR_TAPS; k++)
            acc += conjf(state->ref[k]) * window[b + CORR_LENGTH - k];
        out[b] = acc;
    }
}
#endif
