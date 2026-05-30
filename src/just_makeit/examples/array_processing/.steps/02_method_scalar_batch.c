/* Hand-written batch companion for ema_quantize().
 * Add this to native/src/ema/ema_core.c after implementing the scalar stub.
 * The Python ext allocates out[] via PyArray_SimpleNew before calling this;
 * the Python caller only passes the input array.
 * This is the right pattern when output count == input count (1:1 rate).
 */
void
ema_quantize_steps (ema_state_t *state, const float *in, uint32_t *out,
                    size_t n)
{
  for (size_t i = 0; i < n; i++)
    out[i] = ema_quantize (state, in[i]);
}
