JM_FORCEINLINE JM_HOT float
power_est_step (power_est_state_t *state, float complex x)
{
  float re = crealf (x), im = cimagf (x);
  float mag_sq = re * re + im * im;

  /* O(1) recursive update: subtract the oldest sample, add the new one */
  state->sum_sq += (double)(mag_sq - state->delay[state->pos]);
  state->delay[state->pos] = mag_sq;
  state->pos               = (state->pos + 1) & 63; /* window = 64 = 2^6 */

  return (float)(state->sum_sq * (1.0 / 64.0));
}
