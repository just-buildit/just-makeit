"""Patch acc_cf64 step stub and named method stubs.

Replaces:
  - acc_cf64_step in acc_cf64_core.h  (uses TODO: implement marker)
  - acc_cf64_get, dump, madd, add2d, madd2d in acc_cf64_core.c
    (use <<IMPLEMENT: name >> marker)
"""

import pathlib
import re
import sys


def patch_step(text):
    """Replace the step stub body in the header."""
    stub_re = re.compile(
        r"static inline void\s*\n"
        r"acc_cf64_step\("
        r"acc_cf64_state_t \*state, double complex x\)\s*\{"
        r"[^}]*\}",
        re.DOTALL,
    )
    m = stub_re.search(text)
    if not m:
        print(
            "ERROR: acc_cf64_step stub not found — already patched?",
            file=sys.stderr,
        )
        sys.exit(1)
    impl = (
        "static inline void\n"
        "acc_cf64_step(acc_cf64_state_t *state, double complex x)\n"
        "{\n"
        "    state->acc += x;\n"
        "}"
    )
    return stub_re.sub(impl, text)


def patch_fn(text, fn_name, new_body):
    """Replace the body of a named-method stub (between braces)."""
    marker = f"/* <<IMPLEMENT: {fn_name} >> */"
    idx = text.find(marker)
    if idx < 0:
        raise ValueError(f"marker for {fn_name!r} not found")
    brace_open = text.find("{", idx)
    if brace_open < 0:
        raise ValueError(f"opening brace for {fn_name!r} not found")
    close = re.search(r"^\}", text[brace_open:], re.MULTILINE)
    if not close:
        raise ValueError(f"closing brace for {fn_name!r} not found")
    brace_close = brace_open + close.start()
    return text[: brace_open + 1] + "\n" + new_body + "\n" + text[brace_close:]


# --- Patch header (step stub) ---

header = pathlib.Path("native/inc/acc_cf64/acc_cf64_core.h")
text = header.read_text(encoding="utf-8")
text = patch_step(text)
header.write_text(text, encoding="utf-8")
print(f"patched {header}")

# --- Patch core.c (named methods) ---

core = pathlib.Path("native/src/acc_cf64/acc_cf64_core.c")
text = core.read_text(encoding="utf-8")

text = patch_fn(text, "get", "    return state->acc;")

text = patch_fn(
    text,
    "dump",
    "    double complex v = state->acc;\n"
    "    state->acc = 0.0 + 0.0 * I;\n"
    "    return v;",
)

text = patch_fn(
    text,
    "madd",
    "    size_t n = x_len < h_len ? x_len : h_len;\n"
    "    for (size_t i = 0; i < n; i++)\n"
    "        state->acc += x[i] * (double)h[i];",
)

text = patch_fn(
    text,
    "add2d",
    "    for (size_t i = 0; i < x_len; i++)\n        state->acc += x[i];",
)

text = patch_fn(
    text,
    "madd2d",
    "    size_t n = x_len < h_len ? x_len : h_len;\n"
    "    for (size_t i = 0; i < n; i++)\n"
    "        state->acc += x[i] * (double)h[i];",
)

core.write_text(text, encoding="utf-8")
print(f"patched {core}")
