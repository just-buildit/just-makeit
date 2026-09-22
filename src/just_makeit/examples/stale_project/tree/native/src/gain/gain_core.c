#include "gain/gain_core.h"

gain_state_t *
gain_create(float level)
{
    gain_state_t *obj = calloc(1, sizeof(*obj));
    if (!obj)
        return NULL;
    obj->level = level;
    return obj;
}

void
gain_destroy(gain_state_t *state)
{
    free(state);
}

void
gain_reset(gain_state_t *state)
{
    state->level = 1.0;
}

void gain_steps(
    gain_state_t *state,
    const float    *input,
    float          *output,
    size_t               n)
{
    for (size_t i = 0; i < n; i++)
        output[i] = gain_step(state, input[i]);
}

float
gain_get_level(const gain_state_t *state)
{
    return state->level;
}

void
gain_set_level(gain_state_t *state, float val)
{
    state->level = val;
}
