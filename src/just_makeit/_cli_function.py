"""CLI handler for `just-makeit function`."""

from __future__ import annotations

import sys
from pathlib import Path


def _required_after_default_ok(
    fn_params: list[tuple], pname: str, pdefault: str
) -> bool:
    """Enforce the PyArg ``|`` ordering rule: a required param may not follow a
    defaulted one (gh-240). Returns True if OK; prints + returns False if not.

    Shared by the scalar, path, and enum (gh-353) ``--param`` paths so all three
    obey the same "defaulted params come last" constraint.
    """
    if not pdefault and any(len(fp) > 3 and fp[3] for fp in fn_params):
        print(
            f"error: required param '{pname}' cannot follow a"
            f" defaulted param; defaulted params must come last.",
            file=sys.stderr,
        )
        return False
    return True


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
    fn_out_type: str = ""
    fn_variable_output = False
    fn_out_size: str = ""
    fn_result_fields: list[dict] = []
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
                print(
                    f"error: {tok} '{val}' must be name:type", file=sys.stderr
                )
                sys.exit(1)
            pname, ptype = val.split(":", 1)
            # gh-353: two new arg kinds, handled BEFORE the scalar/array type
            # validation below (which would reject them). They mirror the handle
            # generator's path/enum semantics.
            #   --param name:path        -> str | os.PathLike, const char *
            #   --param name:enum:<e>    -> str validated to int via _enum_index
            # Both may take a trailing `=<default>` like a plain scalar; an
            # enum's default is its choice string, a path's a quoted literal.
            if ptype == "path" and not is_out_flag:
                if not _required_after_default_ok(fn_params, pname, ""):
                    sys.exit(1)
                fn_params.append((pname, "path", False, "", ""))
                i += 1
                continue
            if ptype.startswith("enum:") and not is_out_flag:
                ename = ptype[len("enum:") :]
                edefault = ""
                if "=" in ename:
                    ename, edefault = ename.split("=", 1)
                if not ename:
                    print(
                        f"error: {tok} '{val}' must be name:enum:<enum_name>",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                if not _required_after_default_ok(fn_params, pname, edefault):
                    sys.exit(1)
                # type stays "int" (the C function receives a plain int); the
                # enum name rides in the 5th tuple slot. Name validity against
                # the declared [[enum]] tables is checked in _function.run()
                # (the CLI parser has no loaded cfg here).
                fn_params.append((pname, "int", False, edefault, ename))
                i += 1
                continue
            # gh-240: an optional `=default` suffix makes a scalar param
            # omittable (the binding applies the default when the caller leaves
            # it out). Scoped to plain scalars: arrays, out-params, and complex
            # (parse_type) scalars stay required.
            pdefault = ""
            if "=" in ptype:
                ptype, pdefault = ptype.split("=", 1)
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
            if pdefault and (
                T.is_array_param_type(ptype)
                or is_out_flag
                or T._CTYPE_META.get(ptype, {}).get("parse_type")
            ):
                print(
                    f"error: --param default (=...) is only supported on plain"
                    f" scalar params; '{pname}:{ptype}' cannot take a default.",
                    file=sys.stderr,
                )
                sys.exit(1)
            # A defaulted param makes everything after it optional too: a
            # required param may not follow a defaulted one (PyArg `|` rule).
            if not pdefault and any(len(fp) > 3 and fp[3] for fp in fn_params):
                print(
                    f"error: required param '{pname}' cannot follow a"
                    f" defaulted param; defaulted params must come last.",
                    file=sys.stderr,
                )
                sys.exit(1)
            fn_params.append((pname, ptype, is_out_flag, pdefault))
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
        elif tok == "--out-type":
            i += 1
            if i >= len(remaining):
                print("error: --out-type requires a type", file=sys.stderr)
                sys.exit(1)
            val = remaining[i]
            # Same allowlist as the function param scalar slot — must be a
            # type we can return as a numpy array element.
            if val not in T._CTYPE_TO_NPY:
                print(
                    f"error: --out-type '{val}' must be an array-element type.\n"
                    f"Supported: {', '.join(sorted(T._CTYPE_TO_NPY))}",
                    file=sys.stderr,
                )
                sys.exit(1)
            fn_out_type = val
            i += 1
        elif tok == "--variable-output":
            # #318 / gh-335: the function allocates its own 1-D output (sized by
            # --out-size); `out` is appended last to the C call.
            fn_variable_output = True
            i += 1
        elif tok == "--out-size":
            i += 1
            if i >= len(remaining):
                print(
                    "error: --out-size requires a C size expression",
                    file=sys.stderr,
                )
                sys.exit(1)
            fn_out_size = remaining[i]
            i += 1
        elif tok == "--result-field":
            i += 1
            if i >= len(remaining):
                print(
                    "error: --result-field requires name:type", file=sys.stderr
                )
                sys.exit(1)
            val = remaining[i]
            if ":" not in val:
                print(
                    f"error: --result-field '{val}' must be name:type",
                    file=sys.stderr,
                )
                sys.exit(1)
            rf_name, rf_type = val.split(":", 1)
            if rf_type not in T._CTYPE_META:
                print(
                    f"error: --result-field type '{rf_type}' is not a scalar.\n"
                    f"Supported: {', '.join(sorted(T._CTYPE_META))}",
                    file=sys.stderr,
                )
                sys.exit(1)
            fn_result_fields.append({"name": rf_name, "type": rf_type})
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

    # gh-335: --out-size only makes sense for a self-sizing output, and a
    # self-sizing output needs a type to allocate. Reject the half-specified
    # forms rather than silently ignoring a flag.
    if fn_out_size and not fn_variable_output:
        print(
            "error: --out-size requires --variable-output.",
            file=sys.stderr,
        )
        sys.exit(1)
    if fn_variable_output and not fn_out_type:
        print(
            "error: --variable-output requires --out-type (the element type"
            " of the allocated output array).",
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
        out_type=fn_out_type,
        result_fields=fn_result_fields or None,
        variable_output=fn_variable_output,
        out_size=fn_out_size,
    )
