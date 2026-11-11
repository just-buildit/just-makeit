"""Replace the generated my_vs_scale_step() stub with 02_step.c.

Run from the project root. This is the edit the README makes by hand in
Visual Studio's editor: the stub in the public header becomes the body in
02_step.c, which sits next to this script.
"""

import pathlib
import re
import sys

header = pathlib.Path("native/inc/my_vs/scale/scale_core.h")
impl = pathlib.Path(__file__).with_name("02_step.c")

stub_re = re.compile(
    r"static inline double\s*\n"
    r"my_vs_scale_step\(const my_vs_scale_state_t \*state, double x\)"
    r"\n\{.*?\n\}",
    re.DOTALL,
)

text = header.read_text(encoding="utf-8")
if len(stub_re.findall(text)) != 1:
    print(
        "ERROR: stub not found exactly once -- already patched or changed",
        file=sys.stderr,
    )
    sys.exit(1)

body = impl.read_text(encoding="utf-8").strip()
header.write_text(stub_re.sub(lambda _: body, text), encoding="utf-8")
print(f"patched {header} with {impl.name}")
