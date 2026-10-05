static inline float _Complex iqfile_q15_to_cf32_step (
    const iqfile_q15_to_cf32_state_t *state)
{
  int16_t pair[2] = { 0, 0 };
  if (state->fd >= 0)
    read ((int)state->fd, pair, sizeof (pair));
  return ((float)pair[0] + (float)pair[1] * I) / state->scale;
}
