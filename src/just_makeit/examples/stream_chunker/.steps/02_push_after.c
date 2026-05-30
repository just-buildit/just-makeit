size_t
chunker_push_max_out (chunker_state_t *state)
{
  /* buf[] holds 256 samples — the absolute output ceiling.
   * Push at most (256 - n_buf) samples per call to stay safe. */
  (void)state;
  return 256;
}

size_t
chunker_push (chunker_state_t *state, const float complex *in, size_t n_in,
              float complex *out)
{
  size_t n_out = 0;
  for (size_t i = 0; i < n_in; i++)
    {
      state->buf[state->n_buf++] = in[i];
      if (state->n_buf >= state->chunk_size)
        {
          memcpy (out + n_out, state->buf,
                  (size_t)state->chunk_size * sizeof (float complex));
          n_out += (size_t)state->chunk_size;
          state->n_buf = 0;
        }
    }
  return n_out;
}