/* Implement in native/src/hbdecim/hbdecim_core.c.
 *
 * The Python ext calls this on every execute() call, with that call's input
 * length, to size the output array.  Return the largest n_out that execute()
 * can produce for n_in inputs.  Here: n_in / 2, rounded up.
 *
 * Without --exact-max-out the binding never allocates fewer than n_in
 * elements: a smaller bound, 0 included, falls back to n_in.
 */
size_t
my_decim_hbdecim_execute_max_out (my_decim_hbdecim_state_t *state, size_t n_in)
{
  (void)state;
  return (n_in + 1) / 2;
}

/* Process n_in samples into out[]; return n_out, the count written.
 * The caller (Python ext) supplies out[], sized from execute_max_out(n_in).
 */
size_t
my_decim_hbdecim_execute (my_decim_hbdecim_state_t *state,
                          const float _Complex *in, size_t n_in,
                          float _Complex *out)
{
  size_t n_out = 0;
  (void)state;
  for (size_t i = 0; i + 1 < n_in; i += 2)
    {
      /* TODO: polyphase half-band implementation */
      out[n_out++] = (in[i] + in[i + 1]) * 0.5f;
    }
  return n_out;
}
