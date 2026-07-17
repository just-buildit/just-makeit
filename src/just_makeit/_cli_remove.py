"""_cli_remove.py — argument parsing for `just-makeit remove`.

    just-makeit remove object   <name>
    just-makeit remove module   <name>
    just-makeit remove method   <name> --object <obj>
    just-makeit remove property <name> --object <obj>
    just-makeit remove warning  <condition> --object <obj>
    just-makeit remove error    <obj> --object <obj>
    just-makeit remove function <name> --module <mod>

`--force` (`-f`) skips the confirmation prompt.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import _remove

# gh-490: `warning` and `error` shipped as authoring commands with no removal
# path, leaving hand-editing the TOML as the only undo — which cuts against
# the manifest-is-SSOT/CLI-drives-it contract those features exist to uphold.
#
# Their addressing differs from the rest, and deliberately:
#   warning <condition>  — a warning has no name; its condition is what makes
#                          it unique on a component (the key `jm warning`
#                          de-duplicates on).
#   error <obj>          — takes no name at all: create() has one failure
#                          channel, so there is one translation to remove.
#                          `name` is ignored; --object is what identifies it.
_KINDS = (
    "object",
    "module",
    "method",
    "property",
    "warning",
    "error",
    "function",
    "state",
)


def run(args: list[str]) -> None:
    if len(args) < 2 or args[0] not in _KINDS:
        print(
            "error: 'remove' requires a kind and a name.\n"
            "  just-makeit remove object|module|state|method|property|"
            "warning|error|function <name> [options]",
            file=sys.stderr,
        )
        sys.exit(1)

    kind = args[0]
    name = args[1]
    module: str | None = None
    object_name: str | None = None
    force = False

    rest = args[2:]
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--module":
            i += 1
            if i >= len(rest):
                print("error: --module requires a name", file=sys.stderr)
                sys.exit(1)
            module = rest[i]
        elif tok == "--object":
            i += 1
            if i >= len(rest):
                print("error: --object requires a name", file=sys.stderr)
                sys.exit(1)
            object_name = rest[i]
        elif tok in ("--force", "-f"):
            force = True
        else:
            print(f"error: unknown option '{tok}'", file=sys.stderr)
            sys.exit(1)
        i += 1

    _remove.run(
        Path.cwd(),
        kind,
        name,
        module=module,
        object_name=object_name,
        force=force,
    )
