static inline float
filter_step (const filter_state_t *state, float x)
{
  return (float)(state->gain * x);
}
