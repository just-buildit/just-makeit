"""CLI handler for `just-makeit function`."""

import sys
from pathlib import Path


def run(args: list[str]) -> None:
    if len(args) < 1:
        print("error: 'function' requires a function name.", file=sys.stderr)
        sys.exit(1)
    from . import _function
    from . import _types as T

    fn_name = args[0]
    module = None
    doc = ""
    fn_params: list[tuple] = []
    fn_return_type = "void"
    impl_spec_f: str | None = None
    replacements_f: list[tuple[str, str]] = []
    fn_inline = False

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
        elif tok == "--doc":
            i += 1
            if i >= len(remaining):
                print("error: --doc requires a string", file=sys.stderr)
                sys.exit(1)
            doc = remaining[i]
            i += 1
        elif tok in ("--param", "--out-param"):
            is_out_flag = tok == "--out-param"
            i += 1
            if i >= len(remaining):
                print(f"error: {tok} requires name:type", file=sys.stderr)
                sys.exit(1)
            val = remaining[i]
            if ":" not in val:
                print(f"error: {tok} '{val}' must be name:type", file=sys.stderr)
                sys.exit(1)
            pname, ptype = val.split(":", 1)
            if T.is_array_param_type(ptype):
                elem_ct = T.array_elem_ctype(ptype)
                if elem_ct not in T.SUPPORTED_ARRAY_CTYPES:
                    print(
                        f"error: {tok} array element type '{elem_ct}'"
                        f" is not supported.\n"
                        f"Supported element types: "
                        f"{', '.join(sorted(T.SUPPORTED_ARRAY_CTYPES))}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
            elif ptype not in T._CTYPE_META:
                print(
                    f"error: {tok} type '{ptype}' is not a supported type.\n"
                    f"Supported scalar: {', '.join(sorted(T._CTYPE_META))}\n"
                    f"Array syntax: name:type[]"
                    f'  e.g. ctrl:"float _Complex[]"',
                    file=sys.stderr,
                )
                sys.exit(1)
            if is_out_flag and not T.is_array_param_type(ptype):
                print(
                    f"error: --out-param '{pname}' must be an array type"
                    f" (got '{ptype}'); --out-param only applies to writable"
                    f" array params.",
                    file=sys.stderr,
                )
                sys.exit(1)
            fn_params.append((pname, ptype, is_out_flag))
            i += 1
        elif tok == "--return-type":
            i += 1
            if i >= len(remaining):
                print("error: --return-type requires a type", file=sys.stderr)
                sys.exit(1)
            val = remaining[i]
            if val != "void" and val not in T._CTYPE_META:
                print(
                    f"error: --return-type '{val}' must be void or a scalar.\n"
                    f"Supported: void, {', '.join(sorted(T._CTYPE_META))}",
                    file=sys.stderr,
                )
                sys.exit(1)
            fn_return_type = val
            i += 1
        elif tok == "--impl":
            i += 1
            if i >= len(remaining):
                print("error: --impl requires file::funcname", file=sys.stderr)
                sys.exit(1)
            impl_spec_f = remaining[i]
            i += 1
        elif tok == "--replace":
            i += 1
            if i >= len(remaining):
                print("error: --replace requires old::new", file=sys.stderr)
                sys.exit(1)
            from . import _impl as _I

            replacements_f.append(_I.parse_replace(remaining[i]))
            i += 1
        elif tok == "--inline":
            fn_inline = True
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
    _function.run(
        Path.cwd(),
        fn_name,
        module,
        doc,
        params=fn_params,
        return_type=fn_return_type,
        impl_body=impl_body_f,
        inline=fn_inline,
    )
