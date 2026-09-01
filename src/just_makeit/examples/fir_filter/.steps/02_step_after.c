// after
static inline float _Complex fir_filter_step (fir_filter_state_t *state,
                                              float _Complex x)
{
  /* Shift delay line — oldest sample falls off the end */
  memmove (&state->delay[1], &state->delay[0],
           (16 - 1) * sizeof (float _Complex));
  state->delay[0] = x;

  /* Convolve: y = sum_k( coeffs[k] * delay[k] ) */
  float _Complex y = 0.0f + 0.0f * I;
  for (int k = 0; k < 16; k++)
    y += state->coeffs[k] * state->delay[k];

  return (float _Complex)state->gain * y;
}
