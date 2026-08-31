"""Shared CLI argument parsing helpers."""

from __future__ import annotations

import sys


def parse_state_flag(
    remaining: list[str], i: int
) -> tuple[tuple[str, str, str], int]:
    """Parse one --state/--param flag at index i.

    Returns ((name, ctype, default), new_i).
    """
    from . import _types as T

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


def parse_init_param_flag(remaining: list[str], i: int) -> tuple[tuple, int]:
    """Parse one --init-param flag at index i.

    Handles three forms:
    - ``name:type[:default]``   — scalar or required-array param (3-part)
    - ``name:type:optional[:create_fn]`` — optional array kwarg (4-part);
      position 3 must be the literal string ``optional`` (case-insensitive)
      and the type must be an array type (ends with ``[]``).
    - ``name:type:required`` — a scalar param with no default (gh-266);
      position 3 must be the literal string ``required`` (case-insensitive)
      and the type must be a scalar (not an array). It parses as a positional
      before the PyArg ``|``, so omitting it raises ``TypeError`` rather than
      defaulting to the type's zero.
    - ``name:type:capsule:<capsule-name>[:<header>]`` — a foreign C pointer
      arriving as a named ``PyCapsule`` (gh-790), so the object is constructed
      from a handle another module owns. The type is that pointer's own
      spelling and is NOT validated against jm's type table. Always required.

    Returns a 9- or 12-tuple and the advanced index:
    ``(name, type, default, default_raw, real_type, real_create_fn, optional,
    create_fn, required[, doc, capsule, header])``
    """
    from . import _types as T

    i += 1
    if i >= len(remaining):
        print(
            "error: --init-param requires name:type[:default]",
            file=sys.stderr,
        )
        sys.exit(1)
    spec = remaining[i]
    parts = spec.split(":")
    if len(parts) < 2:
        print(
            f"error: --init-param '{spec}' must be in "
            f"name:type[:default] format",
            file=sys.stderr,
        )
        sys.exit(1)
    name = parts[0]
    ctype = parts[1]

    # Optional array syntax: name:type:optional[:create_fn]
    if len(parts) >= 3 and parts[2].lower() == "optional":
        if not T.is_array_param_type(ctype):
            print(
                f"error: --init-param '{spec}': 'optional' is only valid "
                f"for array types (type must end with '[]').",
                file=sys.stderr,
            )
            sys.exit(1)
        create_fn = parts[3] if len(parts) >= 4 else ""
        return (name, ctype, "", "", "", "", True, create_fn, False), i + 1

    # Derived-length syntax: name:type:derived:<c-param-name> (gh-900).
    # The array's length is passed as a NAMED scalar argument placed
    # immediately before the data pointer, instead of the trailing
    # `<name>_len` jm emits by default — `hbdecim_create(size_t num_taps,
    # const float *h)` rather than `(const float *h, size_t h_len)`.
    if len(parts) >= 3 and parts[2].lower() == "derived":
        if len(parts) < 4 or not parts[3]:
            print(
                f"error: --init-param '{spec}': 'derived' needs the name of"
                " the C parameter carrying the length, e.g.\n"
                "  --init-param 'h:float[]:derived:num_taps'\n"
                "That name goes in the create() prototype; the value is this"
                " array's length, so it is never a Python argument.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not T.is_array_param_type(ctype) or ctype.endswith("[][]"):
            print(
                f"error: --init-param '{spec}': 'derived' is only valid for a"
                " 1-D array type (ending with '[]').\n"
                "A 2-D array already passes its shape as two arguments after"
                " the data pointer.",
                file=sys.stderr,
            )
            sys.exit(1)
        return (
            name,
            ctype,
            "",
            "",
            "",
            "",
            False,
            "",
            False,
            "",
            "",
            "",
            parts[3],
        ), i + 1

    # Object syntax: name:object:<comp>[.<Class>][:optional] (gh-1224).
    #
    # The marker sits in the TYPE position, unlike the capsule form's slot 3,
    # because with an `object` reference there IS no type to write: it is
    # derived from the referenced component, along with the capsule name and
    # the header. Making the author restate a type jm is about to derive
    # would re-introduce the duplication the key exists to remove.
    #
    # Nothing is resolved here. `parse_init_param_flag` has no manifest --
    # the referenced component may not even be declared yet at the moment
    # this flag is parsed -- so the reference is carried verbatim in slot 15
    # and `_config._project_init_params` resolves it on the read path, the
    # same way `enum:` references are handled.
    if len(parts) >= 2 and parts[1].lower() == "object":
        ref = parts[2] if len(parts) > 2 else ""
        if not ref:
            print(
                f"error: --init-param '{spec}': 'object' needs the class it"
                " refers to, e.g.\n"
                "  --init-param 'frame:object:frame.FrameDesc'\n"
                "Give it as <component> or <component>.<ClassName>; the C"
                " type, the capsule name and the header are all derived from"
                " the component you name.",
                file=sys.stderr,
            )
            sys.exit(1)
        nullable = any(t.lower() == "optional" for t in parts[3:] if t)
        return (
            (
                name,
                "",  # derived on the read path from the reference below
                "",
                "",
                "",
                "",
                False,
                "",
                not nullable,
                "",
                "",  # capsule: derived
                "",  # header: derived
                "",
                "",
                "",
                ref,
            ),
            i + 1,
        )

    # Capsule syntax: name:type:capsule:<capsule-name>[:<header>] (gh-790).
    # Checked before the type validation below, because the type is the
    # foreign pointer's own spelling (`dp_tlm_t *`) — deliberately not a
    # type jm knows, so `is_valid_type` rejects it and must not be consulted.
    if len(parts) >= 3 and parts[2].lower() == "capsule":
        if len(parts) < 4 or not parts[3]:
            print(
                f"error: --init-param '{spec}': 'capsule' needs the capsule"
                " name it must carry, e.g.\n"
                "  --init-param 'tlm:dp_tlm_t *:capsule:doppler.telemetry.tlm'"
                "\nThat name is what stops a pointer from one module being"
                " accepted by another.",
                file=sys.stderr,
            )
            sys.exit(1)
        if T.is_array_param_type(ctype):
            print(
                f"error: --init-param '{spec}': 'capsule' is not valid for an"
                " array type — a capsule carries one pointer.",
                file=sys.stderr,
            )
            sys.exit(1)
        # gh-805 §H: a trailing `optional` makes the handle NULLABLE — the
        # Python face accepts `None` and C receives NULL. Matched as a literal
        # token rather than by position so it reads the same with or without a
        # header (`…:capsule:cap:optional` and `…:capsule:cap:clk.h:optional`
        # both work); a header file is never named `optional`.
        #
        # It does NOT make the argument omittable. That is the separate
        # optionality axis, and half-doing it would put a `= ...` in the stub
        # for a slot the binding still demands.
        tail = [t for t in parts[4:] if t]
        nullable = any(t.lower() == "optional" for t in tail)
        header = next(
            (t for t in tail if t.lower() != "optional"),
            "",
        )
        # `required` (slot 8) is the switch, and it already meant "reject
        # None" — it simply had no contrasting branch, because both sides
        # rejected it. Default stays required: there is usually no object to
        # build around a handle that is not there, and a NULL that *means*
        # something is the special case the author opts into.
        return (
            (
                name,
                ctype,
                "",
                "",
                "",
                "",
                False,
                "",
                not nullable,
                "",
                parts[3],
                header,
            ),
            i + 1,
        )

    # Required scalar syntax: name:type:required (gh-266)
    if len(parts) >= 3 and parts[2].lower() == "required":
        if T.is_array_param_type(ctype):
            print(
                f"error: --init-param '{spec}': 'required' is only valid "
                f"for scalar types (array init-params are already required "
                f"positionals).",
                file=sys.stderr,
            )
            sys.exit(1)
        if not T.is_valid_type(ctype):
            supported = ", ".join(sorted(T.SUPPORTED_TYPES))
            print(
                f"error: unsupported type '{ctype}'.\nScalar types: {supported}",
                file=sys.stderr,
            )
            sys.exit(1)
        return (name, ctype, "", "", "", "", False, "", True), i + 1

    # Normal scalar / required-array path.
    if not T.is_valid_type(ctype) and not T.is_array_param_type(ctype):
        supported = ", ".join(sorted(T.SUPPORTED_TYPES))
        print(
            f"error: unsupported type '{ctype}'.\n"
            f"Scalar types: {supported}\n"
            f"Array init-param syntax: type[]  e.g. float[]",
            file=sys.stderr,
        )
        sys.exit(1)
    if T.is_array_param_type(ctype):
        # gh-826: this used to be `default = ""` unconditionally — an array
        # init-param's declared default was discarded here, silently, while
        # its scalar sibling in the same command kept its own.
        #
        # `[]` is not a value jm has no use for: it is the one array default
        # the manifest path supports, and it is what makes the parameter
        # omittable (`_state.py` routes it to `def_arr`). Dropping it left the
        # CLI unable to express a shape the manifest can, and left everything
        # downstream self-consistent about a declaration that was no longer
        # there — so a CLI-driven reproduction of that shape came out clean.
        #
        # Deliberately NOT re-validated here. `_state.py` already owns the
        # rule that `[]` is the only supported array default, and states it
        # with the component and parameter named; a second copy of the
        # predicate in the CLI is the pair that drifts.
        default = parts[2] if len(parts) >= 3 else ""
    else:
        default = parts[2] if len(parts) >= 3 else T._CTYPE_META[ctype]["zero"]
    return (name, ctype, default, "", "", "", False, "", False), i + 1
