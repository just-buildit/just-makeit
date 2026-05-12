"""
_cli.py — just-makeit command-line interface.
"""

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
        object_names: list[str] = []
        modules: list[str] = []
        basic = False
        perf = False
        mutable = False
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
                object_names.append(remaining[i])
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
            elif tok == "--mutable":
                mutable = True
                i += 1
            elif tok in ("--arg-type", "--return-type"):
                i += 1
                if i >= len(remaining):
                    print(f"error: {tok} requires a type", file=sys.stderr)
                    sys.exit(1)
                from . import _templates as T
                val = remaining[i]
                if val.endswith("[]"):
                    if tok == "--return-type":
                        print("error: --return-type cannot be an array type.\n"
                              "Use a scalar type or void.", file=sys.stderr)
                        sys.exit(1)
                    elem = val[:-2]
                    if elem not in T._CTYPE_META:
                        print(
                            f"error: --arg-type array element type '{elem}' "
                            "is not supported.\n"
                            f"Supported element types: "
                            f"{', '.join(sorted(T._CTYPE_META))}",
                            file=sys.stderr)
                        sys.exit(1)
                elif val != "void" and val not in T._CTYPE_META:
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

        _new.run(project, dest, object_names or None, state_vars or None,
                 modules=modules, basic=basic, perf=perf, mutable=mutable,
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
        arg_type = "float _Complex"
        return_type = None
        state_vars: list[tuple[str, str, str]] = []
        array_args_obj: list[tuple[str, str]] = []
        init_params_obj: list[tuple[str, str, str]] = []
        no_state = False
        no_step = False
        mutable = False
        impl_spec: str | None = None
        replacements: list[tuple[str, str]] = []

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
            elif tok == "--mutable":
                mutable = True
                i += 1
            elif tok in ("--arg-type", "--return-type"):
                i += 1
                if i >= len(remaining):
                    print(f"error: {tok} requires a type", file=sys.stderr)
                    sys.exit(1)
                from . import _templates as T
                val = remaining[i]
                if val.endswith("[]"):
                    if tok == "--return-type":
                        print("error: --return-type cannot be an array type.\n"
                              "Use a scalar type or void.", file=sys.stderr)
                        sys.exit(1)
                    elem = val[:-2]
                    if elem not in T._CTYPE_META:
                        print(
                            f"error: --arg-type array element type '{elem}' "
                            "is not supported.\n"
                            f"Supported element types: "
                            f"{', '.join(sorted(T._CTYPE_META))}",
                            file=sys.stderr)
                        sys.exit(1)
                elif val != "void" and val not in T._CTYPE_META:
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
            elif tok == "--no-state":
                no_state = True
                i += 1
            elif tok == "--no-step":
                no_step = True
                i += 1
            elif tok == "--init-param":
                ip_var, i = _parse_state_flags(remaining, i)
                init_params_obj.append(ip_var)
            elif tok == "--impl":
                i += 1
                if i >= len(remaining):
                    print("error: --impl requires file::funcname",
                          file=sys.stderr)
                    sys.exit(1)
                impl_spec = remaining[i]
                i += 1
            elif tok == "--replace":
                i += 1
                if i >= len(remaining):
                    print("error: --replace requires old::new",
                          file=sys.stderr)
                    sys.exit(1)
                from . import _impl as _I
                replacements.append(_I.parse_replace(remaining[i]))
                i += 1
            else:
                print(f"error: unexpected argument '{tok}'", file=sys.stderr)
                sys.exit(1)

        if no_state and state_vars:
            print("error: --no-state and --state are mutually exclusive.",
                  file=sys.stderr)
            sys.exit(1)
        if init_params_obj and not no_state:
            print("error: --init-param requires --no-state.",
                  file=sys.stderr)
            sys.exit(1)

        impl_body_obj: str | None = None
        if impl_spec is not None:
            from . import _impl as _I
            impl_body_obj = _I.load_impl(impl_spec, replacements)
        _object.run(Path.cwd(), object_name, module,
                    None if (no_state or not state_vars) else state_vars,
                    perf=perf, arg_type=arg_type, return_type=return_type,
                    array_args=array_args_obj, no_state=no_state,
                    no_step=no_step, mutable=mutable, impl_body=impl_body_obj,
                    init_params=init_params_obj)

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
        batch_method = False
        multi_output: list[str] = []
        method_params: list[tuple[str, str]] = []
        out_type: str | None = None
        out_divisor: int = 1
        impl_spec_m: str | None = None
        replacements_m: list[tuple[str, str]] = []

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
            elif tok == "--batch":
                batch_method = True
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
                if T.is_array_param_type(ptype):
                    elem_ct = T.array_elem_ctype(ptype)
                    if elem_ct not in T.SUPPORTED_ARRAY_CTYPES:
                        print(
                            f"error: --param array element type '{elem_ct}' is"
                            f" not supported.\n"
                            f"Supported element types: "
                            f"{', '.join(sorted(T.SUPPORTED_ARRAY_CTYPES))}",
                            file=sys.stderr,
                        )
                        sys.exit(1)
                elif ptype not in T._CTYPE_META:
                    print(
                        f"error: --param type '{ptype}' is not a supported type.\n"
                        f"Supported scalar: {', '.join(sorted(T._CTYPE_META))}\n"
                        f"Array syntax: name:type[]  e.g. ctrl:\"float _Complex[]\"",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                method_params.append((pname, ptype))
                i += 1
            elif tok == "--out-divisor":
                i += 1
                if i >= len(remaining):
                    print("error: --out-divisor requires an integer", file=sys.stderr)
                    sys.exit(1)
                try:
                    out_divisor = int(remaining[i])
                    if out_divisor < 1:
                        raise ValueError
                except ValueError:
                    print(
                        f"error: --out-divisor must be a positive integer",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                i += 1
            elif tok == "--out-type":
                i += 1
                if i >= len(remaining):
                    print("error: --out-type requires a type", file=sys.stderr)
                    sys.exit(1)
                val = remaining[i]
                if val not in T._CTYPE_TO_NPY:
                    print(
                        f"error: --out-type '{val}' has no numpy equivalent.\n"
                        f"Supported: {', '.join(sorted(T._CTYPE_TO_NPY))}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                out_type = val
                i += 1
            elif tok in ("--arg-type", "--return-type"):
                i += 1
                if i >= len(remaining):
                    print(f"error: {tok} requires a type", file=sys.stderr)
                    sys.exit(1)
                val = remaining[i]
                if val.endswith("[]"):
                    if tok == "--return-type":
                        print("error: --return-type cannot be an array type.\n"
                              "Use a scalar type or void.", file=sys.stderr)
                        sys.exit(1)
                    elem = val[:-2]
                    if elem not in T._CTYPE_META:
                        print(
                            f"error: --arg-type array element type '{elem}' "
                            "is not supported.\n"
                            f"Supported element types: "
                            f"{', '.join(sorted(T._CTYPE_META))}",
                            file=sys.stderr)
                        sys.exit(1)
                elif val != "void" and val not in T._CTYPE_META:
                    print(f"error: {tok} '{val}' is not a supported scalar type.\n"
                          f"Supported: void, {', '.join(sorted(T._CTYPE_META))}",
                          file=sys.stderr)
                    sys.exit(1)
                if tok == "--arg-type":
                    arg_type = val
                else:
                    return_type = val
                i += 1
            elif tok == "--impl":
                i += 1
                if i >= len(remaining):
                    print("error: --impl requires file::funcname",
                          file=sys.stderr)
                    sys.exit(1)
                impl_spec_m = remaining[i]
                i += 1
            elif tok == "--replace":
                i += 1
                if i >= len(remaining):
                    print("error: --replace requires old::new",
                          file=sys.stderr)
                    sys.exit(1)
                from . import _impl as _I
                replacements_m.append(_I.parse_replace(remaining[i]))
                i += 1
            else:
                print(f"error: unexpected argument '{tok}'", file=sys.stderr)
                sys.exit(1)

        if return_type != "void" and return_type not in T._CTYPE_META:
            print(f"error: --return-type '{return_type}' must be void or a scalar type.\n"
                  f"Supported: void, {', '.join(sorted(T._CTYPE_META))}",
                  file=sys.stderr)
            sys.exit(1)

        impl_body_m: str | None = None
        if impl_spec_m is not None:
            from . import _impl as _I
            impl_body_m = _I.load_impl(impl_spec_m, replacements_m)
        _method.run(
            Path.cwd(), object_name, method_name, module,
            arg_type, return_type, variable_output, multi_output,
            params=method_params, out_type=out_type, out_divisor=out_divisor,
            impl_body=impl_body_m, batch=batch_method,
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
        impl_spec_f: str | None = None
        replacements_f: list[tuple[str, str]] = []

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
                if T.is_array_param_type(ptype):
                    elem_ct = T.array_elem_ctype(ptype)
                    if elem_ct not in T.SUPPORTED_ARRAY_CTYPES:
                        print(
                            f"error: --param array element type '{elem_ct}'"
                            f" is not supported.\n"
                            f"Supported element types: "
                            f"{', '.join(sorted(T.SUPPORTED_ARRAY_CTYPES))}",
                            file=sys.stderr,
                        )
                        sys.exit(1)
                elif ptype not in T._CTYPE_META:
                    print(
                        f"error: --param type '{ptype}' is not a supported type.\n"
                        f"Supported scalar: {', '.join(sorted(T._CTYPE_META))}\n"
                        f"Array syntax: name:type[]  e.g. ctrl:\"float _Complex[]\"",
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
            elif tok == "--impl":
                i += 1
                if i >= len(remaining):
                    print("error: --impl requires file::funcname",
                          file=sys.stderr)
                    sys.exit(1)
                impl_spec_f = remaining[i]
                i += 1
            elif tok == "--replace":
                i += 1
                if i >= len(remaining):
                    print("error: --replace requires old::new",
                          file=sys.stderr)
                    sys.exit(1)
                from . import _impl as _I
                replacements_f.append(_I.parse_replace(remaining[i]))
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

        impl_body_f: str | None = None
        if impl_spec_f is not None:
            from . import _impl as _I
            impl_body_f = _I.load_impl(impl_spec_f, replacements_f)
        _function.run(Path.cwd(), fn_name, module, doc,
                      params=fn_params, return_type=fn_return_type,
                      impl_body=impl_body_f)

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

    else:
        print(f"just-makeit: unknown command '{cmd}'", file=sys.stderr)
        print("Run 'just-makeit help' for usage.", file=sys.stderr)
        sys.exit(1)
