"""Patch fir_filter_step stub with the implementation.

Run from the project root: python3 .steps/02_patch.py
"""

import pathlib
import re
import sys

header = pathlib.Path("native/inc/fir_filter/fir_filter_core.h")
impl = pathlib.Path(__file__).with_name("02_step_after.c")

stub_re = re.compile(
    r"(static inline|JM_FORCEINLINE JM_HOT) float complex\s*\n"
    r"fir_filter_step\((?:const )?fir_filter_state_t \*state.*?\n\}",
    re.DOTALL,
)

text = header.read_text()
m = stub_re.search(text)
if not m:
    print("ERROR: stub not found — already patched or file changed", file=sys.stderr)
    sys.exit(1)

qualifier = m.group(1)
replacement = impl.read_text().strip().replace("static inline", qualifier, 1)
patched = stub_re.sub(replacement, text)
header.write_text(patched)
print(f"patched {header}")
