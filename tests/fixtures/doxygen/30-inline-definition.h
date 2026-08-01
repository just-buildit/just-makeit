/**
 * @brief Filter one sample, defined inline in the header.
 * @param x  Input sample.
 * @return   The filtered sample.
 */
JM_FORCEINLINE JM_HOT float
demo_step(demo_state_t *state, float x)
{
    return x * state->gain;
}
