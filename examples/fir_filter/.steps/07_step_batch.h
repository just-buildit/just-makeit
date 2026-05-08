#define FIR_TAPS   16              /* algorithm:   number of coefficients       */
#define FIR_LENGTH (FIR_TAPS - 1)  /* history:     samples held in delay[]      */
#define FIR_BATCH  8               /* parallelism: AVX-512 complex samples/call */

#ifdef __AVX512F__
JM_FORCEINLINE JM_HOT void
fir_filter_step_batch(
    fir_filter_state_t     *state,
    const float complex    *window,
    float complex          *out)
{
    __m512 vg  = _mm512_set1_ps(state->gain);
    __m512 acc = _mm512_setzero_ps();
    for (int k = 0; k < FIR_TAPS; k++) {
        __m512 vx = _mm512_loadu_ps((const float *)(window + FIR_LENGTH - k));
        acc = _mm512_fmadd_ps(_mm512_set1_ps(state->coeffs[k]), vx, acc);
    }
    _mm512_storeu_ps((float *)out, _mm512_mul_ps(acc, vg));
}
#endif
