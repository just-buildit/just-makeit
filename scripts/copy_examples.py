"""Copy example READMEs into docs/examples/ and generate the index page.

Run from the project root:
    python3 scripts/copy_examples.py

All output files (docs/examples/*.md) are gitignored and regenerated here
before every build. To add a new example:
  1. Add it to the `examples` list below (folder, out_name, display_name).
  2. Add a matching nav entry in zensical.toml.

docs/examples/index.md is also generated here — no manual table needed.
"""

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src" / "just_makeit" / "examples"
DEST = ROOT / "docs" / "examples"
DEST.mkdir(exist_ok=True)

# Single source of truth for published examples (order matches the nav).
# Tuple: (folder, out_name, display_name)
examples = [
    ("running_stats", "running_stats.md", "Running stats"),
    ("fir_filter", "fir_filter.md", "FIR filter"),
    ("sliding_power", "sliding_power.md", "Sliding power"),
    ("sliding_correlator", "sliding_correlator.md", "Sliding correlator"),
    ("array_processing", "array_processing.md", "Array processing"),
    ("stream_chunker", "stream_chunker.md", "Stream chunker"),
    ("dsp_toolkit", "dsp_toolkit.md", "DSP toolkit"),
    ("filter_module", "filter_module.md", "Filter module"),
    ("iqfile", "iqfile.md", "IQ file"),
    ("pytest_style", "pytest_style.md", "pytest style"),
    ("full_workflow", "full_workflow.md", "Full workflow"),
    ("composites", "composites.md", "Composites"),
]


def _first_sentence(md_path: Path) -> str:
    """Return the first meaningful sentence from the first body paragraph."""
    text = md_path.read_text(encoding="utf-8")
    found_heading = False
    para_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not found_heading:
            if stripped.startswith("# "):
                found_heading = True
            continue
        if stripped.startswith(("---", "___", "#")):
            if para_lines:
                break
            continue
        if stripped == "":
            if para_lines:
                break
        else:
            para_lines.append(stripped)

    para = " ".join(para_lines)
    # Trim to first sentence (ends at `. `, `.\n`, or end of string).
    m = re.search(r"\.(\s|$)", para)
    if m:
        para = para[: m.start() + 1]
    # Collapse inline backtick spans (keep them as-is, just tidy whitespace).
    return re.sub(r"\s+", " ", para).strip()


# ── Copy READMEs ─────────────────────────────────────────────────────────────

rows: list[tuple[str, str, str]] = []  # (out_name, display_name, description)

for folder, out_name, display_name in examples:
    src = SRC / folder / "README.md"
    dst = DEST / out_name
    if not src.exists():
        print(f"  skip  {src} (not found)")
        continue
    shutil.copy2(src, dst)
    print(f"  copy  examples/{folder}/README.md → docs/examples/{out_name}")
    description = _first_sentence(dst)
    rows.append((out_name, display_name, description))

# ── Generate index.md ─────────────────────────────────────────────────────────

col1 = max(len(r[1]) + 4 for r in rows) if rows else 20
col2 = max(len(r[2]) for r in rows) if rows else 40

header = (
    "# Examples\n\n"
    "Each example is a complete, buildable project that walks through a real\n"
    "algorithm from scaffold to optimised implementation.\n\n"
)

sep = f"| {'-' * col1} | {'-' * col2} |\n"
head = f"| {'Example':<{col1}} | {'What it demonstrates':<{col2}} |\n"
table = head + sep
for out_name, display_name, description in rows:
    link = f"[{display_name}]({out_name})"
    table += f"| {link:<{col1}} | {description:<{col2}} |\n"

footer = (
    "\nAll examples ship with end-to-end tests in `examples/*/test.py` that are\n"
    "run by the CI suite. See `examples/README.md` for contributor notes on the\n"
    "`.steps/` naming convention.\n"
)

index_path = DEST / "index.md"
index_path.write_text(header + table + footer, encoding="utf-8")
print(f"  gen   docs/examples/index.md  ({len(rows)} examples)")
