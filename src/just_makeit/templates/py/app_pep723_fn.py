# /// script
# requires-python = ">=3.9"
# dependencies = ["<<project>>==<<version>>"]
# ///
"""<<project>> — <<name>>: <<function>>() standalone script (PEP 723).

Run with:  uv run <<name>>.py --help    (requires <<project>>==<<version>>)
"""

import argparse

from <<package>>.<<module>> import <<function>>


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
