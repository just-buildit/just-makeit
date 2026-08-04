/**
 * @brief Select the interpolation mode.
 *
 * The mode decides how a fractional index is resolved:
 *
 *   - nearest: the floor or the next index, whichever `point` is
 *              closer to (an exact 0.5 tie selects the floor index)
 *   - linear:  linear interpolation between the floor index and the
 *              next one, at the fractional position between them
 *
 * @param mode interpolation mode
 */
void demo_set_mode(demo_state_t *state, int mode);
