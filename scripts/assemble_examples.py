"""Re-assemble example READMEs whose ``.steps/`` sources changed.

An example's ``README.md`` is generated, never hand-written: its ``assemble.py``
stitches together the ``.steps/*.md`` narrative files and inlines every script
they reference with the ``{filename}`` fence syntax.  That makes the README a
derived artifact with several inputs -- and nothing re-ran the assembler when
one of those inputs changed.

The failure mode this closes is subtle because the trigger is a *hook*, not a
person: ``ruff format`` rewraps a ``.steps/*.py`` during ``git commit``, the
script on disk changes, and the README goes on quoting the pre-format text.
Nothing local complains.  CI catches it much later via ``assemble.py --check``,
where it surfaces as a ``difflib`` diff of Python inside a markdown fence --
which reads like a formatting-lint failure rather than a staleness one.  (This
cost a full CI cycle on just-makeit PR #577.)

Wired as a pre-commit hook, this script receives the staged paths, maps each
back to the example that owns it, and re-runs that example's assembler exactly
once.  **Ordering matters**: the hook must sit *after* ``ruff-format`` in
``.pre-commit-config.yaml`` so it observes the formatter's rewrites instead of
racing them.  Like any rewriting hook, a regenerated README shows up as
"files were modified by this hook" and the commit is re-run once.

Usage
-----
    python3 scripts/assemble_examples.py <path> [<path> ...]

Paths that do not live under an example's ``.steps/`` directory are ignored, so
the hook is safe to aim at a broad file pattern.

Examples
--------
Re-assemble only the example owning a changed step script::

    $ python3 scripts/assemble_examples.py \
          src/just_makeit/examples/fir_filter/.steps/08_doxygen.py
    wrote /.../src/just_makeit/examples/fir_filter/README.md

Unrelated paths are no-ops, and cost nothing::

    $ python3 scripts/assemble_examples.py README.md src/just_makeit/_cli.py
    $
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def owning_examples(paths: list[str]) -> list[Path]:
    """Map step-source paths back to the example directories that own them.

    A step source lives at ``<example>/.steps/<file>``, so the example root is
    the grandparent of the path.  Results are de-duplicated while preserving
    first-seen order, so editing four scripts in one example still assembles
    that example once.

    Parameters
    ----------
    paths
        Candidate file paths, as handed over by pre-commit.  Anything not
        directly inside a ``.steps`` directory is ignored, as is a ``.steps``
        directory whose example has no ``assemble.py`` (not every directory
        under ``examples/`` is an assembled example).

    Returns
    -------
    list of pathlib.Path
        Example roots to re-assemble, in first-seen order.

    Examples
    --------
    >>> owning_examples(["a/fir/.steps/01.md", "a/fir/.steps/02.py"])  # doctest: +SKIP
    [PosixPath('a/fir')]
    """
    found: dict[Path, None] = {}
    for raw in paths:
        path = Path(raw)
        if path.parent.name != ".steps":
            continue
        example = path.parent.parent
        # `.steps` without an assembler is not an assembled example.
        if (example / "assemble.py").is_file():
            found[example] = None
    return list(found)


def main(argv: list[str]) -> int:
    """Re-assemble every example implicated by *argv*.

    Returns 0 even when a README is rewritten -- pre-commit detects the
    working-tree change itself and fails the commit, exactly as it does for
    ``ruff-format``.  A non-zero exit is reserved for an assembler that
    genuinely errored (e.g. a step file references a script that is missing).
    """
    for example in owning_examples(argv):
        result = subprocess.run(
            [sys.executable, str(example / "assemble.py")],
            capture_output=True,
            text=True,
        )
        sys.stdout.write(result.stdout)
        if result.returncode != 0:
            sys.stderr.write(result.stderr)
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
