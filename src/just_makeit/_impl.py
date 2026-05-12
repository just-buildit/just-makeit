"""
_impl.py — utilities for --impl and --replace.

--impl file::funcname  Extract the body of funcname from a C file and
                       inject it into the generated stub, replacing the
                       /* <<IMPLEMENT>> */ placeholder.

--replace old::new     Apply a string replacement to the extracted body
                       before injection.  Repeatable; applied in order.

Public API
----------
parse_impl(spec)           -> (Path, func_name)
parse_replace(spec)        -> (old, new)
extract_body(path, name)   -> str        (inner lines between { and })
apply_replacements(text, [(old, new)])  -> str
load_impl(spec, replacements)           -> str  (full pipeline)
inject_body_into_stub(stub, body)       -> str  (last fn in stub)
patch_function_body(text, name, body)   -> str  (named fn in C text)
"""

import re
import sys
from pathlib import Path


# ── parsing ───────────────────────────────────────────────────────────────────

def parse_impl(spec: str) -> tuple[Path, str]:
    """Parse 'file::funcname' into (Path(file), funcname)."""
    if "::" not in spec:
        print(
            f"error: --impl must be 'file::funcname', got: {spec!r}\n"
            "Example: --impl ../c/src/resamp_core.c::dp_resamp_execute",
            file=sys.stderr,
        )
        sys.exit(1)
    file_part, func_name = spec.split("::", 1)
    if not func_name:
        print(
            f"error: --impl funcname is empty in: {spec!r}",
            file=sys.stderr,
        )
        sys.exit(1)
    return Path(file_part), func_name


def parse_replace(spec: str) -> tuple[str, str]:
    """Parse 'old::new' into (old, new).  new may be empty."""
    if "::" not in spec:
        print(
            f"error: --replace must be 'old::new', got: {spec!r}\n"
            "Example: --replace dp_resamp_::resamp_",
            file=sys.stderr,
        )
        sys.exit(1)
    old, new = spec.split("::", 1)
    if not old:
        print(
            f"error: --replace 'old' is empty in: {spec!r}",
            file=sys.stderr,
        )
        sys.exit(1)
    return old, new


# ── extraction ────────────────────────────────────────────────────────────────

def extract_body(filepath: Path, func_name: str) -> str:
    """Extract the body of func_name from a C source file.

    Returns the inner content between the outer { and }, with indentation
    normalised to 4 spaces.  Errors and exits if the function is not found
    as a definition (braced body, not a declaration ending in ;).

    The search looks for func_name followed by ( anywhere in the file,
    then scans forward for a { that is not preceded by a ; on the same
    brace-level — distinguishing a definition from a declaration.

    Example::

        # Reference file contains:
        #   size_t dp_execute(dp_state_t *s, ...) {
        #       for (...) { ... }
        #       return n;
        #   }
        body = extract_body(Path("ref.c"), "dp_execute")
        # body == "    for (...) { ... }\\n    return n;"
    """
    try:
        text = filepath.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(
            f"error: --impl file not found: {filepath}",
            file=sys.stderr,
        )
        sys.exit(1)

    func_re = re.compile(r"\b" + re.escape(func_name) + r"\s*\(")

    # Find all candidate matches; pick the first that is a definition.
    for m in func_re.finditer(text):
        pos = m.start()
        # Scan forward up to ~2 KB for { or ;
        window = text[pos: pos + 2048]
        open_idx = window.find("{")
        semi_idx = window.find(";")
        if open_idx == -1:
            continue
        # If ; comes before {, this is a declaration; skip.
        if semi_idx != -1 and semi_idx < open_idx:
            continue

        # Found opening brace at absolute position pos + open_idx
        abs_open = pos + open_idx
        depth = 0
        abs_close = -1
        for i in range(abs_open, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    abs_close = i
                    break

        if abs_close == -1:
            print(
                f"error: unmatched braces extracting "
                f"'{func_name}' from {filepath}",
                file=sys.stderr,
            )
            sys.exit(1)

        inner = text[abs_open + 1: abs_close]
        return _normalise_indent(inner)

    print(
        f"error: function '{func_name}' not found as a definition in "
        f"{filepath}",
        file=sys.stderr,
    )
    sys.exit(1)


def _normalise_indent(inner: str) -> str:
    """Strip one level of leading indentation from extracted body lines."""
    lines = inner.split("\n")
    # Trim leading/trailing blank lines
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return ""
    # Minimum indent of non-empty lines
    non_empty = [l for l in lines if l.strip()]
    if not non_empty:
        return ""
    min_indent = min(len(l) - len(l.lstrip()) for l in non_empty)
    return "\n".join(
        l[min_indent:] if len(l) >= min_indent else l
        for l in lines
    )


# ── transformation ────────────────────────────────────────────────────────────

def apply_replacements(
    text: str,
    replacements: list[tuple[str, str]],
) -> str:
    """Apply each (old, new) pair in order.

    Uses word-boundary matching for simple alphanumeric identifiers
    (letters/digits only, no underscores) so that e.g.
    ``--replace n::w_len`` does not corrupt ``return`` or ``int``.

    Uses literal string replacement for patterns that contain underscores
    or non-word characters, e.g. ``--replace dp_kaiser::kaiser`` (prefix
    substitution), where word-boundary matching would prevent matching
    ``dp_kaiser_window`` because ``dp_kaiser`` is not a complete word.
    """
    for old, new in replacements:
        if re.match(r"^[A-Za-z][A-Za-z0-9]*$", old):
            text = re.sub(r"\b" + re.escape(old) + r"\b", new, text)
        else:
            text = text.replace(old, new)
    return text


def load_impl(
    impl_spec: str,
    replacements: list[tuple[str, str]],
) -> str:
    """Full pipeline: parse spec → extract body → apply replacements."""
    filepath, func_name = parse_impl(impl_spec)
    body = extract_body(filepath, func_name)
    return apply_replacements(body, replacements)


# ── injection ─────────────────────────────────────────────────────────────────

def inject_body_into_stub(stub: str, impl_body: str) -> str:
    """Replace the LAST function body in stub with impl_body.

    The generated stubs always end with the 'main' implementation
    function (for variable-output stubs, that means the processing
    function, not _max_out, since _max_out comes first).

    Also removes the preceding /* <<IMPLEMENT: ... >> */ comment line
    when present, since the stub now has a real implementation.

    impl_body is indented 4 spaces per line; blank lines are preserved
    as empty lines.

    Returns stub unchanged if no function definition is found.
    """
    open_pos = stub.rfind("\n{\n")
    close_pos = stub.rfind("\n}")
    if open_pos == -1 or close_pos <= open_pos:
        return stub

    indented = _indent4(impl_body)

    # Remove the last /* <<IMPLEMENT: ... >> */ comment before this function.
    # The comment is always a single line ending with ' >> */\n'.
    impl_re = re.compile(r"/\* <<IMPLEMENT[^>]*>> \*/\n")
    last_marker: re.Match | None = None
    for m in impl_re.finditer(stub, 0, open_pos + 1):
        last_marker = m

    if last_marker is not None:
        # Keep: everything before comment + signature + new body
        sig = stub[last_marker.end(): open_pos]
        return (
            stub[: last_marker.start()]
            + sig
            + "\n{\n"
            + indented
            + "\n}\n"
        )

    # No comment found — just replace the body.
    return stub[: open_pos + 3] + indented + "\n}\n"


def patch_function_body(
    text: str,
    func_name: str,
    impl_body: str,
) -> str:
    """Replace the body of func_name in arbitrary C text with impl_body.

    Used to patch the step() body in an already-written _core.h.
    Returns text unchanged if func_name is not found as a definition.
    """
    func_re = re.compile(r"\b" + re.escape(func_name) + r"\s*\(")

    for m in func_re.finditer(text):
        pos = m.start()
        window = text[pos: pos + 2048]
        open_idx = window.find("{")
        semi_idx = window.find(";")
        if open_idx == -1:
            continue
        if semi_idx != -1 and semi_idx < open_idx:
            continue

        abs_open = pos + open_idx
        depth = 0
        abs_close = -1
        for i in range(abs_open, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    abs_close = i
                    break

        if abs_close == -1:
            return text

        indented = _indent4(impl_body)
        return (
            text[: abs_open + 1]
            + "\n"
            + indented
            + "\n"
            + text[abs_close:]
        )

    return text


def _indent4(body: str) -> str:
    """Indent each line of body by 4 spaces; blank lines become empty."""
    return "\n".join(
        "    " + line if line.strip() else ""
        for line in body.splitlines()
    )
