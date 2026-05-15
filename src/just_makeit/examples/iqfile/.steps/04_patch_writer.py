"""Implement cf32_to_q15_step() and add the samples_written counter."""

from pathlib import Path
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()

# ── step() in _core.h ──────────────────────────────────────────────────────
core_h = root / "native/inc/cf32_to_q15/cf32_to_q15_core.h"
text = core_h.read_text(encoding="utf-8")

if "<math.h>" not in text:
    text = text.replace(
        '#include "clib_common.h"',
        '#include "clib_common.h"\n#include <math.h>',
        1,
    )

OLD = """\
    (void)state; /* TODO: implement using state variables */
    return (int32_t)x;"""

NEW = """\
    float s = state->scale;
    int16_t i = (int16_t)fmaxf(-s, fminf(s, crealf(x) * s));
    int16_t q = (int16_t)fmaxf(-s, fminf(s, cimagf(x) * s));
    /* Pack I in low 16 bits, Q in high 16 bits.
     * Python: packed.view(np.int16) gives interleaved [i0, q0, i1, q1, ...] */
    return (int32_t)((uint32_t)(uint16_t)i | ((uint32_t)(uint16_t)q << 16));"""

assert OLD in text, "step stub not found — was it already patched?"
core_h.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
print(f"patched  {core_h.relative_to(root)}")

# ── samples_written counter in _core.c ────────────────────────────────────
core_c = root / "native/src/cf32_to_q15/cf32_to_q15_core.c"
text = core_c.read_text(encoding="utf-8")

OLD_LOOP = """\
    for (size_t i = 0; i < n; i++)
        output[i] = cf32_to_q15_step(state, input[i]);
}"""

NEW_LOOP = """\
    for (size_t i = 0; i < n; i++)
        output[i] = cf32_to_q15_step(state, input[i]);
    state->samples_written += (uint32_t)n;
}"""

assert OLD_LOOP in text, "steps() loop not found"
core_c.write_text(text.replace(OLD_LOOP, NEW_LOOP, 1), encoding="utf-8")
print(f"patched  {core_c.relative_to(root)}")
