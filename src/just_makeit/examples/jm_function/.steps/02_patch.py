"""Patch the generated C stubs with real implementations.

Run from the project root: python3 .steps/02_patch.py

Patches three stubs:
  1. linear_to_db() in linear_to_db.c — replaces placeholder with log10f body.
  2. clamp() in utils_core.h        — replaces placeholder with ternary body.
  3. gain_step() in gain_core.h     — replaces unused-state placeholder with
                                      the multiplication that exercises state.

All three use str.replace() on the exact text that jm_function / jm_object
emit.  No regex: the stubs are deterministic so exact matching is safe and
keeps the patch readable.
"""

import pathlib
import sys

# ── 1. linear_to_db in linear_to_db.c ───────────────────────────────────────

core_c = pathlib.Path("native/src/utils/linear_to_db.c")
text = core_c.read_text(encoding="utf-8")

# Add <math.h> if not already included.  The generated file only has
# utils_core.h included; log10f lives in <math.h>.
if "<math.h>" not in text:
    text = text.replace(
        '#include "utils/utils_core.h"',
        '#include "utils/utils_core.h"\n#include <math.h>',
    )

# Replace the placeholder body produced by fn_c_stub().
old_linear = (
    "/* <<IMPLEMENT: linear_to_db>> */\n"
    "float\n"
    "linear_to_db(float x)\n"
    "{\n"
    "    (void)x;\n"
    "    return (float)0.0f; /* placeholder */\n"
    "}"
)
new_linear = (
    "float\n"
    "linear_to_db(float x)\n"
    "{\n"
    "    return 20.0f * log10f(x > 0.0f ? x : 1e-10f);\n"
    "}"
)
if old_linear not in text:
    print(
        "ERROR: linear_to_db stub not found — already patched?",
        file=sys.stderr,
    )
    sys.exit(1)
text = text.replace(old_linear, new_linear)
core_c.write_text(text, encoding="utf-8")
print(f"patched {core_c}")

# ── 2. clamp (static inline) in utils_core.h ────────────────────────────────

core_h = pathlib.Path("native/inc/utils/utils_core.h")
text = core_h.read_text(encoding="utf-8")

old_clamp = (
    "/* <<IMPLEMENT: clamp>> */\n"
    "static inline float\n"
    "clamp(float x, float lo, float hi)\n"
    "{\n"
    "    (void)x; (void)lo; (void)hi;\n"
    "    return (float)0.0f; /* placeholder */\n"
    "}"
)
new_clamp = (
    "static inline float\n"
    "clamp(float x, float lo, float hi)\n"
    "{\n"
    "    return x < lo ? lo : x > hi ? hi : x;\n"
    "}"
)
if old_clamp not in text:
    print("ERROR: clamp stub not found — already patched?", file=sys.stderr)
    sys.exit(1)
text = text.replace(old_clamp, new_clamp)
core_h.write_text(text, encoding="utf-8")
print(f"patched {core_h}")

# ── 3. gain_step (static inline) in gain_core.h ─────────────────────────────
#
# The generated step() body uses (void)state to suppress the unused-variable
# warning.  We replace that one line with the actual multiply so the Gain
# object passes its Python smoke test.

gain_h = pathlib.Path("native/inc/gain/gain_core.h")
text = gain_h.read_text(encoding="utf-8")

old_gain = "    (void)state; /* TODO: implement using state variables */\n    return (float)x;"
new_gain = "    return state->gain * x;"
if old_gain not in text:
    print(
        "ERROR: gain_step stub not found — already patched?", file=sys.stderr
    )
    sys.exit(1)
text = text.replace(old_gain, new_gain)
gain_h.write_text(text, encoding="utf-8")
print(f"patched {gain_h}")
