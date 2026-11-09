"""<<project>> — <<name>>: <<function>>() command-line interface.

Scaffolded by just-makeit. Regenerated from `[[app]]` by `just-makeit app` AND
by every `just-makeit apply` — edits here are discarded. Put custom logic in
a component and call it from here.
Install:  pip install -e .   Run:  <<name>> --help
"""

import argparse

<<fn_import_console>>


def _make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="<<name>>",
<<description_arg>>
    )
<<argparse_state_args>>
    return p


def main() -> None:
    args = _make_parser().parse_args()
<<py_call>>
    print(result)


if __name__ == "__main__":
    main()
