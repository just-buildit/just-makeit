static inline double
my_vs_scale_step (const my_vs_scale_state_t *state, double x)
{
  return state->factor * x;
}
