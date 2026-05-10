"""
_cli.py — just-makeit command-line interface.
"""

import sys
from pathlib import Path


_USAGE = """\
Usage: just-makeit <command> [options]

Commands:
  new <proj> [dir] [--component name] [--state name:type[:default] ...] [--basic] [--perf] [--pure]
             [--arg-type TYPE] [--return-type TYPE]
             [--module name ...]
                     Create a new project; optionally scaffold a first component or one or more modules
                     --component name   scaffold a standalone component (.so) in the same step
                     --module name      scaffold an empty extension module; repeatable
                     --basic uses a plain Makefile instead of CMake
                     --perf generates jm_perf.h with compiler-hint macros (JM_HOT, JM_LIKELY, …)
                     --pure generates a stateless component (scalar params or caller-managed struct)
                     --arg-type TYPE    C type for step()/fn() input x (default: float _Complex)
                     --return-type TYPE C type for step()/fn() return value (default: --arg-type)
  init <name> [--state|--param name:type[:default] ...] [--perf] [--pure]
             [--arg-type TYPE] [--return-type TYPE]
                     Add a standalone component (its own .so) to the project
                     --perf enables performance hints (inherited from project config by default)
                     --pure generates a stateless component (auto-detected: scalar or struct)
                     --param is idiomatic with --pure; --state is idiomatic without it (both work)
                     --arg-type / --return-type set the step() I/O C types
  module <name>      Scaffold a new Python extension module (a subpackage .so that
                     hosts multiple types added via 'object')
  object <name> [--module name] [--state|--param name:type[:default] ...] [--perf] [--pure]
             [--arg-type TYPE] [--return-type TYPE]
                     Add a Python type to an existing module
                     --module is inferred when only one module exists
  add --state|--param name:type[:default] [--component name] [...]
                     Add state/param variables to an existing standalone component

  perf               Upgrade an existing project to use JM_FORCEINLINE / JM_HOT
                     annotations without overwriting any user code
  config [key value] Show or edit project configuration
  build [dir]        Configure + build C, then package wheel into dir (default: dist/)
  test               Build and run CTest + pytest
  dry-run            Show what would get compiled without building
  help               Show this message

Scalar types: double (default), float, int, int8_t…int64_t, uint8_t…uint64_t,
              size_t, ptrdiff_t, float _Complex, double _Complex, long double _Complex
Array types:  type[N]  e.g. float[64], double _Complex[32]
              Array fields are always zero-initialised; no default may be given.

Pure mode auto-detection:
  All scalar --param vars  → scalar style: params passed per call, module functions
                             e.g. normalize(x, scale=1.0); normalize.steps(arr)
  Any array --param var    → struct style: caller-managed params_t, alloc helpers
                             e.g. f = MyComp(cutoff=440.0); f(x); f.steps(arr)

Examples:
  just-makeit new my_filter                                # project scaffold only
  just-makeit new my_filter --component my_filter         # project + first component
  just-makeit new my_bpf --component bpf --state center:double --state bw:double
  just-makeit new my_filters --module filter              # project + one module
  just-makeit new my_dsp --module osc --module env        # project + two modules
  just-makeit init engine --state rate:double:1.0         # stateful component
  just-makeit init norm --pure --param scale:double:1.0   # scalar pure component
  just-makeit init fir --pure --param taps:float[64]      # struct pure (array → struct)
  just-makeit add --state order:int:4                     # add state var (stateful)
  just-makeit add --param n_taps:int:16                   # add param (pure component)
  just-makeit config                                      # show project config
  just-makeit config version 0.2.0                        # set version
  just-makeit build                                       # build wheel into dist/
  just-makeit test                                        # run all tests
  just-makeit dry-run                                     # preview build plan
"""


def _parse_state_flags(
    remaining: list[str], i: int
) -> tuple[list[tuple[str, str, str]], int]:
    """Parse one --state flag starting at index i. Returns (vars, new_i)."""
    from . import _templates as T

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
    if not T.is_valid_type(ctype):
        supported = ", ".join(sorted(T.SUPPORTED_TYPES))
        print(
            f"error: unsupported type '{ctype}'.\n"
            f"Scalar types: {supported}\n"
            f"Array syntax: type[N]  e.g. float[64]",
            file=sys.stderr,
        )
        sys.exit(1)
    arr = T.parse_array_type(ctype)
    if arr is not None:
        if len(parts) == 3:
            print(
                f"warning: default ignored for array type '{ctype}' "
                f"(arrays are always zero-initialised)",
                file=sys.stderr,
            )
        default = ""
    else:
        default = parts[2] if len(parts) == 3 else T._CTYPE_META[ctype]["zero"]
    return (name, ctype, default), i + 1


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(_USAGE, end="")
        return

    cmd = args[0]

    if cmd == "new":
        if len(args) < 2:
            print("error: 'new' requires a project name.", file=sys.stderr)
            sys.exit(1)
        from . import _new

        project = args[1]
        dest = None
        component = None
        modules: list[str] = []
        basic = False
        perf = False
        pure = False
        arg_type = "float _Complex"
        return_type = None
        state_vars: list[tuple[str, str, str]] = []

        remaining = args[2:]
        i = 0
        while i < len(remaining):
            tok = remaining[i]
            if tok == "--component":
                i += 1
                if i >= len(remaining):
                    print("error: --component requires a name", file=sys.stderr)
                    sys.exit(1)
                component = remaining[i]
                i += 1
            elif tok == "--module":
                i += 1
                if i >= len(remaining):
                    print("error: --module requires a name", file=sys.stderr)
                    sys.exit(1)
                modules.append(remaining[i])
                i += 1
            elif tok in ("--state", "--param"):
                var, i = _parse_state_flags(remaining, i)
                state_vars.append(var)
            elif tok == "--basic":
                basic = True
                i += 1
            elif tok == "--perf":
                perf = True
                i += 1
            elif tok == "--pure":
                pure = True
                i += 1
            elif tok in ("--arg-type", "--return-type"):
                i += 1
                if i >= len(remaining):
                    print(f"error: {tok} requires a type", file=sys.stderr)
                    sys.exit(1)
                from . import _templates as T
                val = remaining[i]
                if val not in T._CTYPE_META:
                    print(f"error: {tok} '{val}' is not a supported scalar type.\n"
                          f"Supported: {', '.join(sorted(T._CTYPE_META))}",
                          file=sys.stderr)
                    sys.exit(1)
                if tok == "--arg-type":
                    arg_type = val
                else:
                    return_type = val
                i += 1
            elif dest is None and not tok.startswith("-"):
                dest = Path(tok)
                i += 1
            else:
                print(f"error: unexpected argument '{tok}'", file=sys.stderr)
                sys.exit(1)

        _new.run(project, dest, component, state_vars or None, modules=modules,
                 basic=basic, perf=perf, pure=pure,
                 arg_type=arg_type, return_type=return_type)

    elif cmd == "init":
        if len(args) < 2:
            print("error: 'init' requires a component name.", file=sys.stderr)
            print(
                "Usage: just-makeit init <name> [--state name:type ...]",
                file=sys.stderr,
            )
            sys.exit(1)
        from . import _init

        component = args[1]
        perf: bool | None = None
        pure = False
        arg_type = "float _Complex"
        return_type = None
        state_vars: list[tuple[str, str, str]] = []

        remaining = args[2:]
        i = 0
        while i < len(remaining):
            tok = remaining[i]
            if tok in ("--state", "--param"):
                var, i = _parse_state_flags(remaining, i)
                state_vars.append(var)
            elif tok == "--perf":
                perf = True
                i += 1
            elif tok == "--pure":
                pure = True
                i += 1
            elif tok in ("--arg-type", "--return-type"):
                i += 1
                if i >= len(remaining):
                    print(f"error: {tok} requires a type", file=sys.stderr)
                    sys.exit(1)
                from . import _templates as T
                val = remaining[i]
                if val not in T._CTYPE_META:
                    print(f"error: {tok} '{val}' is not a supported scalar type.\n"
                          f"Supported: {', '.join(sorted(T._CTYPE_META))}",
                          file=sys.stderr)
                    sys.exit(1)
                if tok == "--arg-type":
                    arg_type = val
                else:
                    return_type = val
                i += 1
            else:
                print(f"error: unexpected argument '{tok}'", file=sys.stderr)
                sys.exit(1)

        _init.run(Path.cwd(), component, state_vars or None, perf=perf, pure=pure,
                  arg_type=arg_type, return_type=return_type)

    elif cmd == "module":
        if len(args) < 2:
            print("error: 'module' requires a module name.", file=sys.stderr)
            sys.exit(1)
        from . import _module

        _module.run(Path.cwd(), args[1])

    elif cmd == "object":
        if len(args) < 2:
            print("error: 'object' requires an object name.", file=sys.stderr)
            sys.exit(1)
        from . import _object

        object_name = args[1]
        module = None
        perf: bool | None = None
        pure = False
        arg_type = "float _Complex"
        return_type = None
        state_vars: list[tuple[str, str, str]] = []

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
            elif tok in ("--state", "--param"):
                var, i = _parse_state_flags(remaining, i)
                state_vars.append(var)
            elif tok == "--perf":
                perf = True
                i += 1
            elif tok == "--pure":
                pure = True
                i += 1
            elif tok in ("--arg-type", "--return-type"):
                i += 1
                if i >= len(remaining):
                    print(f"error: {tok} requires a type", file=sys.stderr)
                    sys.exit(1)
                from . import _templates as T
                val = remaining[i]
                if val not in T._CTYPE_META:
                    print(f"error: {tok} '{val}' is not a supported scalar type.\n"
                          f"Supported: {', '.join(sorted(T._CTYPE_META))}",
                          file=sys.stderr)
                    sys.exit(1)
                if tok == "--arg-type":
                    arg_type = val
                else:
                    return_type = val
                i += 1
            else:
                print(f"error: unexpected argument '{tok}'", file=sys.stderr)
                sys.exit(1)

        _object.run(Path.cwd(), object_name, module, state_vars or None,
                    perf=perf, pure=pure, arg_type=arg_type, return_type=return_type)

    elif cmd == "add":
        from . import _add

        component = None
        state_vars: list[tuple[str, str, str]] = []
        remaining = args[1:]
        i = 0
        while i < len(remaining):
            tok = remaining[i]
            if tok == "--component":
                i += 1
                if i >= len(remaining):
                    print("error: --component requires a name", file=sys.stderr)
                    sys.exit(1)
                component = remaining[i]
                i += 1
            elif tok in ("--state", "--param"):
                var, i = _parse_state_flags(remaining, i)
                state_vars.append(var)
            else:
                print(f"error: unexpected argument '{tok}'", file=sys.stderr)
                sys.exit(1)

        if not state_vars:
            print("error: 'add' requires at least one --state or --param flag.", file=sys.stderr)
            sys.exit(1)

        _add.run(Path.cwd(), component, state_vars)

    elif cmd == "perf":
        from . import _perf

        _perf.run(Path.cwd())

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
            proj = cfg.get("project", {})
            print(f"project:  {proj.get('name', '?')}")
            print(f"version:  {proj.get('version', '0.1.0')}")
            for comp in C.components(cfg):
                print(f"\n{comp}:")
                for s in cfg[comp].get("state", []):
                    print(f"  {s['name']}:  {s['type']} = {s['default']}")
        elif len(args) == 3:
            key, value = args[1], args[2]
            if key == "version":
                cfg.setdefault("project", {})["version"] = value
                C.save(root, cfg)
                print(f"version = {value!r}")
            else:
                print(f"error: unknown config key '{key}'", file=sys.stderr)
                sys.exit(1)
        else:
            print(
                "Usage: just-makeit config [key value]\nSupported keys: version",
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
