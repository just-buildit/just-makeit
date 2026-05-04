#include "gain/gain_core.h"

gain_state_t *
gain_create(double gain)
{
    gain_state_t *state = malloc(sizeof(*state));
    if (!state)
        return NULL;
    state->gain = gain;
    return state;
}

void
gain_destroy(gain_state_t *state)
{
    free(state);
}

void
gain_reset(gain_state_t *state)
{
    state->gain = 0.0;
}

void
gain_steps(
    gain_state_t *state,
    const float complex    *input,
    float complex          *output,
    size_t                  n)
{
    for (size_t i = 0; i < n; i++)
        output[i] = gain_step(state, input[i]);
}

double
gain_get_gain(const gain_state_t *state)
{
    return state->gain;
}

void
gain_set_gain(gain_state_t *state, double gain)
{
    state->gain = gain;
}
