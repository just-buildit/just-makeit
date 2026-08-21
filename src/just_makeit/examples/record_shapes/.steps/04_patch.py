"""Implement step() and the three record kernels.

Every body here is the author's. jm generated the prototypes, the argument
parsing, the `PyStructSequence`, and the runtime dtype; what it cannot know is
what a record MEANS, so each `<<IMPLEMENT>>` placeholder is a no-op until this
runs.
"""

from pathlib import Path

HEADER = Path("native/inc/collector/collector_core.h")
CORE = Path("native/src/collector/collector_core.c")

STEP_BODY = """\
collector_step(collector_state_t *state, double x)
{
    size_t slot = (size_t)(state->count % EVLOG_CAPACITY);

    state->t[slot] = state->count;
    state->v[slot] = x;
    state->count++;
}"""

HELPERS = """\
/* Ring capacity. Must match the [64] on the t/v state fields: jm sizes the
 * struct members from the manifest and this constant is how the kernels
 * agree with it.
 */
#define EVLOG_CAPACITY 64

/* A value has to beat this to count as a peak. */
#define EVLOG_PEAK_THRESHOLD 1.0
"""

HELD = """\
/* How many slots actually hold data: the ring is not full until `count`
 * reaches capacity, and reading past that returns whatever create() zeroed.
 */
static size_t
collector_held(const collector_state_t *state)
{
    return state->count < EVLOG_CAPACITY ? (size_t)state->count
                                         : (size_t)EVLOG_CAPACITY;
}

"""

SUMMARY = """\
    size_t          held = collector_held(state);
    evlog_summary_t _r   = {0};
    double          sum  = 0.0;

    /* `n` is every record ever seen; the mean is over what the ring still
     * holds. Returned BY VALUE -- jm's binding converts it to a Summary.
     */
    _r.n = state->count;
    for (size_t i = 0; i < held; i++)
        sum += state->v[i];
    _r.mean = held ? sum / (double)held : 0.0;
    return _r;"""

READ_MAX_OUT = """\
    (void)n;
    /* Upper bound on what read() can produce, so the binding can size the
     * `out` buffer before calling it.
     */
    return collector_held(state);"""

READ = """\
    size_t held = collector_held(state);

    (void)n;
    /* Fill the caller's buffer with whole records; the return value is how
     * many are valid. jm turns those into a structured ndarray.
     */
    for (size_t i = 0; i < held; i++) {
        out[i].t = state->t[i];
        out[i].v = state->v[i];
    }
    return held;"""

PEAKS = """\
    size_t held  = collector_held(state);
    size_t n_out = 0;

    /* Whole evlog_peak_t rows into result[], capped at max_results; the
     * return value is how many were written. jm turns each row into a
     * tuple, reading the members named by result_fields.
     */
    for (size_t i = 0; i < held && n_out < max_results; i++) {
        if (state->v[i] <= EVLOG_PEAK_THRESHOLD)
            continue;
        result[n_out].index = i;
        result[n_out].value = state->v[i];
        n_out++;
    }
    return n_out;"""


def _replace(text: str, old: str, new: str, what: str) -> str:
    assert old in text, (
        f"anchor for {what} not found -- did the scaffold change?"
    )
    return text.replace(old, new, 1)


def main() -> None:
    h = HEADER.read_text(encoding="utf-8")
    h = _replace(
        h,
        """collector_step(collector_state_t *state, double x)
{
    (void)state; (void)x; /* TODO: implement */
}""",
        STEP_BODY,
        "step()",
    )
    h = _replace(
        h,
        '#include "clib_common.h"',
        '#include "clib_common.h"\n\n' + HELPERS,
        "helper defines",
    )
    HEADER.write_text(h, encoding="utf-8")

    c = CORE.read_text(encoding="utf-8")
    c = _replace(
        c,
        "/* <<IMPLEMENT: compute and return the record >> */",
        HELD + "/* <<IMPLEMENT: compute and return the record >> */",
        "held() helper",
    )
    c = _replace(
        c,
        "    (void)state;\n\n    evlog_summary_t _r = {0};\n    return _r; /* placeholder */",
        SUMMARY,
        "summary()",
    )
    c = _replace(
        c,
        "    (void)state; (void)n;\n    return 0; /* placeholder */",
        READ_MAX_OUT,
        "read_max_out()",
    )
    c = _replace(
        c,
        "    (void)state;\n    (void)n;\n    (void)out;\n    return 0; /* placeholder */",
        READ,
        "read()",
    )
    c = _replace(
        c,
        "    (void)state;\n\n    (void)result; (void)max_results;\n    return 0; /* placeholder */",
        PEAKS,
        "peaks()",
    )
    CORE.write_text(c, encoding="utf-8")


if __name__ == "__main__":
    main()
