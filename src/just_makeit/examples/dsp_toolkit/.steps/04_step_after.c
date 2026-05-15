static inline float ema_step(ema_state_t *state, float x) {
    float y     = (float)state->alpha * x + (float)(1.0 - state->alpha) * state->prev;
    state->prev = y;
    return y;
}
