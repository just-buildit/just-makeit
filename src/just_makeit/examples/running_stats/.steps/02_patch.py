"""Patch running_stats_step stub with an implementation.

Run from the project root:
    python3 .steps/02_patch.py                  # base body (mean/var only)
    python3 .steps/02_patch.py 02_step_after.c  # full body (+ min/max state)

The implementation file is resolved next to this script. It is applied by
replacing the generated `running_stats_step()` stub in the public header. The
same script is re-run after `jm add` regenerates the object back to a fresh
stub — that is the canonical "add state, then re-implement" loop. After adding
min_val/max_val, pass 02_step_after.c to restore the algorithm on top of the
new state.
"""

import pathlib
import re
import sys

header = pathlib.Path("native/inc/running_stats/running_stats_core.h")
impl_name = sys.argv[1] if len(sys.argv) > 1 else "02_base_step.c"
impl = pathlib.Path(__file__).with_name(impl_name)

stub_re = re.compile(
    r"static inline float complex\s*\n"
    r"running_stats_step\((?:const )?running_stats_state_t \*state.*?\n\}",
    re.DOTALL,
)

text = header.read_text()
if not stub_re.search(text):
    print(
        "ERROR: stub not found — already patched or file changed",
        file=sys.stderr,
    )
    sys.exit(1)

patched = stub_re.sub(impl.read_text().strip(), text)
header.write_text(patched)
print(f"patched {header} with {impl_name}")
