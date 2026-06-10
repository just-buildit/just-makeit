"""CLI handler for `just-makeit object`."""

from __future__ import annotations

import sys
from pathlib import Path

from ._cli_parse import parse_init_param_flag, parse_state_flag


# Phase 3a preset shorthand. Each entry maps a preset name to the flag
# combination it expands to, applied before the normal arg parser runs.
# The expansion is purely additive — users can still pass any of these
# flags directly, and `--preset NAME` is interchangeable with typing
# them out.
#
#   processor: default; no-op alias (documents the canonical shape).
#   blockwise: array IO; renderer support for array arg/return types
#              still pending (see gh-86), so the preset will error in
#              the same place as the equivalent hand-typed flags until
#              that lands. Documented in the gallery either way.
#   generator: no input — strips the input side of step().
#   consumer:  no output — strips the output side of step().
#   reader:    no auto-step + file path init-param; user adds custom
#              read/seek/close methods via `jm method`.
_PRESETS: dict[str, list[str]] = {
    "processor": [],
    "generator": ["--arg-type", "void"],
    "consumer": ["--return-type", "void"],
    "reader": [
        "--no-step",
        "--init-param",
        "filepath:const char *",
    ],
    # blockwise: array-in / array-out.  Default element type is float _Complex
    # (complex64 — the most common array-transform type).  Override element
    # types with explicit --arg-type / --return-type flags after --preset.
    "blockwise": [
        "--arg-type",
        "float _Complex[]",
        "--return-type",
        "float _Complex[]",
    ],
}


def _expand_presets(args: list[str]) -> list[str]:
    """Expand `--preset NAME` tokens into their flag combination.

    Walks the arg list once; each `--preset NAME` it encounters is
    replaced by the flags from `_PRESETS[NAME]`. Other tokens pass
    through unchanged. Errors on unknown names with the full allowlist
    listed alphabetically.

    `--preset` is not repeatable. A second occurrence is a hard error
    rather than silent "last one wins" — composing presets is
    intentionally not supported (the underlying flag combinations may
    conflict; users wanting custom combos should pass flags directly)."""
    out: list[str] = []
    saw_preset = False
    i = 0
    while i < len(args):
        tok = args[i]
        if tok != "--preset":
            out.append(tok)
            i += 1
            continue
        if saw_preset:
            print(
                "error: --preset may not be specified more than once. "
                "To customise further, pass the underlying flags directly.",
                file=sys.stderr,
            )
            sys.exit(1)
        i += 1
        if i >= len(args):
            allowed = ", ".join(sorted(_PRESETS))
            print(
                f"error: --preset requires a name. Allowed: {allowed}",
                file=sys.stderr,
            )
            sys.exit(1)
        name = args[i]
        if name not in _PRESETS:
            allowed = ", ".join(sorted(_PRESETS))
            print(
                f"error: --preset '{name}' is not a known preset. Allowed: {allowed}",
                file=sys.stderr,
            )
            sys.exit(1)
        out.extend(_PRESETS[name])
        saw_preset = True
        i += 1
    return out


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
    streamable = False
    async_stream = False
    stream_block_default_obj: int | None = None
    variable_output_obj = False
    max_out_obj: int = 0
    multi_output_obj: list[str] = []
    extra_include_dirs_obj: list[str] = []
    method_name_obj = "run"
    impl_spec: str | None = None
    create_impl_spec: str | None = None
    reset_impl_spec: str | None = None
    destroy_impl_spec: str | None = None
    replacements: list[tuple[str, str]] = []
    class_name_obj: str | None = None

    # Expand `--preset NAME` (Phase 3a) into its flag combination before
    # the normal parser runs, so the rest of this function stays a flat
    # token loop.
    remaining = _expand_presets(args[1:])
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
        elif tok == "--streamable":
            streamable = True
            i += 1
        elif tok == "--async-stream":
            # async iteration is layered on the sync stream; it implies it.
            streamable = True
            async_stream = True
            i += 1
        elif tok == "--stream-block":
            i += 1
            if i >= len(remaining):
                print(
                    "error: --stream-block requires an integer",
                    file=sys.stderr,
                )
                sys.exit(1)
            try:
                stream_block_default_obj = int(remaining[i])
            except ValueError:
                print(
                    f"error: --stream-block '{remaining[i]}' is not an"
                    " integer",
                    file=sys.stderr,
                )
                sys.exit(1)
            if stream_block_default_obj <= 0:
                print(
                    "error: --stream-block must be a positive integer",
                    file=sys.stderr,
                )
                sys.exit(1)
            streamable = True
            i += 1
        elif tok in ("--arg-type", "--return-type"):
            i += 1
            if i >= len(remaining):
                print(f"error: {tok} requires a type", file=sys.stderr)
                sys.exit(1)
            val = remaining[i]
            if val.endswith("[]"):
                elem = val[:-2]
                if elem not in T._CTYPE_META:
                    which = (
                        "--arg-type"
                        if tok == "--arg-type"
                        else "--return-type"
                    )
                    print(
                        f"error: {which} array element type '{elem}' "
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
                print(
                    "error: --array-arg requires name:dtype", file=sys.stderr
                )
                sys.exit(1)
            val = remaining[i]
            if ":" not in val:
                print(
                    f"error: --array-arg '{val}' must be name:dtype",
                    file=sys.stderr,
                )
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
                print(
                    "error: --max-out must be a positive integer",
                    file=sys.stderr,
                )
                sys.exit(1)
            i += 1
        elif tok == "--multi-output":
            i += 1
            if i >= len(remaining):
                print(
                    "error: --multi-output requires a C type", file=sys.stderr
                )
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
                print(
                    "error: --impl requires file::funcname"
                    " or SLOT::file::funcname where SLOT is"
                    " create / reset / destroy",
                    file=sys.stderr,
                )
                sys.exit(1)
            spec = remaining[i]
            # Recognize the SLOT::file::funcname form for lifecycle bodies.
            # Step-body shorthand (file::funcname, 2 parts) keeps working.
            parts = spec.split("::", 2)
            if len(parts) == 3 and parts[0] in ("create", "reset", "destroy"):
                slot, rest = parts[0], "::".join(parts[1:])
                if slot == "create":
                    create_impl_spec = rest
                elif slot == "reset":
                    reset_impl_spec = rest
                else:
                    destroy_impl_spec = rest
            else:
                impl_spec = spec
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
        elif tok == "--extra-include-dirs":
            i += 1
            if i >= len(remaining):
                print(
                    "error: --extra-include-dirs requires a path or ${VAR}",
                    file=sys.stderr,
                )
                sys.exit(1)
            extra_include_dirs_obj.append(remaining[i])
            i += 1
        else:
            print(f"error: unexpected argument '{tok}'", file=sys.stderr)
            sys.exit(1)

    if variable_output_obj:
        no_step = True

    if no_state and state_vars:
        print(
            "error: --no-state and --state are mutually exclusive.",
            file=sys.stderr,
        )
        sys.exit(1)

    impl_body_obj: str | None = None
    create_impl_body_obj: str | None = None
    reset_impl_body_obj: str | None = None
    destroy_impl_body_obj: str | None = None
    if (
        impl_spec is not None
        or create_impl_spec is not None
        or reset_impl_spec is not None
        or destroy_impl_spec is not None
    ):
        from . import _impl as _I

        if impl_spec is not None:
            impl_body_obj = _I.load_impl(impl_spec, replacements)
        if create_impl_spec is not None:
            create_impl_body_obj = _I.load_impl(create_impl_spec, replacements)
        if reset_impl_spec is not None:
            reset_impl_body_obj = _I.load_impl(reset_impl_spec, replacements)
        if destroy_impl_spec is not None:
            destroy_impl_body_obj = _I.load_impl(
                destroy_impl_spec, replacements
            )
    # --streamable needs a block producer: a void-arg source (built-in steps)
    # or a variable_output method (blockwise). With neither, the flag is
    # recorded but stream() stays dormant until a variable_output method is
    # added — warn so the no-op is not silent.
    if streamable and arg_type != "void" and not variable_output_obj:
        print(
            "warning: --streamable has no block producer yet; stream() and"
            " __iter__ will be generated once a variable_output method is"
            " added (use --variable-output, or a void --arg-type for a"
            " source).",
            file=sys.stderr,
        )

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
        streamable=streamable,
        async_stream=async_stream,
        stream_block_default=stream_block_default_obj,
        impl_body=impl_body_obj,
        create_impl_body=create_impl_body_obj,
        reset_impl_body=reset_impl_body_obj,
        destroy_impl_body=destroy_impl_body_obj,
        init_params=init_params_obj,
        variable_output=variable_output_obj,
        multi_output=multi_output_obj,
        method_name=method_name_obj,
        class_name=class_name_obj,
        max_out=max_out_obj,
        extra_include_dirs=extra_include_dirs_obj,
    )
