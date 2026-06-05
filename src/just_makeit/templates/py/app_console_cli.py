"""<<project>> — <<name>>: <<Component>> command-line interface.

Scaffolded by just-makeit.  Re-running `just-makeit app` overwrites this file;
edit for custom logic.

Install:  pip install -e .
Run:      <<name>> --help
"""

import argparse
import sys

import numpy as np

from . import <<Component>>


def _make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="<<name>>",
        description="<<project>>: <<Component>>-powered stream tool.",
    )
    p.add_argument(
        "--input", "-i", default=None,
        help="input file (default: stdin)",
    )
    p.add_argument(
        "--output", "-o", default=None,
        help="output file (default: stdout)",
    )
<<argparse_state_args>>
    return p


def main() -> None:
    args = _make_parser().parse_args()
<<py_io_loop>>


if __name__ == "__main__":
    main()
