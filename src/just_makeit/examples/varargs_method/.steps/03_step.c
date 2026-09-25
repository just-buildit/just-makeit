static inline float
va_filter_filter_step (const va_filter_filter_state_t *state, float x)
{
  return (float)(state->gain * x);
}
