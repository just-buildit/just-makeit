/* Batch companion for my_arrays_ema_quantize(): the body of the stub that
 * `just-makeit method ... --batch` appended to native/src/ema/ema_core.c.
 * The Python ext allocates out[] (or takes the caller's out= array) before
 * calling this; the Python caller only passes the input array.
 * This is the right pattern when output count == input count (1:1 rate).
 */
void
my_arrays_ema_quantize_steps (my_arrays_ema_state_t *state, const float *in,
                              size_t n, uint32_t *out)
{
  for (size_t i = 0; i < n; i++)
    out[i] = my_arrays_ema_quantize (state, in[i]);
}
