#include "fir_filter/fir_filter_core.h"

fir_filter_state_t *
fir_filter_create(float gain)
{
    fir_filter_state_t *state = malloc(sizeof(*state));
    if (!state)
        return NULL;
    state->gain = gain;
    memset(state->coeffs, 0, sizeof(state->coeffs));
    memset(state->delay, 0, sizeof(state->delay));
    return state;
}

void
fir_filter_destroy(fir_filter_state_t *state)
{
    free(state);
}

void
fir_filter_reset(fir_filter_state_t *state)
{
    state->gain = 1.0f;
    memset(state->coeffs, 0, sizeof(state->coeffs));
    memset(state->delay, 0, sizeof(state->delay));
}

void
fir_filter_steps(
    fir_filter_state_t *state,
    const float complex    *input,
    float complex          *output,
    size_t                  n)
{
    for (size_t i = 0; i < n; i++)
        output[i] = fir_filter_step(state, input[i]);
}

float
fir_filter_get_gain(const fir_filter_state_t *state)
{
    return state->gain;
}

void
fir_filter_set_gain(fir_filter_state_t *state, float gain)
{
    state->gain = gain;
}

void
fir_filter_get_coeffs(const fir_filter_state_t *state, float *dest)
{
    memcpy(dest, state->coeffs, 16 * sizeof(float));
}

const float *
fir_filter_get_coeffs_view(const fir_filter_state_t *state)
{
    return state->coeffs;
}

void
fir_filter_set_coeffs(fir_filter_state_t *state, const float *src)
{
    memcpy(state->coeffs, src, 16 * sizeof(float));
}

void
fir_filter_get_delay(const fir_filter_state_t *state, float _Complex *dest)
{
    memcpy(dest, state->delay, 16 * sizeof(float _Complex));
}

const float _Complex *
fir_filter_get_delay_view(const fir_filter_state_t *state)
{
    return state->delay;
}

void
fir_filter_set_delay(fir_filter_state_t *state, const float _Complex *src)
{
    memcpy(state->delay, src, 16 * sizeof(float _Complex));
}
