"""Patch running_stats_step stub with the Welford implementation.

Run from the project root: python3 .steps/02_patch.py
"""

import pathlib
import re
import sys

header = pathlib.Path("native/inc/running_stats/running_stats_core.h")
impl = pathlib.Path(__file__).with_name("02_step_after.c")

stub_re = re.compile(
    r"static inline float complex\s*\n"
    r"running_stats_step\((?:const )?running_stats_state_t \*state.*?\n\}",
    re.DOTALL,
)

text = header.read_text()
if not stub_re.search(text):
    print("ERROR: stub not found — already patched or file changed", file=sys.stderr)
    sys.exit(1)

patched = stub_re.sub(impl.read_text().strip(), text)
header.write_text(patched)
print(f"patched {header}")
