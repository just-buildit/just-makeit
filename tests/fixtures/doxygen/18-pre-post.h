/**
 * @brief Advance one sample.
 * @pre  reset() has been called.
 * @post The state has advanced by exactly one sample.
 * @invariant The phase stays within [0, 2pi).
 */
float demo_step(demo_state_t *state, float x);
