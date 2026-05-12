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

def _examples_root() -> Path | None:
    """Return the examples/ directory, searching installed then editable paths."""
    import just_makeit

    pkg = Path(just_makeit.__file__).parent
    for candidate in (pkg / "examples", pkg.parent.parent / "examples"):
        if candidate.is_dir():
            return candidate
    return None


def _discover() -> list[str]:
    """Return sorted list of example names found on disk."""
    root = _examples_root()
    if root is None:
        return []
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and (d / "test.py").exists()
    )


# Kept for callers that import _EXAMPLES directly (e.g. CI).
_EXAMPLES = _discover()


def _find(name: str) -> Path | None:
    """Return path to the named example directory, or None."""
    root = _examples_root()
    if root is None:
        return None
    candidate = root / name
    return candidate if candidate.is_dir() and (candidate / "test.py").exists() else None


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
