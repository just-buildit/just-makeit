"""Patch fir_step stub: drop const, add delay-line + convolution body."""
import pathlib
import re
import sys

header = pathlib.Path("native/inc/fir/fir_core.h")
text   = header.read_text()

stub_re = re.compile(
    r"(static inline|JM_FORCEINLINE JM_HOT) float complex\s*\n"
    r"fir_step\(const fir_state_t \*state.*?\n\}",
    re.DOTALL,
)
m = stub_re.search(text)
if not m:
    print("ERROR: fir_step stub not found — already patched?", file=sys.stderr)
    sys.exit(1)

impl = (
    "fir_step(fir_state_t *state, float complex x)\n"
    "{\n"
    "    memmove(&state->delay[1], &state->delay[0],\n"
    "            (16 - 1) * sizeof(float complex));\n"
    "    state->delay[0] = x;\n"
    "    float complex y = 0.0f;\n"
    "    for (int k = 0; k < 16; k++)\n"
    "        y += state->coeffs[k] * state->delay[k];\n"
    "    return (float complex)state->gain * y;\n"
    "}"
)
replacement = m.group(1) + " float complex\n" + impl
header.write_text(stub_re.sub(replacement, text))
print(f"patched {header}")
