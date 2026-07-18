"""CLI handler for `just-makeit view` (gh-504)."""

from __future__ import annotations

import sys
from pathlib import Path


def run(args: list[str]) -> None:
    if len(args) < 2:
        print(
            "error: 'view' requires an object name and a view class name.\n"
            "Usage: just-makeit view <obj> <ViewClassName> --module <mod> "
            "--create-fn <fn> [--init-param ...] [--exclude-property ...]",
            file=sys.stderr,
        )
        sys.exit(1)

    from . import _view
    from ._cli_parse import parse_init_param_flag

    object_name = args[0]
    class_name = args[1]
    module: str | None = None
    create_fn = ""
    doc = ""
    init_params: list[tuple] = []
    exclude_properties: list[str] = []

    remaining = args[2:]
    i = 0
    while i < len(remaining):
        tok = remaining[i]
        if tok == "--module":
            i += 1
            if i >= len(remaining):
                print("error: --module requires a name", file=sys.stderr)
                sys.exit(1)
            module = remaining[i]
            i += 1
        elif tok == "--create-fn":
            i += 1
            if i >= len(remaining):
                print(
                    "error: --create-fn requires a C function name",
                    file=sys.stderr,
                )
                sys.exit(1)
            create_fn = remaining[i]
            i += 1
        elif tok == "--init-param":
            param, i = parse_init_param_flag(remaining, i)
            init_params.append(param)
        elif tok == "--exclude-property":
            i += 1
            if i >= len(remaining):
                print(
                    "error: --exclude-property requires a name",
                    file=sys.stderr,
                )
                sys.exit(1)
            exclude_properties.append(remaining[i])
            i += 1
        elif tok == "--doc":
            i += 1
            if i >= len(remaining):
                print("error: --doc requires a string", file=sys.stderr)
                sys.exit(1)
            doc = remaining[i]
            i += 1
        else:
            print(f"error: unexpected argument '{tok}'", file=sys.stderr)
            sys.exit(1)

    _view.run(
        Path.cwd(),
        object_name,
        class_name,
        module,
        create_fn,
        init_params=init_params,
        exclude_properties=exclude_properties,
        doc=doc,
    )
