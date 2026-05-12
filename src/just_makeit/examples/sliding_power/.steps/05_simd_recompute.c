/* Add to power_est_core.c — SIMD recompute from delay line.
 *
 * Uses jm_simd.h macros: JM_ADD_F32, JM_LOAD_F32, JM_HSUM_F32, JM_UNROLL.
 * Compiles to AVX-512, AVX2, or scalar depending on -march flags.
 *
 * Call every ~1000 samples to correct floating-point drift in sum_sq.
 */
static inline float
power_est_recompute(power_est_state_t *state)
{
    JM_VEC_F32 acc = JM_ZERO_F32();
    JM_UNROLL(4)
    for (int k = 0; k < 64; k += JM_SIMD_WIDTH_F32)
        acc = JM_ADD_F32(acc, JM_LOAD_F32(state->delay + k));
    float power = JM_HSUM_F32(acc) * (1.0f / 64.0f);

    /* Sync the double accumulator so both paths agree */
    state->sum_sq = (double)power * 64.0;
    return power;
}
