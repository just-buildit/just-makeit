"""Copy example READMEs into docs/examples/ before building the HTML site.

Run from the project root:
    python3 scripts/copy_examples.py

The output files are gitignored — only docs/examples/index.md is tracked.
"""

import shutil
from pathlib import Path

ROOT = Path(__file__).parent.parent
DEST = ROOT / "docs" / "examples"
DEST.mkdir(exist_ok=True)

examples = [
    ("running_stats", "running_stats.md"),
    ("fir_filter", "fir_filter.md"),
    ("sliding_correlator", "sliding_correlator.md"),
    ("sliding_power", "sliding_power.md"),
    ("dsp_toolkit", "dsp_toolkit.md"),
]

for folder, out_name in examples:
    src = ROOT / "src" / "just_makeit" / "examples" / folder / "README.md"
    dst = DEST / out_name
    if not src.exists():
        print(f"  skip  {src} (not found)")
        continue
    shutil.copy2(src, dst)
    print(f"  copy  examples/{folder}/README.md → docs/examples/{out_name}")
