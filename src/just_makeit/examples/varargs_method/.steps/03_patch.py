"""Patch filter_step and filter_configure stubs with implementations.

Run from the project root (my_filter/):
    python3 .steps/03_patch.py
"""

import pathlib
import re

STEPS = pathlib.Path(__file__).parent

# -- 1. Patch the inline filter_step in filter_core.h -------------------
header = pathlib.Path("native/inc/filter/filter_core.h")
step_impl = (STEPS / "03_step.c").read_text()
step_re = re.compile(
    r"static inline float\s*\nfilter_step"
    r"\(const filter_state_t \*state, float x\)\n\{.*?\}",
    re.DOTALL,
)
text = header.read_text()
if step_re.search(text):
    header.write_text(step_re.sub(step_impl.strip(), text))
    print(f"patched {header}")
else:
    print("filter_step: already patched or stub changed — skipping")

# -- 2. Replace filter_configure_core.c with the full implementation ----
configure_c = pathlib.Path("native/src/filter/filter_configure_core.c")
configure_c.write_text((STEPS / "03_configure.c").read_text())
print(f"patched {configure_c}")

# -- 3. Implement the typed filter_current_gain reader in filter_core.c --
core = pathlib.Path("native/src/filter/filter_core.c")
core_text = core.read_text()
current_gain_re = re.compile(
    r"/\* <<IMPLEMENT: current_gain >> \*/\n"
    r"double\s*\nfilter_current_gain\(filter_state_t \*state\)\n\{.*?\}",
    re.DOTALL,
)
current_gain_impl = (
    "double\n"
    "filter_current_gain(filter_state_t *state)\n"
    "{\n"
    "    return state->gain;\n"
    "}"
)
if current_gain_re.search(core_text):
    core.write_text(current_gain_re.sub(current_gain_impl, core_text))
    print(f"patched {core}")
else:
    print("filter_current_gain: already patched or stub changed — skipping")
