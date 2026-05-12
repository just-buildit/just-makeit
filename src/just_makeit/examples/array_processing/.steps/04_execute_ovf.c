/* Implement in native/src/hbdecim/hbdecim_core.c.
 *
 * Two output arrays: primary (filtered samples) and secondary (overflow flags).
 * Both are pre-allocated by the ext to execute_ovf_max_out() elements.
 * Return the actual count written to both arrays.
 */
size_t
hbdecim_execute_ovf_max_out(hbdecim_state_t *state)
{
    return (state->block_size + 1) / 2;
}

size_t
hbdecim_execute_ovf(hbdecim_state_t    *state,
                    const float complex *in,
                    size_t              n_in,
                    float complex       *out,    /* primary */
                    uint8_t             *ovf)    /* secondary */
{
    size_t n_out = 0;
    for (size_t i = 0; i + 1 < n_in; i += 2) {
        float complex y = (in[i] + in[i + 1]) * 0.5f;
        out[n_out] = y;
        ovf[n_out] = (cabsf(y) > 1.0f) ? 1 : 0;
        n_out++;
    }
    return n_out;
}
