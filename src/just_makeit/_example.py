"""
_example.py — `just-makeit example` command.

Runs a bundled example end-to-end (scaffold -> implement -> cmake build ->
test) inside a temporary directory, printing live output.

    just-makeit example fir_filter
    just-makeit example              # list available examples
"""

import subprocess
import sys
from pathlib import Path

_EXAMPLES = [
    "array_processing",
    "dsp_toolkit",
    "filter_module",
    "fir_filter",
    "iqfile",
    "running_stats",
    "sliding_correlator",
    "sliding_power",
]


def _find(name: str) -> Path | None:
    """Return path to the named example directory, or None."""
    import just_makeit

    pkg = Path(just_makeit.__file__).parent

    # Wheel install: examples bundled alongside the package via force-include.
    bundled = pkg / "examples" / name
    if bundled.is_dir() and (bundled / "test.py").exists():
        return bundled

    # Editable / development install: examples/ lives at the repo root.
    dev = pkg.parent.parent / "examples" / name
    if dev.is_dir() and (dev / "test.py").exists():
        return dev

    return None


def run(name: str | None) -> None:
    if name is None:
        print("Available examples:")
        for ex in _EXAMPLES:
            print(f"  {ex}")
        print("\nUsage: just-makeit example <name>")
        return

    example_dir = _find(name)
    if example_dir is None:
        print(f"error: example '{name}' not found.", file=sys.stderr)
        print(f"Available: {', '.join(_EXAMPLES)}", file=sys.stderr)
        sys.exit(1)

    print(f"just-makeit: running example '{name}'")
    print(f"  source: {example_dir}")
    print(flush=True)
    r = subprocess.run([sys.executable, str(example_dir / "test.py")])
    sys.exit(r.returncode)
