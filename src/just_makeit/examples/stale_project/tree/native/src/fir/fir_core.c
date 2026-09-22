#include "fir/fir_core.h"

fir_state_t *
fir_create(float scale)
{
    fir_state_t *obj = calloc(1, sizeof(*obj));
    if (!obj)
        return NULL;
    obj->scale = scale;
    return obj;
}

void
fir_destroy(fir_state_t *state)
{
    free(state);
}

void
fir_reset(fir_state_t *state)
{
    state->scale = 1.0;
}

void fir_steps(
    fir_state_t *state,
    const float    *input,
    float          *output,
    size_t               n)
{
    for (size_t i = 0; i < n; i++)
        output[i] = fir_step(state, input[i]);
}

float
fir_get_scale(const fir_state_t *state)
{
    return state->scale;
}

void
fir_set_scale(fir_state_t *state, float val)
{
    state->scale = val;
}

/* Worst-case output count for decimate() — set via --max-out 4096. */
size_t
fir_decimate_max_out(fir_state_t *state)
{
    (void)state;
    return 4096;
}

/* <<IMPLEMENT: process input and write results into out[0..n_out-1]; return actual output count >> */
size_t
fir_decimate(fir_state_t *state, const float *in, size_t n_in, float *out)
{
    (void)state;
    (void)in; (void)n_in;
    (void)out;
    return 0; /* placeholder */
}

/* <<IMPLEMENT: shape >> */
float complex
fir_shape(fir_state_t *state, float x, float *out)
{
    (void)state; (void)x; (void)out;
    return (float complex)0.0f + 0.0f * I;
}
