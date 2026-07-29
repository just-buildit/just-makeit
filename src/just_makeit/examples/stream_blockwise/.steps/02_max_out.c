/* Implement in native/src/drainer/drainer_core.c — replace the generated stub.
 *
 * Worst-case output for one call: the whole remaining source. The binding
 * uses this to size the NumPy array it allocates for each call.
 */
size_t
drainer_run_max_out (drainer_state_t *state)
{
  return (size_t)state->total;
}
