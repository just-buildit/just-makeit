"""<<project>> — <<name>>: multi-command CLI (scaffolded by just-makeit).

Regenerated from `[app]` by `just-makeit app` AND by every `just-makeit
apply` — edits here are discarded, command bodies included. Put the
implementation in a component and call it from each body.
Install:  pip install -e .   Run:  <<name>> --help
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
