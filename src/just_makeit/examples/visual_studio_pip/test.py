"""End-to-end test: the Visual Studio user's workflow on Windows.

gh-2593, split by gh-2610 into one example per way of making the project's
``.venv``: ``visual_studio_pip`` and ``visual_studio_uv``. This file is the
same in both. It runs this example's ``.steps/*.ps1`` -- the PowerShell the
README shows -- line by line, through :mod:`just_makeit._vsexample`, whose
docstring says which lines run as written on Windows and which are replaced
elsewhere, and why.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/<this example>/test.py
"""

from __future__ import annotations

from pathlib import Path

from just_makeit._vsexample import run_example

HERE = Path(__file__).parent


def run(root: Path) -> None:
    run_example(HERE, root)


if __name__ == "__main__":
    from just_makeit._example import scratch_dir

    with scratch_dir() as tmp:
        run(Path(tmp))
    print(f"{HERE.name}: PASSED")
