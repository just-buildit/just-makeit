"""Patch fir_filter_core.h and fir_filter_core.c for the scratch-buffer kernel.

Run from the project root: python3 .steps/07_patch.py

Header: inserts fir_filter_step_batch() after fir_filter_step().
Source: replaces fir_filter_steps() with JM_DEFINE_STEPS(...).
"""

import pathlib
import re
import sys

header = pathlib.Path("native/inc/fir_filter/fir_filter_core.h")
core_c = pathlib.Path("native/src/fir_filter/fir_filter_core.c")
batch_h = pathlib.Path(__file__).with_name("07_step_batch.h")
kernel = pathlib.Path(__file__).with_name("07_kernel.c")

# ── 1. Insert step_batch() into header ───────────────────────────────────────

insert_re = re.compile(
    r"(fir_filter_step\(.*?\n\})\s*\n(/\*\*\n \* @brief Process a block)",
    re.DOTALL,
)

htext = header.read_text()
if "fir_filter_step_batch" in htext:
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

# ── 2. Replace fir_filter_steps() in source ──────────────────────────────────

ctext = core_c.read_text()

if "JM_DEFINE_STEPS" in ctext:
    print(f"{core_c}: JM_DEFINE_STEPS already present, skipping")
else:
    fn_re = re.compile(
        r"void\s+fir_filter_steps\(.*?\n\}",
        re.DOTALL,
    )
    if not fn_re.search(ctext):
        print(
            "ERROR: fir_filter_steps not found — already patched or file changed",
            file=sys.stderr,
        )
        sys.exit(1)
    ctext = fn_re.sub(kernel.read_text().strip(), ctext)
    core_c.write_text(ctext)
    print(f"patched {core_c}")
