# /// script
# requires-python = ">=3.9"
# dependencies = ["<<project>>==<<version>>"]
# ///
"""<<project>> — <<name>>: multi-command CLI (PEP 723).

Run with:  uv run <<name>>.py --help    (requires <<project>>==<<version>>)
"""

import argparse

<<command_fns>>

def _make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="<<name>>", description="<<project>> command-line tool."
    )
    sub = p.add_subparsers(dest="command", required=True)
<<subparsers>>
    return p


def main() -> None:
    args = _make_parser().parse_args()
    args._fn(args)


if __name__ == "__main__":
    main()
