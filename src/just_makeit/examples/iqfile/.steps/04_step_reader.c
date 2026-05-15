static inline float complex q15_to_cf32_step(const q15_to_cf32_state_t *state) {
    int16_t pair[2] = {0, 0};
    ssize_t n       = read((int)state->fd, pair, sizeof(pair));
    (void)n;
    return (crealf(0.0f) + cimagf(0.0f) * I) + ((float)pair[0] + (float)pair[1] * I) / state->scale;
}
