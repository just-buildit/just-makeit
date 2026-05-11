/* native/inc/fir_pure/fir_pure_core.h — replace fir_pure_fn stub */

static inline float complex
fir_pure_fn(float complex x, fir_pure_params_t *params)
{
    /* Shift delay line — oldest sample falls off the end */
    memmove(&params->delay[1], &params->delay[0],
            (16 - 1) * sizeof(float complex));
    params->delay[0] = x;

    /* Convolve: y = sum_k( coeffs[k] * delay[k] ) */
    float complex y = 0.0f;
    for (int k = 0; k < 16; k++)
        y += params->coeffs[k] * params->delay[k];
    return y;
}
