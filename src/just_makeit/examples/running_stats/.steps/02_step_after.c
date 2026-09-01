// after — Welford's online algorithm + running min/max
// Input:  real part = new sample (imaginary part ignored)
// Output: real = current mean, imag = sample variance (0 until n > 1)
// State:  min_val / max_val track the smallest / largest sample seen so far.
static inline float _Complex running_stats_step (running_stats_state_t *state,
                                                 float _Complex x)
{
  double sample = (double)crealf (x);
  state->n++;
  double delta = sample - state->mean;
  state->mean += delta / (double)state->n;
  double delta2 = sample - state->mean;
  state->m2 += delta * delta2;
  if (state->n == 1 || sample < state->min_val)
    state->min_val = sample;
  if (state->n == 1 || sample > state->max_val)
    state->max_val = sample;
  double var = (state->n > 0) ? state->m2 / (double)state->n : 0.0;
  return (float)state->mean + (float)var * I;
}
