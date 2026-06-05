# /// script
# requires-python = ">=3.9"
# dependencies = ["<<project>>==<<version>>"]
# ///
"""<<project>> standalone script (PEP 723).

Distribute this file — recipients run it with:
    uv run <<name>>.py --help

Requires <<project>>==<<version>> on PyPI.
Scaffolded by just-makeit.  Implement the processing loop below.
"""

import argparse
import sys

from <<package>> import <<Component>>


def _make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="<<name>>",
        description="<<project>> <<Component>>-powered DSP tool",
    )
    p.add_argument(
        "--input", "-i", metavar="FILE",
        help="Input file (default: stdin)",
    )
    p.add_argument(
        "--output", "-o", metavar="FILE",
        help="Output file (default: stdout)",
    )
<<argparse_state_args>>
    return p


def main() -> None:
    args = _make_parser().parse_args()
    obj = <<Component>>(<<py_create_args>>)

    # <<IMPLEMENT: open input/output, call obj.step() / obj.steps(), write>>
    _ = obj
    sys.exit(0)


if __name__ == "__main__":
    main()
