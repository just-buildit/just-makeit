"""CLI handler for `just-makeit method`."""

import sys
from pathlib import Path


def run(args: list[str]) -> None:
    if len(args) < 2:
        print(
            "error: 'method' requires an object name and a method name.",
            file=sys.stderr,
        )
        sys.exit(1)
    from . import _method
    from . import _types as T

    object_name = args[0]
    method_name = args[1]
    module = None
    arg_type = "void"
    return_type = "float _Complex"
    variable_output = False
    batch_method = False
    multi_output: list[str] = []
    method_params: list[tuple[str, str]] = []
    out_type: str | None = None
    out_divisor: int = 1
    no_bench = False
    impl_spec_m: str | None = None
    replacements_m: list[tuple[str, str]] = []

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
        elif tok == "--no-bench":
            no_bench = True
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
                print(
                    f"error: --multi-output '{val}' is not a supported type.",
                    file=sys.stderr,
                )
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
                    f'Array syntax: name:type[]  e.g. ctrl:"float _Complex[]"',
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
                    "error: --out-divisor must be a positive integer", file=sys.stderr
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
        elif tok == "--impl":
            i += 1
            if i >= len(remaining):
                print("error: --impl requires file::funcname", file=sys.stderr)
                sys.exit(1)
            impl_spec_m = remaining[i]
            i += 1
        elif tok == "--replace":
            i += 1
            if i >= len(remaining):
                print("error: --replace requires old::new", file=sys.stderr)
                sys.exit(1)
            from . import _impl as _I

            replacements_m.append(_I.parse_replace(remaining[i]))
            i += 1
        else:
            print(f"error: unexpected argument '{tok}'", file=sys.stderr)
            sys.exit(1)

    if return_type != "void" and return_type not in T._CTYPE_META:
        print(
            f"error: --return-type '{return_type}' must be void or a scalar.\n"
            f"Supported: void, {', '.join(sorted(T._CTYPE_META))}",
            file=sys.stderr,
        )
        sys.exit(1)

    impl_body_m: str | None = None
    if impl_spec_m is not None:
        from . import _impl as _I

        impl_body_m = _I.load_impl(impl_spec_m, replacements_m)
    _method.run(
        Path.cwd(),
        object_name,
        method_name,
        module,
        arg_type,
        return_type,
        variable_output,
        multi_output,
        params=method_params,
        out_type=out_type,
        out_divisor=out_divisor,
        impl_body=impl_body_m,
        batch=batch_method,
        no_bench=no_bench,
    )
