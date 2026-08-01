/**
 * @brief Decimate a CF32 block.
 * Each sample is pushed through the integrators.
 *
 * @note **Input amplitude is bounded.** A component beyond the range
 * is clipped at the boundary before filtering.
 *
 * @param x input block
 * @return decimated block
 */
float demo_step(demo_state_t *state, float x);
