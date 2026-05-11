#include "engine/engine_core.h"

engine_state_t *
engine_create(double gain)
{
    engine_state_t *state = malloc(sizeof(*state));
    if (!state)
        return NULL;
    state->gain = gain;
    return state;
}

void
engine_destroy(engine_state_t *state)
{
    free(state);
}

void
engine_reset(engine_state_t *state)
{
    state->gain = 1.0;
}

void
engine_steps(
    engine_state_t *state,
    const float complex   *input,
    float complex      *output,
    size_t                 n)
{
    for (size_t i = 0; i < n; i++)
        output[i] = engine_step(state, input[i]);
}

double
engine_get_gain(const engine_state_t *state)
{
    return state->gain;
}

void
engine_set_gain(engine_state_t *state, double gain)
{
    state->gain = gain;
}
