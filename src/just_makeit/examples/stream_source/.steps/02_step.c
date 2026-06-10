/* Implement in native/inc/ramp/ramp_core.h — replace the generated stub.
 *
 * A free-running source: emit the current value, then advance it. `value`
 * and `step_inc` are state fields, so each call resumes where the last one
 * left off — exactly what stream() drives, block by block.
 */
static inline float
ramp_step (ramp_state_t *state)
{
  const float out = state->value;
  state->value += state->step_inc;
  return out;
}
