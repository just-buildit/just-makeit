/**
 * @brief Reset the receiver.
 *
 * Zeroes both sample clocks (so `elapsed_s` and the carrier phase restart at
 * 0) and clears the resampler's delay line and fractional accumulator.
 *
 * A real numbered list still renders, whether it follows a lead-in:
 *
 * Modes:
 * 1. fast, low quality
 * 2. slow, high quality
 *
 * or stands alone after a blank line:
 *
 * 1. first
 * 2. second
 */
void demo_reset(demo_state_t *state);
