"""Replace the generated acc_f32_steps() with an explicit SIMD version.

Patches native/src/acc_f32/acc_f32_core.c — run from inside my_acc/.

The generated loop is a serial dependency chain:
    for (size_t i = 0; i < n; i++)
        acc_f32_step(state, input[i]);  /* state->acc += input[i] */

Each iteration waits for the previous float add to complete (~4 cycle latency).
The replacement uses JM_VEC_F32 to accumulate JM_SIMD_WIDTH_F32 independent
lanes simultaneously, then horizontally reduces at the end with JM_HSUM_F32.
On the scalar tier (JM_SIMD_WIDTH_F32 == 1) the #else branch is a no-op.
"""

import pathlib
import re
import sys

NEW_STEPS = """\
#if JM_SIMD_WIDTH_F32 > 1
JM_HOT void
acc_f32_steps(acc_f32_state_t *JM_RESTRICT state,
              const float *JM_RESTRICT input, size_t n)
{
    JM_VEC_F32 vacc = JM_ZERO_F32();
    size_t i = 0;
    for (; i + JM_SIMD_WIDTH_F32 <= n; i += JM_SIMD_WIDTH_F32)
        vacc = JM_ADD_F32(vacc, JM_LOAD_F32(input + i));
    state->acc += JM_HSUM_F32(vacc);
    for (; i < n; i++)
        state->acc += input[i];
}
#else
JM_HOT void
acc_f32_steps(acc_f32_state_t *JM_RESTRICT state,
              const float *JM_RESTRICT input, size_t n)
{
    for (size_t i = 0; i < n; i++)
        state->acc += input[i];
}
#endif"""

core = pathlib.Path("native/src/acc_f32/acc_f32_core.c")
text = core.read_text(encoding="utf-8")

start = text.find("void acc_f32_steps(")
if start < 0:
    print("ERROR: acc_f32_steps not found", file=sys.stderr)
    sys.exit(1)

brace_open = text.find("{", start)
if brace_open < 0:
    print("ERROR: opening brace not found", file=sys.stderr)
    sys.exit(1)

close_m = re.search(r"^\}", text[brace_open:], re.MULTILINE)
if not close_m:
    print("ERROR: closing brace not found", file=sys.stderr)
    sys.exit(1)

brace_close = brace_open + close_m.end()

text = text[:start] + NEW_STEPS + text[brace_close:]
core.write_text(text, encoding="utf-8")
print(f"patched {core}")
