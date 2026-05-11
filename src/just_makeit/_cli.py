"""
_cli.py — just-makeit command-line interface.
"""

import sys
from pathlib import Path


_USAGE = """\
Usage: just-makeit <command> [options]

Commands:
  new <proj> [dir] [--object name] [--state name:type[:default] ...] [--basic] [--perf] [--pure]
             [--arg-type TYPE] [--return-type TYPE]
             [--module name ...]
                     Create a new project; optionally scaffold a first object or one or more modules
                     --object name      scaffold a standalone object (.so) in the same step
                     --module name      scaffold an empty extension module; repeatable
                     --basic uses a plain Makefile instead of CMake
                     --perf generates jm_perf.h with compiler-hint macros (JM_HOT, JM_LIKELY, …)
                     --pure generates a stateless object (scalar params or caller-managed struct)
                     --arg-type TYPE    C type for step()/fn() input x (default: float _Complex)
                     --return-type TYPE C type for step()/fn() return value (default: --arg-type)
  module <name>      Scaffold a new Python extension module (a subpackage .so that
                     hosts multiple types added via 'object')
  object <name> [--module name] [--state|--param name:type[:default] ...] [--perf] [--pure]
             [--arg-type TYPE] [--return-type TYPE]
                     Add a Python type to the project
                     Without --module: standalone object with its own .so
                     With --module:    type grouped into a shared module subpackage .so
  method <object> <method_name> [--module name]
             [--param name:type ...] --return-type TYPE [--variable-output]
             [--arg-type TYPE] [--multi-output TYPE ...]
                     Add a named execute method to an existing object
                     --param name:type  Named typed parameter (repeatable; use instead of --arg-type)
                     --variable-output  Pre-allocates output buffer at init; returns zero-copy view
                     --multi-output T   Additional return types (produces a tuple); repeatable
  property <object> <prop_name> [--module name] --type TYPE [--writable]
                     Add a read-only (or read-write) Python property to an existing object
  function <name> --module <mod> [--param name:type ...] [--return-type TYPE] [--doc "text"]
                     Add a module-level function (no type object) to an existing module
  add --state|--param name:type[:default] [--object name] [...]
                     Add state/param variables to an existing standalone object

  perf               Upgrade an existing project to use JM_FORCEINLINE / JM_HOT
                     annotations without overwriting any user code
  config [key value] Show or edit project configuration
  build [dir]        Configure + build C, then package wheel into dir (default: dist/)
  test               Build and run CTest + pytest
  dry-run            Show what would get compiled without building
  install-deps [path]
                     Install cmake, a C compiler, and numpy; create a venv at path
                     (default: /tmp/jm-venv on Linux/macOS, %%LOCALAPPDATA%%\\jm-venv on Windows)
                     Pass --check to report status without making changes
  example [name]     Run a bundled end-to-end example (scaffold -> build -> test)
                     Omit name to list available examples
  help               Show this message

Scalar types: double (default), float, int, int8_t…int64_t, uint8_t…uint64_t,
              size_t, ptrdiff_t, float _Complex, double _Complex, long double _Complex
Array types:  type[N]  e.g. float[64], double _Complex[32]
              Array fields are always zero-initialised; no default may be given.

Pure mode auto-detection:
  All scalar --param vars  -> scalar style: params passed per call, module functions
                             e.g. normalize(x, scale=1.0); normalize.steps(arr)
  Any array --param var    -> struct style: caller-managed params_t, alloc helpers
                             e.g. f = MyComp(cutoff=440.0); f(x); f.steps(arr)

Examples:
  just-makeit new my_filter                                # project scaffold only
  just-makeit new my_filter --object my_filter            # project + first object
  just-makeit new my_bpf --object bpf --state center:double --state bw:double
  just-makeit new my_filters --module filter              # project + one module
  just-makeit new my_dsp --module osc --module env        # project + two modules
  just-makeit object engine --state rate:double:1.0       # standalone stateful object
  just-makeit object norm --pure --param scale:double:1.0 # scalar pure object
  just-makeit object fir --module filter                  # object in a module
  just-makeit method nco execute_cf32 --module dsp \\
      --arg-type void --return-type "float _Complex" --variable-output
  just-makeit method nco execute_u32_ovf --module dsp \\
      --arg-type void --return-type uint32_t --variable-output --multi-output uint8_t
  just-makeit property nco phase --module dsp --type uint32_t
  just-makeit property buffer dropped --type size_t
  just-makeit add --state order:int:4                     # add state var
  just-makeit add --param n_taps:int:16                   # add param (pure object)
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
        object_name = None
        modules: list[str] = []
        basic = False
        perf = False
        pure = False
        arg_type = "float _Complex"
        return_type = None
        state_vars: list[tuple[str, str, str]] = []
        array_args_new: list[tuple[str, str]] = []

        remaining = args[2:]
        i = 0
        while i < len(remaining):
            tok = remaining[i]
            if tok == "--object":
                i += 1
                if i >= len(remaining):
                    print("error: --object requires a name", file=sys.stderr)
                    sys.exit(1)
                object_name = remaining[i]
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
                if val != "void" and val not in T._CTYPE_META:
                    print(f"error: {tok} '{val}' is not a supported scalar type.\n"
                          f"Supported: void, {', '.join(sorted(T._CTYPE_META))}",
                          file=sys.stderr)
                    sys.exit(1)
                if tok == "--arg-type":
                    arg_type = val
                else:
                    return_type = val
                i += 1
            elif tok == "--array-arg":
                i += 1
                if i >= len(remaining):
                    print("error: --array-arg requires name:dtype", file=sys.stderr)
                    sys.exit(1)
                from . import _templates as T
                val = remaining[i]
                if ":" not in val:
                    print(f"error: --array-arg '{val}' must be name:dtype",
                          file=sys.stderr)
                    sys.exit(1)
                aa_name, aa_dtype = val.split(":", 1)
                if aa_dtype not in T.SUPPORTED_ARRAY_DTYPES:
                    print(
                        f"error: --array-arg dtype '{aa_dtype}' not supported.\n"
                        f"Supported: {', '.join(sorted(T.SUPPORTED_ARRAY_DTYPES))}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                array_args_new.append((aa_name, aa_dtype))
                i += 1
            elif dest is None and not tok.startswith("-"):
                dest = Path(tok)
                i += 1
            else:
                print(f"error: unexpected argument '{tok}'", file=sys.stderr)
                sys.exit(1)

        _new.run(project, dest, object_name, state_vars or None, modules=modules,
                 basic=basic, perf=perf, pure=pure,
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
        array_args_obj: list[tuple[str, str]] = []

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
                if val != "void" and val not in T._CTYPE_META:
                    print(f"error: {tok} '{val}' is not a supported scalar type.\n"
                          f"Supported: void, {', '.join(sorted(T._CTYPE_META))}",
                          file=sys.stderr)
                    sys.exit(1)
                if tok == "--arg-type":
                    arg_type = val
                else:
                    return_type = val
                i += 1
            elif tok == "--array-arg":
                i += 1
                if i >= len(remaining):
                    print("error: --array-arg requires name:dtype", file=sys.stderr)
                    sys.exit(1)
                from . import _templates as T
                val = remaining[i]
                if ":" not in val:
                    print(f"error: --array-arg '{val}' must be name:dtype",
                          file=sys.stderr)
                    sys.exit(1)
                aa_name, aa_dtype = val.split(":", 1)
                if aa_dtype not in T.SUPPORTED_ARRAY_DTYPES:
                    print(
                        f"error: --array-arg dtype '{aa_dtype}' not supported.\n"
                        f"Supported: {', '.join(sorted(T.SUPPORTED_ARRAY_DTYPES))}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                array_args_obj.append((aa_name, aa_dtype))
                i += 1
            else:
                print(f"error: unexpected argument '{tok}'", file=sys.stderr)
                sys.exit(1)

        _object.run(Path.cwd(), object_name, module, state_vars or None,
                    perf=perf, pure=pure, arg_type=arg_type, return_type=return_type,
                    array_args=array_args_obj)

    elif cmd == "method":
        if len(args) < 3:
            print("error: 'method' requires an object name and a method name.", file=sys.stderr)
            sys.exit(1)
        from . import _method
        from . import _templates as T

        object_name = args[1]
        method_name = args[2]
        module = None
        arg_type = "void"
        return_type = "float _Complex"
        variable_output = False
        multi_output: list[str] = []
        method_params: list[tuple[str, str]] = []

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
            elif tok == "--variable-output":
                variable_output = True
                i += 1
            elif tok == "--multi-output":
                i += 1
                if i >= len(remaining):
                    print("error: --multi-output requires a type", file=sys.stderr)
                    sys.exit(1)
                val = remaining[i]
                if val not in T._CTYPE_META:
                    print(f"error: --multi-output '{val}' is not a supported type.",
                          file=sys.stderr)
                    sys.exit(1)
                multi_output.append(val)
                i += 1
            elif tok == "--param":
                i += 1
                if i >= len(remaining):
                    print("error: --param requires name:type", file=sys.stderr)
                    sys.exit(1)
                val = remaining[i]
                if ":" not in val:
                    print(f"error: --param '{val}' must be name:type", file=sys.stderr)
                    sys.exit(1)
                pname, ptype = val.split(":", 1)
                if ptype not in T._CTYPE_META:
                    print(
                        f"error: --param type '{ptype}' is not a supported type.\n"
                        f"Supported: {', '.join(sorted(T._CTYPE_META))}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                method_params.append((pname, ptype))
                i += 1
            elif tok in ("--arg-type", "--return-type"):
                i += 1
                if i >= len(remaining):
                    print(f"error: {tok} requires a type", file=sys.stderr)
                    sys.exit(1)
                val = remaining[i]
                if val != "void" and val not in T._CTYPE_META:
                    print(f"error: {tok} '{val}' is not a supported scalar type.\n"
                          f"Supported: void, {', '.join(sorted(T._CTYPE_META))}",
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

        if return_type != "void" and return_type not in T._CTYPE_META:
            print(f"error: --return-type '{return_type}' must be void or a scalar type.\n"
                  f"Supported: void, {', '.join(sorted(T._CTYPE_META))}",
                  file=sys.stderr)
            sys.exit(1)

        _method.run(
            Path.cwd(), object_name, method_name, module,
            arg_type, return_type, variable_output, multi_output,
            params=method_params,
        )

    elif cmd == "property":
        if len(args) < 3:
            print("error: 'property' requires an object name and a property name.",
                  file=sys.stderr)
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
                    print(f"error: --type '{val}' is not a supported scalar type.\n"
                          f"Supported: {', '.join(sorted(T._CTYPE_META))}",
                          file=sys.stderr)
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
        if len(args) < 2:
            print("error: 'function' requires a function name.", file=sys.stderr)
            sys.exit(1)
        from . import _function
        from . import _templates as T

        fn_name = args[1]
        module = None
        doc = ""
        fn_params: list[tuple[str, str]] = []
        fn_return_type = "void"

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
            elif tok == "--doc":
                i += 1
                if i >= len(remaining):
                    print("error: --doc requires a string", file=sys.stderr)
                    sys.exit(1)
                doc = remaining[i]
                i += 1
            elif tok == "--param":
                i += 1
                if i >= len(remaining):
                    print("error: --param requires name:type", file=sys.stderr)
                    sys.exit(1)
                val = remaining[i]
                if ":" not in val:
                    print(f"error: --param '{val}' must be name:type", file=sys.stderr)
                    sys.exit(1)
                pname, ptype = val.split(":", 1)
                if ptype not in T._CTYPE_META:
                    print(
                        f"error: --param type '{ptype}' is not a supported type.\n"
                        f"Supported: {', '.join(sorted(T._CTYPE_META))}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                fn_params.append((pname, ptype))
                i += 1
            elif tok == "--return-type":
                i += 1
                if i >= len(remaining):
                    print("error: --return-type requires a type", file=sys.stderr)
                    sys.exit(1)
                val = remaining[i]
                if val != "void" and val not in T._CTYPE_META:
                    print(
                        f"error: --return-type '{val}' must be void or a scalar type.\n"
                        f"Supported: void, {', '.join(sorted(T._CTYPE_META))}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                fn_return_type = val
                i += 1
            else:
                print(f"error: unexpected argument '{tok}'", file=sys.stderr)
                sys.exit(1)

        if module is None:
            print(
                "error: 'function' requires --module (functions must belong to a module).",
                file=sys.stderr,
            )
            sys.exit(1)

        _function.run(Path.cwd(), fn_name, module, doc,
                      params=fn_params, return_type=fn_return_type)

    elif cmd == "add":
        from . import _add

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

    elif cmd == "install-deps":
        from . import _scripts
        sys.argv = [sys.argv[0]] + args[1:]
        _scripts.install_deps()

    elif cmd == "example":
        from . import _example
        _example.run(args[1] if len(args) > 1 else None)

    else:
        print(f"just-makeit: unknown command '{cmd}'", file=sys.stderr)
        print("Run 'just-makeit help' for usage.", file=sys.stderr)
        sys.exit(1)
