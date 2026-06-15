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

    Returns a 9-tuple and the advanced index:
    ``(name, type, default, default_raw, real_type, real_create_fn, optional,
    create_fn, required)``
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
        default = ""
    else:
        default = parts[2] if len(parts) >= 3 else T._CTYPE_META[ctype]["zero"]
    return (name, ctype, default, "", "", "", False, "", False), i + 1
