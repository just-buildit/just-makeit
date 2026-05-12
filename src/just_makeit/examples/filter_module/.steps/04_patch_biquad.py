"""Patch biquad_step stub: drop const, add DF2T body."""
import pathlib
import re
import sys

header = pathlib.Path("native/inc/biquad/biquad_core.h")
text   = header.read_text()

stub_re = re.compile(
    r"(static inline|JM_FORCEINLINE JM_HOT) float\s*\n"
    r"biquad_step\((?:const )?biquad_state_t \*state.*?\n\}",
    re.DOTALL,
)
m = stub_re.search(text)
if not m:
    print("ERROR: biquad_step stub not found — already patched?", file=sys.stderr)
    sys.exit(1)

impl = (
    "biquad_step(biquad_state_t *state, float x)\n"
    "{\n"
    "    float y  = state->b0 * x + state->w1;\n"
    "    state->w1 = state->b1 * x - state->a1 * y + state->w2;\n"
    "    state->w2 = state->b2 * x - state->a2 * y;\n"
    "    return y;\n"
    "}"
)
replacement = m.group(1) + " float\n" + impl
header.write_text(stub_re.sub(replacement, text))
print(f"patched {header}")
