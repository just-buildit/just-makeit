/* Implement in native/src/drainer/drainer_core.c — replace the generated stub.
 *
 * Emit up to n of the remaining samples as an ascending complex ramp, advance
 * pos, and return the count. The empty return once drained (pos == total) is
 * what makes stream() terminate.
 */
size_t
drainer_run (drainer_state_t *state, size_t n, float _Complex *out)
{
  int32_t avail = state->total - state->pos;
  if (avail < 0)
    avail = 0;
  size_t k = (size_t)avail < n ? (size_t)avail : n;
  for (size_t i = 0; i < k; i++)
    out[i] = (float _Complex) (float)(state->pos + (int32_t)i);
  state->pos += (int32_t)k;
  return k;
}
