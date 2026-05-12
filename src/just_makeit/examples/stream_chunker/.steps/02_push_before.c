/* <<IMPLEMENT>> return max samples chunker_push can ever produce */
size_t
chunker_push_max_out(chunker_state_t *state)
{
    (void)state;
    return 0; /* TODO */
}

/* <<IMPLEMENT>> process input and write results to out[]; return count written */
size_t
chunker_push(chunker_state_t *state, const float complex *in, size_t n_in,
             float complex *out)
{
    (void)state; (void)in; (void)out;
    return 0; /* TODO */
}
