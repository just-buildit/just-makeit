"""Patch ema_step stub with the implementation.

EMA writes back to state->prev, so the signature drops `const`:
  const ema_state_t *  ->  ema_state_t *

Run from the project root: python3 .steps/04_patch.py
"""

import pathlib
import re
import sys

header = pathlib.Path("native/inc/ema/ema_core.h")
impl = pathlib.Path(__file__).with_name("04_step_after.c")

stub_re = re.compile(
    r"(static inline|JM_FORCEINLINE JM_HOT) float\s*\n"
    r"ema_step\((?:const )?ema_state_t \*state.*?\n\}",
    re.DOTALL,
)

text = header.read_text()
m = stub_re.search(text)
if not m:
    print(
        "ERROR: stub not found — already patched or file changed",
        file=sys.stderr,
    )
    sys.exit(1)

qualifier = m.group(1)
# The impl already has the non-const signature; sub replaces the whole match.
replacement = impl.read_text().strip().replace("static inline", qualifier, 1)
patched = stub_re.sub(replacement, text)
header.write_text(patched)
print(f"patched {header}")
