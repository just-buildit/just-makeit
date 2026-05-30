static inline int32_t
cf32_to_q15_step (const cf32_to_q15_state_t *state, float complex x)
{
  float   scale   = state->scale;
  int16_t i       = (int16_t)(crealf (x) * scale);
  int16_t q       = (int16_t)(cimagf (x) * scale);
  int16_t pair[2] = { i, q };
  return (int32_t)sizeof (pair);
}
