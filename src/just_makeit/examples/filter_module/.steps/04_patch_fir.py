"""Patch fir_step stub: drop const, add delay-line + convolution body."""

import pathlib
import re
import sys

header = pathlib.Path("native/inc/fir/fir_core.h")
text = header.read_text()

stub_re = re.compile(
    r"(static inline|JM_FORCEINLINE JM_HOT) float _Complex\s*\n"
    r"fir_step\((?:const )?fir_state_t \*state.*?\n\}",
    re.DOTALL,
)
m = stub_re.search(text)
if not m:
    print("ERROR: fir_step stub not found — already patched?", file=sys.stderr)
    sys.exit(1)

impl = (
    "fir_step(fir_state_t *state, float _Complex x)\n"
    "{\n"
    "    memmove(&state->delay[1], &state->delay[0],\n"
    "            (16 - 1) * sizeof(float _Complex));\n"
    "    state->delay[0] = x;\n"
    "    float _Complex y = 0.0f;\n"
    "    for (int k = 0; k < 16; k++)\n"
    "        y += state->coeffs[k] * state->delay[k];\n"
    "    return (float _Complex)state->gain * y;\n"
    "}"
)
replacement = m.group(1) + " float _Complex\n" + impl
header.write_text(stub_re.sub(replacement, text))
print(f"patched {header}")
