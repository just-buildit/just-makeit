"""
_cli.py — just-makeit command-line interface.
"""

from __future__ import annotations

import sys
from pathlib import Path


_USAGE = """\
Usage: just-makeit <command> [options]

Commands:
  init <name> [dir] [--state name:type[:default] ...]
                     Create a new C extension project called <name>
  add --state name:type[:default] [...]
                     Add state variables to the project in the current directory
  config [key value] Show or edit project configuration
  build [dir]        Configure + build C, then package wheel into dir (default: dist/)
  test               Build and run CTest + pytest
  dry-run            Show what would get compiled without building
  help               Show this message

State types: double (default), float, int
Default value is zero for each type if omitted.

Examples:
  just-makeit init my_filter                               # single 'gain:double:0.0' var
  just-makeit init my_filter --state gain:double:1.0      # explicit default
  just-makeit init my_bpf --state center:double --state bw:double
  just-makeit add --state order:int:4                     # add to existing project
  just-makeit config                                      # show project config
  just-makeit config version 0.2.0                        # set version
  just-makeit build                                       # build wheel into dist/
  just-makeit test                                        # run all tests
  just-makeit dry-run                                     # preview build plan
"""


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(_USAGE, end="")
        return

    cmd = args[0]

    if cmd == "init":
        if len(args) < 2:
            print("error: 'init' requires a component name.", file=sys.stderr)
            print(
                "Usage: just-makeit init <name> [dir] [--state name:type ...]",
                file=sys.stderr,
            )
            sys.exit(1)
        from . import _init
        from . import _templates as T

        component = args[1]
        dest = None
        state_vars: list[tuple[str, str]] = []

        remaining = args[2:]
        i = 0
        while i < len(remaining):
            tok = remaining[i]
            if tok == "--state":
                i += 1
                if i >= len(remaining):
                    print("error: --state requires name:type", file=sys.stderr)
                    sys.exit(1)
                spec = remaining[i]
                parts = spec.split(":", 2)
                if len(parts) < 2:
                    print(
                        f"error: --state '{spec}' must be in name:type[:default] format",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                name, ctype = parts[0], parts[1]
                if ctype not in T.SUPPORTED_TYPES:
                    supported = ", ".join(sorted(T.SUPPORTED_TYPES))
                    print(
                        f"error: unsupported type '{ctype}'. Supported: {supported}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                _ZERO = {"double": "0.0", "float": "0.0f", "int": "0"}
                default = parts[2] if len(parts) == 3 else _ZERO[ctype]
                state_vars.append((name, ctype, default))
                i += 1
            elif dest is None and not tok.startswith("-"):
                dest = Path(tok)
                i += 1
            else:
                print(f"error: unexpected argument '{tok}'", file=sys.stderr)
                sys.exit(1)

        _init.run(component, dest, state_vars or None)

    elif cmd == "add":
        from . import _add
        from . import _templates as T

        state_vars: list[tuple[str, str, str]] = []
        remaining = args[1:]
        i = 0
        while i < len(remaining):
            tok = remaining[i]
            if tok == "--state":
                i += 1
                if i >= len(remaining):
                    print("error: --state requires name:type[:default]", file=sys.stderr)
                    sys.exit(1)
                spec = remaining[i]
                parts = spec.split(":", 2)
                if len(parts) < 2:
                    print(
                        f"error: --state '{spec}' must be in name:type[:default] format",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                name, ctype = parts[0], parts[1]
                if ctype not in T.SUPPORTED_TYPES:
                    supported = ", ".join(sorted(T.SUPPORTED_TYPES))
                    print(
                        f"error: unsupported type '{ctype}'. Supported: {supported}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                _ZERO = {"double": "0.0", "float": "0.0f", "int": "0"}
                default = parts[2] if len(parts) == 3 else _ZERO[ctype]
                state_vars.append((name, ctype, default))
                i += 1
            else:
                print(f"error: unexpected argument '{tok}'", file=sys.stderr)
                sys.exit(1)

        if not state_vars:
            print("error: 'add' requires at least one --state flag.", file=sys.stderr)
            sys.exit(1)

        _add.run(Path.cwd(), state_vars)

    elif cmd == "config":
        from . import _config as C

        root = Path.cwd()
        cfg = C.load(root)
        if not cfg:
            print(
                f"error: no {C.FILENAME} found in {root}.",
                file=sys.stderr,
            )
            sys.exit(1)

        if len(args) == 1:
            comp = cfg.get("component", {})
            print(f"component: {comp.get('name', '?')}")
            print(f"version:   {comp.get('version', '0.1.0')}")
            state = cfg.get("state", [])
            if state:
                print("state:")
                for s in state:
                    print(f"  {s['name']}: {s['type']} = {s['default']}")
            else:
                print("state: (none)")
        elif len(args) == 3:
            key, value = args[1], args[2]
            if key == "version":
                cfg.setdefault("component", {})["version"] = value
                C.save(root, cfg)
                print(f"version = {value!r}")
            else:
                print(f"error: unknown config key '{key}'", file=sys.stderr)
                sys.exit(1)
        else:
            print(
                "Usage: just-makeit config [key value]\n"
                "Supported keys: version",
                file=sys.stderr,
            )
            sys.exit(1)

    elif cmd == "build":
        from . import _build
        _build.cmd_build(args[1:])

    elif cmd == "test":
        from . import _build
        _build.cmd_test(args[1:])

    elif cmd == "dry-run":
        from . import _build
        _build.cmd_dry_run()

    else:
        print(f"just-makeit: unknown command '{cmd}'", file=sys.stderr)
        print("Run 'just-makeit help' for usage.", file=sys.stderr)
        sys.exit(1)
