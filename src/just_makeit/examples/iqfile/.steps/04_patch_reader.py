"""Implement q15_to_cf32_step(), samples_read counter, and eof getter."""

from pathlib import Path
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()

# ── add <unistd.h> to _core.h ─────────────────────────────────────────────
core_h = root / "native/inc/q15_to_cf32/q15_to_cf32_core.h"
text = core_h.read_text(encoding="utf-8")

if "<unistd.h>" not in text:
    text = text.replace(
        '#include "clib_common.h"',
        '#include "clib_common.h"\n#include <unistd.h>',
        1,
    )

# ── step() in _core.h ──────────────────────────────────────────────────────
OLD = """\
    (void)state; /* TODO: implement */
    return (float complex)0;"""

NEW = """\
    int16_t pair[2] = {0, 0};
    if (state->fd >= 0)
        read((int)state->fd, pair, sizeof(pair));
    return ((float)pair[0] + (float)pair[1] * I) / state->scale;"""

assert OLD in text, "step stub not found — was it already patched?"
text = text.replace(OLD, NEW, 1)

# Add eof getter declaration before the closing header guard #endif
guard = "#endif /* Q15_TO_CF32_CORE_H */"
assert guard in text, "header guard not found"
text = text.replace(
    guard,
    "int32_t q15_to_cf32_get_eof(const q15_to_cf32_state_t *state);\n\n"
    + guard,
    1,
)
core_h.write_text(text, encoding="utf-8")
print(f"patched  {core_h.relative_to(root)}")

# ── samples_read counter + eof getter in _core.c ──────────────────────────
core_c = root / "native/src/q15_to_cf32/q15_to_cf32_core.c"
text = core_c.read_text(encoding="utf-8")

# Add <unistd.h> if needed (steps() calls read/lseek)
if "<unistd.h>" not in text:
    text = text.replace(
        '#include "q15_to_cf32/q15_to_cf32_core.h"',
        '#include "q15_to_cf32/q15_to_cf32_core.h"\n#include <unistd.h>',
        1,
    )

# Counter in steps()
OLD_LOOP = """\
    for (size_t i = 0; i < n; i++)
        output[i] = q15_to_cf32_step(state);
}"""

NEW_LOOP = """\
    for (size_t i = 0; i < n; i++)
        output[i] = q15_to_cf32_step(state);
    state->samples_read += (uint32_t)n;
}"""

assert OLD_LOOP in text, "steps() loop not found"
text = text.replace(OLD_LOOP, NEW_LOOP, 1)

# eof getter stub (called by the Python property)
EOF_IMPL = """
int32_t
q15_to_cf32_get_eof(const q15_to_cf32_state_t *state)
{
    if (state->fd < 0)
        return 1;
    off_t cur = lseek((int)state->fd, 0, SEEK_CUR);
    off_t end = lseek((int)state->fd, 0, SEEK_END);
    lseek((int)state->fd, cur, SEEK_SET);
    return cur == end ? 1 : 0;
}
"""

text += EOF_IMPL
core_c.write_text(text, encoding="utf-8")
print(f"patched  {core_c.relative_to(root)}")
