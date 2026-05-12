"""Patch chunker_core.c stubs with the accumulate-and-fire implementation.

Run from the project root: python3 .steps/02_patch.py
"""

import pathlib
import re
import sys

core = pathlib.Path("native/src/chunker/chunker_core.c")
impl = pathlib.Path(__file__).with_name("02_push_after.c")

text = core.read_text(encoding="utf-8")
after = impl.read_text(encoding="utf-8").strip()

# Replace both stubs in one pass: match from the first <<IMPLEMENT>> comment
# before chunker_push_max_out through the closing brace of chunker_push.
stubs_re = re.compile(
    r"/\* <<IMPLEMENT[^>]*>>\s*\*/\s*\n"
    r"size_t\s*\nchunker_push_max_out\([^{]*\)\n\{[^}]*\}"
    r"\s*\n\n"
    r"/\* <<IMPLEMENT[^>]*>>\s*\*/\s*\n"
    r"size_t\s*\nchunker_push\([^{]*\)\n\{[^}]*\}",
    re.DOTALL,
)

if not stubs_re.search(text):
    print("ERROR: stubs not found — already patched or file changed", file=sys.stderr)
    sys.exit(1)

patched = stubs_re.sub(after, text)
core.write_text(patched, encoding="utf-8")
print(f"patched {core}")
