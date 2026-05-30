"""Patch sliding_correlator_core.h and .c for JM_DEFINE_STEPS.

Run from the project root: python3 .steps/04_patch.py

Header: inserts sliding_correlator_step_batch() after sliding_correlator_step().
Source: replaces sliding_correlator_steps() with JM_DEFINE_STEPS(...).
"""

import pathlib
import re
import sys

header = pathlib.Path(
    "native/inc/sliding_correlator/sliding_correlator_core.h"
)
core_c = pathlib.Path(
    "native/src/sliding_correlator/sliding_correlator_core.c"
)
batch_h = pathlib.Path(__file__).with_name("04_step_batch.h")
kernel = pathlib.Path(__file__).with_name("04_kernel.c")

# ── 1. Insert step_batch() into header ───────────────────────────────────────

insert_re = re.compile(
    r"(sliding_correlator_step\(.*?\n\})\s*\n(/\*\*\n \* @brief Process a block)",
    re.DOTALL,
)

htext = header.read_text()
if "sliding_correlator_step_batch" in htext:
    print(f"{header}: step_batch already present, skipping")
else:
    if not insert_re.search(htext):
        print("ERROR: insertion point not found in header", file=sys.stderr)
        sys.exit(1)
    batch_content = batch_h.read_text().strip()
    htext = insert_re.sub(
        lambda m: m.group(1) + "\n\n" + batch_content + "\n\n" + m.group(2),
        htext,
    )
    header.write_text(htext)
    print(f"patched {header}")

# ── 2. Replace sliding_correlator_steps() in source ──────────────────────────

ctext = core_c.read_text()

if "JM_DEFINE_STEPS" in ctext:
    print(f"{core_c}: JM_DEFINE_STEPS already present, skipping")
else:
    fn_re = re.compile(
        r"void\s+sliding_correlator_steps\(.*?\n\}",
        re.DOTALL,
    )
    if not fn_re.search(ctext):
        print("ERROR: sliding_correlator_steps not found", file=sys.stderr)
        sys.exit(1)
    ctext = fn_re.sub(kernel.read_text().strip(), ctext)
    core_c.write_text(ctext)
    print(f"patched {core_c}")
