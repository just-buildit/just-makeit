static inline int32_t
iqfile_cf32_to_q15_step (const iqfile_cf32_to_q15_state_t *state,
                         float _Complex x)
{
  float   s = state->scale;
  int16_t i = (int16_t)fmaxf (-s, fminf (s, crealf (x) * s));
  int16_t q = (int16_t)fmaxf (-s, fminf (s, cimagf (x) * s));
  /* Pack I in the low 16 bits, Q in the high 16 bits. */
  return (int32_t)((uint32_t)(uint16_t)i | ((uint32_t)(uint16_t)q << 16));
}
