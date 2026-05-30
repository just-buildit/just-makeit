// after — Welford's online algorithm
// Input:  real part = new sample (imaginary part ignored)
// Output: real = current mean, imag = sample variance (0 until n > 1)
static inline float complex
running_stats_step (running_stats_state_t *state, float complex x)
{
  double sample = (double)crealf (x);
  state->n++;
  double delta = sample - state->mean;
  state->mean += delta / (double)state->n;
  double delta2 = sample - state->mean;
  state->m2 += delta * delta2;
  double var = (state->n > 0) ? state->m2 / (double)state->n : 0.0;
  return (float)state->mean + (float)var * I;
}
