"""Patch power_est_step stub with the recursive implementation.

Run from the project root: python3 .steps/02_patch.py
"""

import pathlib
import re
import sys

header = pathlib.Path("native/inc/power_est/power_est_core.h")
impl = pathlib.Path(__file__).with_name("02_step_impl.c")

stub_re = re.compile(
    r"JM_FORCEINLINE JM_HOT float\s*\n"
    r"power_est_step\((?:const )?power_est_state_t \*state.*?\n\}",
    re.DOTALL,
)

text = header.read_text()
if not stub_re.search(text):
    print("ERROR: stub not found — already patched or file changed", file=sys.stderr)
    sys.exit(1)

header.write_text(stub_re.sub(impl.read_text().strip(), text))
print(f"patched {header}")
