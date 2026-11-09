# /// script
# requires-python = ">=3.9"
# dependencies = ["<<project>>==<<version>>"]
# ///
"""<<project>> — <<name>>: <<function>>() standalone script (PEP 723).

Run with:  uv run <<name>>.py --help
Requires <<project>>==<<version>>.
"""

import argparse

<<fn_import_pep723>>


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
