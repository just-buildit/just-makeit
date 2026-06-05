"""<<project>> — <<name>>: <<function>>() command-line interface.

Scaffolded by just-makeit.  Re-running `just-makeit app` overwrites this file.
Install:  pip install -e .   Run:  <<name>> --help
"""

import argparse

from .<<module>> import <<function>>


def _make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="<<name>>",
        description="<<project>>: <<function>>() command-line tool.",
    )
<<argparse_state_args>>
    return p


def main() -> None:
    args = _make_parser().parse_args()
    print(<<function>>(<<py_call_args>>))


if __name__ == "__main__":
    main()
