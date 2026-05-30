"""CLI handler for `just-makeit object`."""

import sys
from pathlib import Path

from ._cli_parse import parse_init_param_flag, parse_state_flag


def run(args: list[str]) -> None:
    if len(args) < 1:
        print("error: 'object' requires an object name.", file=sys.stderr)
        sys.exit(1)
    from . import _object
    from . import _types as T

    object_name = args[0]
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
    variable_output_obj = False
    max_out_obj: int = 0
    multi_output_obj: list[str] = []
    method_name_obj = "run"
    impl_spec: str | None = None
    replacements: list[tuple[str, str]] = []
    class_name_obj: str | None = None

    remaining = args[1:]
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
            var, i = parse_state_flag(remaining, i)
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
            val = remaining[i]
            if val.endswith("[]"):
                if tok == "--return-type":
                    print(
                        "error: --return-type cannot be an array type.\n"
                        "Use a scalar type or void.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                elem = val[:-2]
                if elem not in T._CTYPE_META:
                    print(
                        f"error: --arg-type array element type '{elem}' "
                        "is not supported.\n"
                        f"Supported element types: "
                        f"{', '.join(sorted(T._CTYPE_META))}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
            elif val != "void" and val not in T._CTYPE_META:
                print(
                    f"error: {tok} '{val}' is not a supported scalar type.\n"
                    f"Supported: void, {', '.join(sorted(T._CTYPE_META))}",
                    file=sys.stderr,
                )
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
            val = remaining[i]
            if ":" not in val:
                print(f"error: --array-arg '{val}' must be name:dtype", file=sys.stderr)
                sys.exit(1)
            aa_name, aa_dtype = val.split(":", 1)
            canonical = T.normalize_array_dtype(aa_dtype)
            if canonical is None:
                print(
                    f"error: --array-arg type '{aa_dtype}' not supported.\n"
                    f"Accepted dtype names: {', '.join(sorted(T.SUPPORTED_ARRAY_DTYPES))}\n"
                    f"Accepted C types: {', '.join(sorted(T.SUPPORTED_ARRAY_CTYPES))}",
                    file=sys.stderr,
                )
                sys.exit(1)
            array_args_obj.append((aa_name, canonical))
            i += 1
        elif tok == "--no-state":
            no_state = True
            i += 1
        elif tok == "--no-step":
            no_step = True
            i += 1
        elif tok == "--init-param":
            ip_var, i = parse_init_param_flag(remaining, i)
            init_params_obj.append(ip_var)
        elif tok == "--variable-output":
            variable_output_obj = True
            i += 1
        elif tok == "--max-out":
            i += 1
            if i >= len(remaining):
                print("error: --max-out requires an integer", file=sys.stderr)
                sys.exit(1)
            try:
                max_out_obj = int(remaining[i])
                if max_out_obj < 1:
                    raise ValueError
            except ValueError:
                print("error: --max-out must be a positive integer", file=sys.stderr)
                sys.exit(1)
            i += 1
        elif tok == "--multi-output":
            i += 1
            if i >= len(remaining):
                print("error: --multi-output requires a C type", file=sys.stderr)
                sys.exit(1)
            mo_val = remaining[i]
            if mo_val not in T._CTYPE_META:
                print(
                    f"error: --multi-output type '{mo_val}' not supported.\n"
                    f"Supported: {', '.join(sorted(T._CTYPE_META))}",
                    file=sys.stderr,
                )
                sys.exit(1)
            multi_output_obj.append(mo_val)
            i += 1
        elif tok == "--method-name":
            i += 1
            if i >= len(remaining):
                print("error: --method-name requires a name", file=sys.stderr)
                sys.exit(1)
            method_name_obj = remaining[i]
            i += 1
        elif tok == "--impl":
            i += 1
            if i >= len(remaining):
                print("error: --impl requires file::funcname", file=sys.stderr)
                sys.exit(1)
            impl_spec = remaining[i]
            i += 1
        elif tok == "--replace":
            i += 1
            if i >= len(remaining):
                print("error: --replace requires old::new", file=sys.stderr)
                sys.exit(1)
            from . import _impl as _I

            replacements.append(_I.parse_replace(remaining[i]))
            i += 1
        elif tok == "--class-name":
            i += 1
            if i >= len(remaining):
                print("error: --class-name requires a name", file=sys.stderr)
                sys.exit(1)
            class_name_obj = remaining[i]
            i += 1
        else:
            print(f"error: unexpected argument '{tok}'", file=sys.stderr)
            sys.exit(1)

    if variable_output_obj:
        no_step = True

    if no_state and state_vars:
        print("error: --no-state and --state are mutually exclusive.", file=sys.stderr)
        sys.exit(1)

    impl_body_obj: str | None = None
    if impl_spec is not None:
        from . import _impl as _I

        impl_body_obj = _I.load_impl(impl_spec, replacements)
    _object.run(
        Path.cwd(),
        object_name,
        module,
        None if (no_state or not state_vars) else state_vars,
        perf=perf,
        arg_type=arg_type,
        return_type=return_type,
        array_args=array_args_obj,
        no_state=no_state,
        no_step=no_step,
        mutable=mutable,
        impl_body=impl_body_obj,
        init_params=init_params_obj,
        variable_output=variable_output_obj,
        multi_output=multi_output_obj,
        method_name=method_name_obj,
        class_name=class_name_obj,
        max_out=max_out_obj,
    )
