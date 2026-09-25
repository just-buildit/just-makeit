"""Implement iqfile_q15_to_cf32_step(), samples_read counter, and eof getter."""

from pathlib import Path
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()

# read()/lseek() live in <unistd.h> on POSIX and <io.h> on Windows, under the
# same names -- so one include block, used by both files below.
POSIX_IO = """\
#include <stdio.h>     /* SEEK_SET/CUR/END: <io.h> lacks them */
#include <sys/types.h> /* off_t */
#ifdef _WIN32
#include <io.h> /* read, lseek: the POSIX names, as the CRT spells them */
#else
#include <unistd.h>
#endif"""

# ── add the I/O header to _core.h ─────────────────────────────────────────
core_h = root / "native/inc/iqfile/q15_to_cf32/q15_to_cf32_core.h"
text = core_h.read_text(encoding="utf-8")

if "<unistd.h>" not in text:
    text = text.replace(
        '#include "iqfile/clib_common.h"',
        '#include "iqfile/clib_common.h"\n' + POSIX_IO,
        1,
    )

# ── step() in _core.h ──────────────────────────────────────────────────────
OLD = """\
    (void)state; /* TODO: implement */
    return (float _Complex)0;"""

NEW = """\
    int16_t pair[2] = {0, 0};
    if (state->fd >= 0)
        read((int)state->fd, pair, sizeof(pair));
    return ((float)pair[0] + (float)pair[1] * I) / state->scale;"""

assert OLD in text, "step stub not found — was it already patched?"
text = text.replace(OLD, NEW, 1)

# Add eof getter declaration before the closing header guard #endif
guard = "#endif /* IQFILE_Q15_TO_CF32_CORE_H */"
assert guard in text, "header guard not found"
text = text.replace(
    guard,
    "int32_t iqfile_q15_to_cf32_get_eof(const iqfile_q15_to_cf32_state_t *state);\n\n"
    + guard,
    1,
)
core_h.write_text(text, encoding="utf-8")
print(f"patched  {core_h.relative_to(root)}")

# ── samples_read counter + eof getter in _core.c ──────────────────────────
core_c = root / "native/src/q15_to_cf32/q15_to_cf32_core.c"
text = core_c.read_text(encoding="utf-8")

# The I/O header if needed (steps() calls read/lseek)
if "<unistd.h>" not in text:
    text = text.replace(
        '#include "iqfile/q15_to_cf32/q15_to_cf32_core.h"',
        '#include "iqfile/q15_to_cf32/q15_to_cf32_core.h"\n' + POSIX_IO,
        1,
    )

# Counter in steps()
OLD_LOOP = """\
    for (size_t i = 0; i < n; i++)
        output[i] = iqfile_q15_to_cf32_step(state);
}"""

NEW_LOOP = """\
    for (size_t i = 0; i < n; i++)
        output[i] = iqfile_q15_to_cf32_step(state);
    state->samples_read += (uint32_t)n;
}"""

assert OLD_LOOP in text, "steps() loop not found"
text = text.replace(OLD_LOOP, NEW_LOOP, 1)

# eof getter: `jm property` scaffolded a marked placeholder for it (step 3);
# fill that in rather than writing a second definition beside it.
OLD_EOF = """\
/* <<IMPLEMENT: iqfile_q15_to_cf32_get_eof>> */
int32_t
iqfile_q15_to_cf32_get_eof(const iqfile_q15_to_cf32_state_t *state)
{
    (void)state;
    return 0; /* placeholder */
}"""

NEW_EOF = """\
int32_t
iqfile_q15_to_cf32_get_eof(const iqfile_q15_to_cf32_state_t *state)
{
    if (state->fd < 0)
        return 1;
    off_t cur = lseek((int)state->fd, 0, SEEK_CUR);
    off_t end = lseek((int)state->fd, 0, SEEK_END);
    lseek((int)state->fd, cur, SEEK_SET);
    return cur == end ? 1 : 0;
}"""

assert OLD_EOF in text, "eof placeholder not found — was it already patched?"
text = text.replace(OLD_EOF, NEW_EOF, 1)
core_c.write_text(text, encoding="utf-8")
print(f"patched  {core_c.relative_to(root)}")
