"""CLI handler for `just-makeit method`."""

from __future__ import annotations

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
    from . import _record
    from . import _types as T

    object_name = args[0]
    method_name = args[1]
    module = None
    arg_type = "void"
    return_type = "float _Complex"
    variable_output = False
    pass_capacity = False
    exact_max_out = False
    count_default = ""
    nogil = False
    varargs = False
    manual_stub = False
    arg_type_explicit = False
    return_type_explicit = False
    single = False
    record_name = ""
    record_module = ""
    record_doc = ""
    record_dtype = ""
    # gh-805 §A2 / §B
    fn = ""
    error_negative = False
    status_return = False
    error = ""
    error_message = ""
    batch_method = False
    doc = ""
    multi_output: list[str] = []
    method_params: list[tuple[str, str]] = []
    out_type: str | None = None
    out_divisor: int = 1
    max_out: int = 0
    no_bench = False
    py_return_type: str = ""
    result_fields: list[dict] = []
    impl_spec_m: str | None = None
    replacements_m: list[tuple[str, str]] = []
    view = ""

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
        elif tok == "--pass-capacity":
            pass_capacity = True
            i += 1
        elif tok == "--exact-max-out":
            exact_max_out = True
            i += 1
        elif tok == "--count-default":
            # gh-657: C expression seeding a void-input variable_output
            # method's `count` kwarg, e.g. --count-default "state->num_taps".
            i += 1
            if i >= len(remaining):
                print(
                    "error: --count-default requires a C expression",
                    file=sys.stderr,
                )
                sys.exit(1)
            count_default = remaining[i]
            i += 1
        elif tok == "--nogil":
            nogil = True
            i += 1
        elif tok == "--fn":
            # gh-805 §A2: the C symbol to bind, when it is not the derived
            # `<comp>_<method>`. Adopting existing C, or binding a validating
            # variant of a hot-path function under the plain Python name.
            i += 1
            if i >= len(remaining):
                print(
                    "error: --fn requires a C function name",
                    file=sys.stderr,
                )
                sys.exit(1)
            fn = remaining[i]
            i += 1
        elif tok == "--error-negative":
            error_negative = True
            i += 1
        # gh-823 Ask D: `status_return` was manifest-only, so `jm script`
        # replayed a project WITHOUT it — the gh-808 divergence class, and it
        # became load-bearing the moment `--error` could accompany it, since
        # the replay would otherwise emit `--error` with nothing to attach to.
        elif tok == "--status-return":
            status_return = True
            i += 1
        elif tok == "--error":
            i += 1
            if i >= len(remaining):
                print(
                    "error: --error requires an exception name",
                    file=sys.stderr,
                )
                sys.exit(1)
            error = remaining[i]
            i += 1
        elif tok == "--error-message":
            i += 1
            if i >= len(remaining):
                print(
                    "error: --error-message requires text",
                    file=sys.stderr,
                )
                sys.exit(1)
            error_message = remaining[i]
            i += 1
        elif tok == "--varargs":
            varargs = True
            i += 1
        elif tok == "--manual-stub":
            # gh-428: the method's C binding already lives, hand-written, in
            # a sacred _ext_<obj>_extra.c fragment -- jm declares nothing for
            # it and only preserves its .pyi placeholder verbatim on regen.
            manual_stub = True
            i += 1
        elif tok == "--single":
            # gh-244: with --result-field, return ONE named record
            # (PyStructSequence) instead of a list[tuple].
            single = True
            i += 1
        elif tok == "--record-name":
            # gh-257: chosen public name for the --single record, overriding
            # the C-return-type derivation.
            i += 1
            if i >= len(remaining):
                print("error: --record-name requires a name", file=sys.stderr)
                sys.exit(1)
            record_name = remaining[i]
            i += 1
        elif tok == "--record-module":
            # gh-261: module qualifier for the --single record's __module__,
            # so its repr matches the project's import path.
            i += 1
            if i >= len(remaining):
                print(
                    "error: --record-module requires a name", file=sys.stderr
                )
                sys.exit(1)
            record_module = remaining[i]
            i += 1
        elif tok == "--record-doc":
            # gh-646: the record type's own documentation — what
            # `help(ToneMetrics)` shows, and the record class's .pyi docstring.
            i += 1
            if i >= len(remaining):
                print("error: --record-doc requires text", file=sys.stderr)
                sys.exit(1)
            record_doc = remaining[i]
            i += 1
        elif tok == "--record-dtype":
            # gh-788: the POD C struct whose layout becomes the returned
            # array's numpy dtype. Pairs with --variable-output and one
            # --result-field per member.
            i += 1
            if i >= len(remaining):
                print(
                    "error: --record-dtype requires a C struct type name",
                    file=sys.stderr,
                )
                sys.exit(1)
            record_dtype = remaining[i]
            i += 1
        elif tok == "--doc":
            i += 1
            if i >= len(remaining):
                print("error: --doc requires a string", file=sys.stderr)
                sys.exit(1)
            doc = remaining[i]
            i += 1
        elif tok == "--view":
            i += 1
            if i >= len(remaining):
                print("error: --view requires a class name", file=sys.stderr)
                sys.exit(1)
            view = remaining[i]
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
        elif tok in ("--param", "--extra-arg"):
            i += 1
            if i >= len(remaining):
                print(f"error: {tok} requires name:type", file=sys.stderr)
                sys.exit(1)
            val = remaining[i]
            if ":" not in val:
                print(
                    f"error: {tok} '{val}' must be name:type",
                    file=sys.stderr,
                )
                sys.exit(1)
            pname, ptype = val.split(":", 1)
            # gh-240: an optional `=default` makes a scalar param omittable.
            pdefault = ""
            if "=" in ptype:
                ptype, pdefault = ptype.split("=", 1)
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
            if pdefault and (
                T.is_array_param_type(ptype)
                or T._CTYPE_META.get(ptype, {}).get("parse_type")
            ):
                print(
                    f"error: --param default (=...) is only supported on plain"
                    f" scalar params; '{pname}:{ptype}' cannot take a default.",
                    file=sys.stderr,
                )
                sys.exit(1)
            if not pdefault and any(
                len(mp) > 2 and mp[2] for mp in method_params
            ):
                print(
                    f"error: required param '{pname}' cannot follow a"
                    f" defaulted param; defaulted params must come last.",
                    file=sys.stderr,
                )
                sys.exit(1)
            method_params.append((pname, ptype, pdefault))
            i += 1
        elif tok == "--out-divisor":
            i += 1
            if i >= len(remaining):
                print(
                    "error: --out-divisor requires an integer", file=sys.stderr
                )
                sys.exit(1)
            try:
                out_divisor = int(remaining[i])
                if out_divisor < 1:
                    raise ValueError
            except ValueError:
                print(
                    "error: --out-divisor must be a positive integer",
                    file=sys.stderr,
                )
                sys.exit(1)
            i += 1
        elif tok == "--max-out":
            i += 1
            if i >= len(remaining):
                print("error: --max-out requires an integer", file=sys.stderr)
                sys.exit(1)
            try:
                max_out = int(remaining[i])
                if max_out < 1:
                    raise ValueError
            except ValueError:
                print(
                    "error: --max-out must be a positive integer",
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
        elif tok == "--result-field":
            i += 1
            if i >= len(remaining):
                print(
                    "error: --result-field requires name:type", file=sys.stderr
                )
                sys.exit(1)
            try:
                result_fields.append(_record.parse_result_field(remaining[i]))
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                sys.exit(1)
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
            elif (
                tok == "--arg-type"
                and val != "void"
                and val not in T._CTYPE_META
            ):
                print(
                    f"error: {tok} '{val}' is not a supported scalar type.\n"
                    f"Supported: void, {', '.join(sorted(T._CTYPE_META))}",
                    file=sys.stderr,
                )
                sys.exit(1)
            # gh-244: --return-type may name a record struct for a result_fields
            # method; validate it post-loop (once --result-field is known).
            if tok == "--arg-type":
                arg_type = val
                arg_type_explicit = True
            else:
                return_type = val
                return_type_explicit = True
            i += 1
        elif tok == "--py-return-type":
            i += 1
            if i >= len(remaining):
                print(
                    "error: --py-return-type requires a Python type string",
                    file=sys.stderr,
                )
                sys.exit(1)
            py_return_type = remaining[i]
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

    # gh-244: a result_fields method's --return-type names the user's record
    # struct (the buffer element type for a list, or the returned record for
    # --single), not a scalar — so it's exempt from the scalar allowlist.
    if not T.is_supported_return_type(return_type) and not result_fields:
        print(
            f"error: --return-type '{return_type}' must be void or a scalar.\n"
            f"{T.unsupported_return_type_help(return_type)}",
            file=sys.stderr,
        )
        sys.exit(1)

    # gh-244: --single returns ONE named record; only meaningful with
    # --result-field (which supplies the field names/types).
    if single and not result_fields:
        print(
            "error: --single requires at least one --result-field.",
            file=sys.stderr,
        )
        sys.exit(1)

    # gh-646: a per-field doc lands on the PyStructSequence field and the
    # record class in the .pyi — both of which only exist for --single. A
    # list-of-records result has no named field surface to carry it, so
    # accepting the text there would render nowhere and say nothing.
    # gh-788: ...or --record-dtype, whose fields are the columns of a
    # structured array. Those DO have a named surface — `y["value"]` — and
    # the whole point of migrating a hand-written module is that its doc
    # surfaces stop being unlinked, so the field docs are accepted and
    # rendered into the method's runtime docstring below. `--record-doc`
    # still needs --single: there is no record TYPE here to attach it to.
    if not single and record_doc:
        print(
            "error: --record-doc requires --single.\n"
            "Without it there is no named record type to document.",
            file=sys.stderr,
        )
        sys.exit(1)
    if (
        not single
        and not record_dtype
        and any(f.get("doc") for f in result_fields)
    ):
        print(
            "error: --result-field docs require --single or --record-dtype.\n"
            "Without either the method returns a list of plain tuples, which "
            "has no named field surface to carry them.",
            file=sys.stderr,
        )
        sys.exit(1)

    # gh-428: manual_stub's C binding is entirely hand-owned, so combining it
    # with any flag that drives jm's own C-side codegen is nonsensical --
    # silently ignoring the extra flag would be a footgun.
    if manual_stub and (
        arg_type_explicit
        or return_type_explicit
        or variable_output
        or method_params
        or multi_output
    ):
        print(
            "error: --manual-stub cannot be combined with --arg-type, "
            "--return-type, --variable-output, --param/--extra-arg, or "
            "--multi-output -- its C binding is hand-written and jm emits "
            "nothing for it.",
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
        py_return_type=py_return_type,
        max_out=max_out,
        result_fields=result_fields or None,
        single=single,
        record_name=record_name,
        record_module=record_module,
        record_doc=record_doc,
        record_dtype=record_dtype,
        varargs=varargs,
        manual_stub=manual_stub,
        pass_capacity=pass_capacity,
        exact_max_out=exact_max_out,
        count_default=count_default,
        nogil=nogil,
        fn=fn,
        error_negative=error_negative,
        status_return=status_return,
        error=error,
        error_message=error_message,
        doc=doc,
        view=view,
    )
