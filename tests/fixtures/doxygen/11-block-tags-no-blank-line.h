/**
 * @brief Filter one sample.
 * @param x  Input sample.
 * @return   The filtered sample.
 * @note  Not reentrant.
 * @warning Overflows above unity.
 * @see demo_reset
 */
float demo_step(demo_state_t *state, float x);
