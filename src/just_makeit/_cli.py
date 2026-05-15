"""_cli.py — just-makeit command-line interface."""

import sys
from pathlib import Path


_USAGE = """\
Usage: just-makeit <command> [options]

Commands:
  new <proj> [dir] [OPTIONS]    Create a new project scaffold.
    --object name               Also scaffold a standalone object; repeatable.
    --module name               Also scaffold an extension module; repeatable.
    --state name:type[:default] Initial state variable; repeatable.
    --arg-type TYPE             step() input type (default: float _Complex).
    --return-type TYPE          step() return type (default: --arg-type).
    --perf                      Annotate step() with JM_HOT/JM_FORCEINLINE.
    --mutable                   Remove const from state pointer in step().
    --basic                     Emit a plain Makefile instead of CMake.
    --pytest                    Generate pure pytest tests (no unittest shim).
    --pytest-benchmark          Generate pytest-benchmark bench files.

  module <name>                 Add an extension module subpackage to a project.

  object <name> [OPTIONS]       Add a Python-wrapped C type to a project.
    --module name               Place object inside this module's .so.
    --state name:type[:default] State variable; repeatable.
    --arg-type TYPE             step() input type (default: float _Complex).
    --return-type TYPE          step() return type (default: --arg-type).
    --perf                      Annotate step() with JM_HOT/JM_FORCEINLINE.
    --mutable                   Remove const from state pointer in step().
    --no-state                  Generate empty state struct; user fills in fields manually.
    --no-step                   Omit step() method.
    --init-param name:type[:default]  Constructor param for --no-state objects; repeatable.
    --impl file::funcname       Lift step() body from funcname in file.
    --replace old::new          String substitution on --impl body; repeatable.

  method <obj> <name> [OPTIONS] Add a named execute variant to an object.
    --module name               Module the object lives in.
    --param name:type           Input parameter; repeatable.
    --arg-type TYPE             Bulk-input array type.
    --return-type TYPE          Return type.
    --variable-output           Output length determined at runtime.
    --multi-output TYPE         Emit a second output array of this type.
    --out-type TYPE             Allocate an output array per call; length = in_len / out-divisor.
    --out-divisor N             Divide input length by N for output array (default: 1).
    --batch                     Generate 1:1-rate array transform (allocates output per call).
    --impl file::funcname       Lift method body from funcname in file.
    --replace old::new          String substitution on --impl body; repeatable.

  property <obj> <name> [OPTIONS]  Add a Python property to an object.
    --module name               Module the object lives in.
    --type TYPE                 C type of the property value.
    --writable                  Generate a setter in addition to the getter.
    --field                     Back property with a struct field (no getter C fn).

  function <name> [OPTIONS]     Add a module-level C function.
    --module name               Module to add the function to (required).
    --param name:type           Input parameter; repeatable.
    --return-type TYPE          Return type (default: void).
    --doc "text"                Docstring shown in Python help().
    --impl file::funcname       Lift function body from funcname in file.
    --replace old::new          String substitution on --impl body; repeatable.

  add [OPTIONS]                 Append variables to the current object.
    --state name:type[:default] Add a state variable.
    --param name:type[:default] Add a constructor parameter.

  perf                          Retrofit JM_HOT/JM_FORCEINLINE without touching user code.
  script                        Print a shell script that fully reconstructs this project via CLI.
  config [key value]            Show all config keys, or get/set one value.
  build [dir]                   Build C extensions and package a wheel (default: dist/).
  test                          Build then run CTest + pytest.
  dry-run                       Show what would be compiled without building.
  install-deps [path]           Install cmake, C compiler, numpy, and create a venv.
  example [name]                Run a bundled end-to-end example (omit name to list).
  version                       Show just-makeit's version.
  help                          Show this message.

Types (--arg-type / --return-type / --param / --state):
  void  float  double  float _Complex  double _Complex
  int  int8_t…int64_t  uint8_t…uint64_t  size_t  ptrdiff_t
  Append [] for array params: float _Complex[]  int16_t[]  …
  Append [N] for fixed-length state fields: float[64]  double _Complex[32]

Examples:
  just-makeit new my_filter                                # project scaffold only
  just-makeit new my_filter --object my_filter            # project + first object
  just-makeit new my_bpf --object bpf --state center:double --state bw:double
  just-makeit new my_filters --module filter              # project + one module
  just-makeit new my_dsp --module osc --module env        # project + two modules
  just-makeit object sink --arg-type "float _Complex" --return-type void  # sink object
  just-makeit object gen  --arg-type void --return-type "float _Complex"  # read-only generator
  just-makeit object nco  --arg-type void --return-type "float _Complex" --mutable  # mutating generator (NCO, counter)
  just-makeit object engine --state rate:double:1.0       # standalone stateful object
  just-makeit object norm --state scale:double:1.0        # object with one state var
  just-makeit object fir --module filter                  # object in a module
  just-makeit method nco configure --module dsp \\
      --param freq:float --param phase:float --return-type void
  just-makeit method resamp execute_ctrl --module dsp \\
      --param ctrl:"float _Complex[]" --return-type size_t
  just-makeit method nco execute_cf32 --module dsp \\
      --arg-type void --return-type "float _Complex" --variable-output
  just-makeit method nco execute_u32_ovf --module dsp \\
      --arg-type void --return-type uint32_t --variable-output --multi-output uint8_t
  just-makeit function apply_window --module fft \\
      --param data:"float _Complex[]" --return-type void
  just-makeit property nco phase --module dsp --type uint32_t
  just-makeit property buffer dropped --type size_t
  just-makeit add --state order:int:4                     # add state var
  just-makeit add --param n_taps:int:16                   # add constructor parameter
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

    if cmd == "new":
        from ._cli_new import run as _cmd_new
        _cmd_new(args[1:])

    elif cmd == "module":
        if len(args) < 2:
            print("error: 'module' requires a module name.", file=sys.stderr)
            sys.exit(1)
        from . import _module
        _module.run(Path.cwd(), args[1])

    elif cmd == "object":
        from ._cli_object import run as _cmd_object
        _cmd_object(args[1:])

    elif cmd == "method":
        from ._cli_method import run as _cmd_method
        _cmd_method(args[1:])

    elif cmd == "property":
        if len(args) < 3:
            print(
                "error: 'property' requires an object name and a property name.",
                file=sys.stderr,
            )
            sys.exit(1)
        from . import _property
        from . import _templates as T

        object_name = args[1]
        prop_name = args[2]
        module = None
        ctype = "size_t"
        writable = False
        field = False

        remaining = args[3:]
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
            elif tok == "--type":
                i += 1
                if i >= len(remaining):
                    print("error: --type requires a type", file=sys.stderr)
                    sys.exit(1)
                val = remaining[i]
                if val not in T._CTYPE_META:
                    print(
                        f"error: --type '{val}' is not a supported scalar type.\n"
                        f"Supported: {', '.join(sorted(T._CTYPE_META))}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                ctype = val
                i += 1
            elif tok == "--writable":
                writable = True
                i += 1
            elif tok == "--field":
                field = True
                i += 1
            else:
                print(f"error: unexpected argument '{tok}'", file=sys.stderr)
                sys.exit(1)

        _property.run(
            Path.cwd(), object_name, prop_name, module, ctype, writable, field
        )

    elif cmd == "function":
        from ._cli_function import run as _cmd_function
        _cmd_function(args[1:])

    elif cmd == "add":
        from . import _add
        from ._cli_parse import parse_state_flag

        component = None
        state_vars: list[tuple[str, str, str]] = []
        remaining = args[1:]
        i = 0
        while i < len(remaining):
            tok = remaining[i]
            if tok == "--object":
                i += 1
                if i >= len(remaining):
                    print("error: --object requires a name", file=sys.stderr)
                    sys.exit(1)
                component = remaining[i]
                i += 1
            elif tok in ("--state", "--param"):
                var, i = parse_state_flag(remaining, i)
                state_vars.append(var)
            else:
                print(f"error: unexpected argument '{tok}'", file=sys.stderr)
                sys.exit(1)

        if not state_vars:
            print(
                "error: 'add' requires at least one --state or --param flag.",
                file=sys.stderr,
            )
            sys.exit(1)

        _add.run(Path.cwd(), component, state_vars)

    elif cmd == "perf":
        from . import _perf
        _perf.run(Path.cwd())

    elif cmd == "script":
        from . import _script
        _script.run(Path.cwd())

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

    elif cmd == "install-deps":
        from . import _scripts
        sys.argv = [sys.argv[0]] + args[1:]
        _scripts.install_deps()

    elif cmd == "example":
        from . import _example
        _example.run(args[1] if len(args) > 1 else None)

    elif cmd == "version":
        from . import __version__
        print(__version__)

    else:
        print(f"just-makeit: unknown command '{cmd}'", file=sys.stderr)
        print("Run 'just-makeit help' for usage.", file=sys.stderr)
        sys.exit(1)
