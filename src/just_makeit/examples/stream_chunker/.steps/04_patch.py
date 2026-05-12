"""Patch chunker_core.c stubs with the accumulate-and-fire implementation.

Run from the project root: python3 .steps/04_patch.py
"""

import pathlib
import re
import sys

core = pathlib.Path("native/src/chunker/chunker_core.c")
text = core.read_text(encoding="utf-8")

# ── chunker_push_max_out ──────────────────────────────────────────────────────

max_out_re = re.compile(
    r"/\* <<IMPLEMENT.*?max.*?out.*?>>\s*\*/\s*\n"
    r"size_t\s*\nchunker_push_max_out\(chunker_state_t \*state\)\n"
    r"\{[^}]*\}",
    re.DOTALL,
)

max_out_impl = """\
size_t
chunker_push_max_out(chunker_state_t *state)
{
    /* Worst case: buffer already holds (chunk_size - 1) samples and the
     * caller passes enough to fill it many times over.  The internal buf[]
     * is 256 elements, so 256 is an absolute upper bound on output. */
    (void)state;
    return 256;
}"""

if not max_out_re.search(text):
    print("ERROR: chunker_push_max_out stub not found", file=sys.stderr)
    sys.exit(1)

text = max_out_re.sub(max_out_impl, text)

# ── chunker_push ─────────────────────────────────────────────────────────────

push_re = re.compile(
    r"/\* <<IMPLEMENT.*?>>\s*\*/\s*\n"
    r"size_t\s*\nchunker_push\([^{]*\)\n"
    r"\{[^}]*\}",
    re.DOTALL,
)

push_impl = """\
size_t
chunker_push(chunker_state_t *state, const float complex *in, size_t n_in,
             float complex *out)
{
    size_t n_out = 0;
    for (size_t i = 0; i < n_in; i++) {
        state->buf[state->n_buf++] = in[i];
        if (state->n_buf >= state->chunk_size) {
            memcpy(out + n_out, state->buf,
                   (size_t)state->chunk_size * sizeof(float complex));
            n_out += (size_t)state->chunk_size;
            state->n_buf = 0;
        }
    }
    return n_out;
}"""

if not push_re.search(text):
    print("ERROR: chunker_push stub not found", file=sys.stderr)
    sys.exit(1)

text = push_re.sub(push_impl, text)
core.write_text(text, encoding="utf-8")
print(f"patched {core}")
