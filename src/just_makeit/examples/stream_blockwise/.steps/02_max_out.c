/* Implement in native/src/drainer/drainer_core.c — replace the generated stub.
 *
 * Worst-case output for one call: the whole remaining source, independent of
 * the requested count n. The binding uses this to size the NumPy array it
 * allocates for each call.
 */
size_t
stream_blockwise_demo_drainer_run_max_out (
    stream_blockwise_demo_drainer_state_t *state, size_t n)
{
  (void)n;
  return (size_t)state->total;
}
