#define FIR_TAPS 16 /* algorithm:   number of coefficients       */
#define FIR_LENGTH                                                            \
  (FIR_TAPS - 1) /* history:     samples held in delay[]      */
/* JM_SIMD_WIDTH_F32 floats = JM_SIMD_WIDTH_F32/2 complex samples per batch.
 * On scalar targets (width=1) this is 0; _JM_STEPS_SIMD_ is a no-op there. */
#define FIR_BATCH (JM_SIMD_WIDTH_F32 / 2)

#if JM_SIMD_WIDTH_F32 > 1
JM_FORCEINLINE JM_HOT void
fir_filter_step_batch (fir_filter_state_t *state, const float _Complex *window,
                       float _Complex *out)
{
  JM_VEC_F32 acc = JM_ZERO_F32 ();
  for (int k = 0; k < FIR_TAPS; k++)
    JM_MAC_F32 (acc, (const float *)(window + FIR_LENGTH - k),
                state->coeffs[k]);
  JM_STORE_F32 ((float *)out, JM_MUL_F32 (acc, JM_SPLAT_F32 (state->gain)));
}
#endif
