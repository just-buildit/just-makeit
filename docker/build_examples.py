"""Build all bundled just-makeit examples into DEST.

Usage:
    python3 build_examples.py /path/to/dest

Each example's test.py run() function is called with DEST as the root, so
each scaffolded project lands at DEST/<project-name>/.
"""

import importlib.util
import sys
import traceback
from pathlib import Path

if len(sys.argv) < 2:
    print("usage: build_examples.py <dest>", file=sys.stderr)
    sys.exit(1)

dest = Path(sys.argv[1])
dest.mkdir(parents=True, exist_ok=True)

from just_makeit._example import _EXAMPLES, _find

failed = []
for name in _EXAMPLES:
    ex_dir = _find(name)
    if ex_dir is None:
        print(f"  {name}: skipped (not found)", flush=True)
        continue
    spec = importlib.util.spec_from_file_location(
        f"jm_ex_{name}", ex_dir / "test.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print(f"  [{name}] building...", flush=True)
    try:
        mod.run(dest)
        print(f"  [{name}] PASSED", flush=True)
    except Exception:
        print(f"  [{name}] FAILED:", flush=True)
        traceback.print_exc()
        failed.append(name)

if failed:
    print(f"\nFailed: {', '.join(failed)}", file=sys.stderr)
    sys.exit(1)

print(f"\nAll {len(_EXAMPLES)} examples built in {dest}")
