static inline float
gain_step (const gain_state_t *state, float x)
{
  return state->gain * x;
}
