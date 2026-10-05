/* Implement in native/src/hbdecim/hbdecim_core.c.
 *
 * Two output arrays: primary (filtered samples) and secondary (overflow
 * flags). Both are allocated per call by the ext, NumPy-owned, sized from
 * execute_ovf_max_out(n_in). Return the actual count written to both arrays.
 */
size_t
my_decim_hbdecim_execute_ovf_max_out (my_decim_hbdecim_state_t *state,
                                      size_t                    n_in)
{
  (void)state;
  return (n_in + 1) / 2;
}

size_t
my_decim_hbdecim_execute_ovf (my_decim_hbdecim_state_t *state,
                              const float _Complex *in, size_t n_in,
                              float _Complex *out,  /* primary */
                              uint8_t        *out1) /* secondary: overflow */
{
  size_t n_out = 0;
  (void)state;
  for (size_t i = 0; i + 1 < n_in; i += 2)
    {
      float _Complex y = (in[i] + in[i + 1]) * 0.5f;
      out[n_out]       = y;
      out1[n_out]      = (cabsf (y) > 1.0f) ? 1 : 0;
      n_out++;
    }
  return n_out;
}
