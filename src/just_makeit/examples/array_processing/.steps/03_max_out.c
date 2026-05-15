/* Implement in native/src/hbdecim/hbdecim_core.c.
 *
 * The Python ext calls this once at __init__ to size the pre-allocated
 * output buffer.  Return the largest n_out that execute() can ever produce
 * for any valid call.  Here: block_size / 2, rounded up.
 *
 * Must be positive.  Returning 0 causes malloc(0), which is implementation-
 * defined and will likely produce a silent bug.
 */
size_t hbdecim_execute_max_out(hbdecim_state_t *state) {
    /* state->block_size is a constructor parameter (add with just-makeit add) */
    return (state->block_size + 1) / 2;
}

/* Process n_in samples; write actual output count to *out; return n_out.
 * The caller (Python ext) supplies the pre-allocated output buffer.
 */
size_t hbdecim_execute(hbdecim_state_t *state, const float complex *in, size_t n_in,
                       float complex *out) {
    size_t n_out = 0;
    for (size_t i = 0; i + 1 < n_in; i += 2) {
        /* TODO: polyphase half-band implementation */
        out[n_out++] = (in[i] + in[i + 1]) * 0.5f;
    }
    return n_out;
}
