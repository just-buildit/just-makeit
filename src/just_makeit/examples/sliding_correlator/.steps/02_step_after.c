// after
static inline float complex sliding_correlator_step(sliding_correlator_state_t *state,
                                                    float complex               x) {
    memmove(&state->delay[1], &state->delay[0], 15 * sizeof(float complex));
    state->delay[0] = x;

    float complex acc = 0.0f + 0.0f * I;
    for (int k = 0; k < 16; k++)
        acc += conjf(state->ref[k]) * state->delay[k];
    return acc;
}
