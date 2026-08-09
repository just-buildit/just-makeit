"""Generate __init__.pyi type stubs for scaffolded C-extension modules.

Each module gets a stub alongside its __init__.py that mirrors every
class, method, property, and module-level function with proper Python
annotations.  The stubs are regenerated in full every time any command
mutates the module (object, method, property, function).

C-type → Python annotation rules
---------------------------------
  float / double               -> float
  *_Complex                    -> complex
  int* / uint* / size_t        -> int
  void                         -> None
  <elem_ctype>[]  (array)      -> NDArray[<numpy_dtype>]

Docstring convention
--------------------
All class docstrings use numpy-style format with ``Parameters`` and
``Examples`` sections.  The ``Examples`` section contains runnable
doctests:  ``python -m doctest -v src/<pkg>/<module>/<module>.pyi``
"""

from __future__ import annotations

import ast
import re as _re
import sys as _sys
import textwrap

from . import _codec as _codec
from . import _coerce
from . import _config as C
from . import _record
from . import _context as Ctx
from . import _types as T
from dataclasses import replace as _replace

from ._context._diagnostics import raises_doc as _raises_doc
from ._gluedoc import glue_methods, max_out_method as _max_out_method
from ._docstring import (
    STUB_TARGET_WIDTH,
    ClassParam,
    class_docstring,
    max_out_arity_key,
    max_out_is_state_only,
)

# ── annotation maps ──────────────────────────────────────────────────────────

_CTYPE_TO_PY: dict[str, str] = {
    "float": "float",
    "double": "float",
    "float _Complex": "complex",
    "double _Complex": "complex",
    "bool": "bool",
    "int": "int",
    "int8_t": "int",
    "int16_t": "int",
    "int32_t": "int",
    "int64_t": "int",
    "uint8_t": "int",
    "uint16_t": "int",
    "uint32_t": "int",
    "uint64_t": "int",
    "size_t": "int",
    "const char *": "str",
}

_CTYPE_TO_NP: dict[str, str] = {
    "bool": "np.bool_",
    "float": "np.float32",
    "double": "np.float64",
    "float _Complex": "np.complex64",
    "double _Complex": "np.complex128",
    "int": "np.int32",
    "int8_t": "np.int8",
    "int16_t": "np.int16",
    "int32_t": "np.int32",
    "int64_t": "np.int64",
    "uint8_t": "np.uint8",
    "uint16_t": "np.uint16",
    "uint32_t": "np.uint32",
    "uint64_t": "np.uint64",
    "size_t": "np.uintp",
}

_DTYPE_TO_CTYPE: dict[str, str] = {
    "float32": "float",
    "float64": "double",
    "complex64": "float _Complex",
    "complex128": "double _Complex",
    "int8": "int8_t",
    "int16": "int16_t",
    "int32": "int32_t",
    "int64": "int64_t",
    "uint8": "uint8_t",
    "uint16": "uint16_t",
    "uint32": "uint32_t",
    "uint64": "uint64_t",
    "uintp": "size_t",
    "intp": "ptrdiff_t",
}


def _py(ctype: str) -> str:
    """Return the Python annotation string for a C type."""
    if ctype == "void":
        return "None"
    if ctype.endswith("[]"):
        # Strip all [] suffixes to handle both 1-D (float[]) and 2-D (float[][]).
        elem = ctype
        while elem.endswith("[]"):
            elem = elem[:-2]
        npt = _CTYPE_TO_NP.get(elem, "Any")
        return f"NDArray[{npt}]"
    if ctype.startswith("string_enum:"):
        choices = ctype[len("string_enum:") :].split(",")
        return "Literal[" + ", ".join(f'"{c}"' for c in choices) + "]"
    if ctype == "path":
        # gh-623: the binding coerces with PyUnicode_FSConverter, so a Path is
        # as valid as a str and the annotation must admit both. (gh-515 spelled
        # this `str` to keep `import os` out of object stubs — but _uses_os
        # emits that import on demand, and a narrow annotation made a working
        # call a type error, which is the more expensive of the two.)
        return _coerce.PATH_PY_TYPE
    if ctype == "bytes":
        # gh-565: an opaque-bytes init-param crosses as a plain `bytes`.
        return "bytes"
    return _CTYPE_TO_PY.get(ctype, "Any")


def _np(ctype: str) -> str:
    """Return the numpy dtype string for a C type (scalar or array) for NDArray hints."""
    elem = ctype
    while elem.endswith("[]"):
        elem = elem[:-2]
    return _CTYPE_TO_NP.get(elem, "Any")


# ── per-object class stub ─────────────────────────────────────────────────────


def _title(name: str) -> str:
    """The stub's class name for *name* — the same derivation the C side uses.

    gh-628: this used ``str.title()``, which lower-cases everything after each
    word's first letter, so a manifest id that already carried capitals came
    out mangled (``HalfbandDecimator`` -> ``Halfbanddecimator``) and the stub
    named a class the extension does not define. One primitive now, in
    ``_config``, beside the ``class_name`` override it falls back from.
    """
    return C.default_class_name(name)


# ── member-level merge / manual_stub splice engine (gh-428) ─────────────────
#
# A `manual_stub = true` method's C binding is entirely hand-owned (spliced
# directly into a sacred `_ext_<obj>_extra.c` fragment jm never created), so
# jm's .pyi codegen only ever knows to emit a generic placeholder for it.
# Separately, a `# jm:hand` comment directly above any class member (method
# or property, manifest-derived or not) marks that member as hand-owned with
# zero manifest declaration required -- the field-data case that motivated
# gh-428's re-scope (doppler's `Fft.execute_ci16`, a hand-added CPython
# overload with no representable manifest entry at all).
#
# Both mechanisms funnel through the same splice: this mirrors
# `_status.py::_pyi_symbols` (gh-426) -- a .pyi is valid Python, so
# `ast.parse` gives exact member text for free -- but extracts source text
# instead of just names, then transplants the old hand-written text back
# over (or, for a `# jm:hand` member with no manifest counterpart, appends
# it after) the freshly rendered class body, the same way `_object.py`'s
# `_extract_c_function_bodies`/`_restore_c_function_bodies` preserve `_ext.c`
# function bodies by name across regen. A property's getter/setter share a
# Python name (`@property def x` / `@x.setter def x`) and are always treated
# as one unit -- splicing only the getter and leaving a stale setter behind
# would be worse than not splicing at all.

_HAND_MARKER = "# jm:hand"
_MANUAL_STUB_PLACEHOLDER = "<<MANUAL_STUB>>"


def _node_span(text: str, node: ast.AST) -> tuple[int, int]:
    """Absolute (start, end) character offsets for *node* within *text*.

    ``ast``'s ``col_offset``/``end_col_offset`` are UTF-8 *byte* offsets
    within their line, not character offsets -- a non-ASCII character
    earlier on the line (e.g. an em dash in a docstring) throws off a naive
    character-index computation, silently swallowing or duplicating text
    after it. Each line's byte column is re-decoded back to a character
    column before combining with the (character-based) line start offset.
    """
    lines = text.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    def _char_offset(lineno: int, byte_col: int) -> int:
        line = lines[lineno - 1]
        char_col = len(line.encode("utf-8")[:byte_col].decode("utf-8"))
        return starts[lineno - 1] + char_col

    return (
        _char_offset(node.lineno, node.col_offset),
        _char_offset(node.end_lineno, node.end_col_offset),
    )


def _member_start_node(node: ast.AST) -> ast.AST:
    """*node* itself, or its first decorator when it has any -- a
    decorator's own ``lineno``/``col_offset`` sit before the ``def`` line's,
    so a property's ``@property``/``@x.setter`` line is only captured by
    starting the span there instead of at the ``def`` keyword."""
    decorators = getattr(node, "decorator_list", None)
    return decorators[0] if decorators else node


def _member_groups(text: str) -> dict[tuple[str, str], list[ast.AST]]:
    """Map ``(ClassName, member_name) -> [FunctionDef, ...]`` for every
    class-body method/property in a `.pyi` source. A property's getter and
    setter share ``member_name`` and land in the same list -- callers must
    treat the group as one atomic unit. Best-effort: unparsable text yields
    an empty map rather than raising."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    groups: dict[tuple[str, str], list[ast.AST]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                groups.setdefault((node.name, item.name), []).append(item)
    return groups


def _group_span(text: str, nodes: list[ast.AST]) -> tuple[int, int]:
    """Combined (start, end) offset spanning every node in *nodes* (a
    property's getter + setter), decorators included."""
    starts = [_node_span(text, _member_start_node(n))[0] for n in nodes]
    ends = [_node_span(text, n)[1] for n in nodes]
    return min(starts), max(ends)


def _group_start_lineno(nodes: list[ast.AST]) -> int:
    return min(_member_start_node(n).lineno for n in nodes)


def _line_start_offset(text: str, lineno: int) -> int:
    """Absolute character offset of the start of 1-indexed *lineno*."""
    lines = text.splitlines(keepends=True)
    return sum(len(line) for line in lines[: lineno - 1])


def _import_bindings(text: str) -> dict[str, ast.stmt]:
    """Map each name a top-level import binds to its import node.

    ``import numpy as np`` -> ``np``; ``import os`` -> ``os``;
    ``import a.b.c`` -> ``a`` (the bound top name); ``from m import X`` -> ``X``;
    ``from m import X as Y`` -> ``Y``. A single node may bind several names.
    Best-effort: unparsable text yields an empty map.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    out: dict[str, ast.stmt] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                out[alias.asname or alias.name.split(".")[0]] = node
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                out[alias.asname or alias.name] = node
    return out


def _referenced_names(block_text: str) -> set[str]:
    """Every bare name referenced inside a transplanted member block.

    The block is class-body-indented (``    def …``), so it is dedented before
    parsing. Collecting ``ast.Name`` ids captures both a direct reference
    (``Sequence``) and the root of an attribute chain (``np`` in ``np.float64``,
    since walking the ``Attribute`` reaches its ``Name`` value). Best-effort.
    """
    try:
        tree = ast.parse(textwrap.dedent(block_text))
    except SyntaxError:
        return set()
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def _imports_for_hand_members(
    old_text: str, new_text: str, hand_blocks: list[str]
) -> list[str]:
    """Verbatim import lines a transplant needs but the fresh render lacks.

    gh-557: ``_splice_manual_stub_bodies`` carries a ``# jm:hand`` member's text
    but not the top-of-file import it references, so a hand stub using a
    non-builtin name (e.g. ``Sequence``) lost ``from collections.abc import
    Sequence`` on the next apply and left an unresolved name. This returns the
    old import lines to reinstate, bounded on both sides: only imports a
    transplanted member actually references, and only ones the fresh render
    does not already emit (so an import jm legitimately dropped is not
    resurrected).
    """
    referenced: set[str] = set()
    for block in hand_blocks:
        referenced |= _referenced_names(block)
    if not referenced:
        return []
    old_imports = _import_bindings(old_text)
    already = set(_import_bindings(new_text))
    nodes: list[ast.stmt] = []
    seen: set[int] = set()
    for name in referenced:
        node = old_imports.get(name)
        if node is None or name in already or id(node) in seen:
            continue
        seen.add(id(node))
        nodes.append(node)
    nodes.sort(key=lambda n: n.lineno)
    return [old_text[slice(*_node_span(old_text, n))] for n in nodes]


def _inject_imports(text: str, imports: list[str]) -> str:
    """Insert *imports* after the last existing top-level import in *text*.

    Falls back to after the first line (the header comment) when the stub has
    no imports at all, which for a generated stub does not occur but keeps the
    helper total.
    """
    if not imports:
        return text
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    last_end = None
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last_end = _node_span(text, node)[1]
    block = "\n".join(imports)
    if last_end is None:
        nl = text.find("\n")
        cut = nl + 1 if nl != -1 else len(text)
        return text[:cut] + block + "\n" + text[cut:]
    return text[:last_end] + "\n" + block + text[last_end:]


def _hand_marker_start(lines: list[str], member_lineno: int) -> int | None:
    """1-indexed line number of the ``# jm:hand`` marker immediately above
    *member_lineno* (skipping at most one blank separator line), or None."""
    idx = member_lineno - 2  # 0-indexed line just above the member
    if idx < 0:
        return None
    if lines[idx].strip() == "":
        idx -= 1
        if idx < 0:
            return None
    return idx + 1 if lines[idx].strip() == _HAND_MARKER else None


#: A ``def`` opening a class member, matched on raw text rather than through
#: `ast`. Only ever used on a stub that has *already* failed to parse, where
#: there is nothing else to read — see :func:`hand_owned_at_risk`.
_DEF_LINE = _re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+(\w+)[ \t]*\(")


def hand_owned_at_risk(cfg: dict, text: str) -> list[str]:
    """Names of hand-owned members recoverable from an **unparseable** stub.

    gh-785. When `ast.parse` fails there is no member map, so
    :func:`_splice_hand_owned` transplants nothing and every hand-written
    member in the file is discarded by the next render. jm can still say
    *what* is being discarded, because both ownership marks survive in plain
    text: ``# jm:hand`` is a comment, and a ``manual_stub`` member's name is
    in the manifest.

    Deliberately a **text** scan. Every structural tool jm has for a ``.pyi``
    starts with `ast.parse`, and the whole point of this function is the case
    where that has already raised — a tolerant re-parse would be a second,
    weaker implementation of the member map that only ever runs on input the
    first one rejected.

    Returns bare member names, not ``Class.member`` pairs: the class a
    ``def`` belongs to is exactly the structure that was lost. A name is
    listed once however many classes declare it, which is the honest
    precision for a file jm cannot read.

    Parameters
    ----------
    cfg : dict
        The manifest, for the ``manual_stub`` declarations.
    text : str
        The old stub source, known not to parse.

    Returns
    -------
    list of str
        Sorted member names. Empty when the broken stub held nothing
        hand-owned — the case where regenerating over it costs nothing and
        should stay quiet.
    """
    lines = text.splitlines()
    manual_names = {name for _cls, name in _manual_stub_pairs(cfg)}
    # Where each member's text starts, so a manual_stub member can be asked
    # whether it still carries the placeholder (nothing to lose) or has been
    # hand-filled (everything to lose).
    starts = [
        (i, m.group(1))
        for i, line in enumerate(lines)
        if (m := _DEF_LINE.match(line))
    ]
    at_risk: set[str] = set()
    for pos, (idx, name) in enumerate(starts):
        end = starts[pos + 1][0] if pos + 1 < len(starts) else len(lines)
        if _hand_marker_start(lines, idx + 1) is not None:
            at_risk.add(name)
        elif (
            name in manual_names
            and _MANUAL_STUB_PLACEHOLDER not in "\n".join(lines[idx:end])
        ):
            at_risk.add(name)
    return sorted(at_risk)


def parse_error(text: str) -> SyntaxError | None:
    """The `SyntaxError` a ``.pyi`` source raises, or None if it parses.

    One place asks this question, so the apply-time warning and the
    ``jm status`` section cannot disagree about whether a given file is
    readable. Blank or whitespace-only text is *not* a failure: a stub that
    has not been written yet is the first-render case, not a broken one.
    """
    if not text.strip():
        return None
    try:
        ast.parse(text)
    except SyntaxError as exc:
        return exc
    return None


def describe_unparseable(
    cfg: dict, text: str, where: str = "the stub"
) -> str | None:
    """The gh-785 report for *text*, or None when there is nothing to say.

    Returns None both when the stub parses and when it does not parse but
    holds nothing hand-owned — in the second case the fresh render is a
    clean repair and announcing it would train the reader to skip the
    message that matters.
    """
    exc = parse_error(text)
    if exc is None:
        return None
    lost = hand_owned_at_risk(cfg, text)
    if not lost:
        return None
    lines = text.splitlines()
    offending = (
        lines[exc.lineno - 1].strip()
        if exc.lineno and exc.lineno <= len(lines)
        else ""
    )
    return (
        f"{len(lost)} hand-written .pyi member(s) will not survive this "
        "render.\n"
        f"  {where}:{exc.lineno}: {exc.msg}\n"
        + (f"    {offending}\n" if offending else "")
        + "  jm finds a stub's members with `ast`, so a stub it cannot parse "
        "has none to\n  find and the fresh render replaces them:\n"
        + "".join(f"    - {name}\n" for name in lost)
        + "  Fix the syntax error first and every one of them is preserved. "
        "Recover them\n  from version control, or paste them back once the "
        "stub parses again."
    )


def _manual_stub_pairs(cfg: dict) -> set[tuple[str, str]]:
    """``{(ClassName, method_name)}`` for every ``manual_stub = true`` entry
    declared anywhere in the manifest (standalone or module object)."""
    pairs: set[tuple[str, str]] = set()
    for comp in C.components(cfg):
        Component = C.class_name(cfg, comp) or _title(comp)
        for m in C.methods(cfg, comp):
            if m.get("manual_stub"):
                pairs.add((Component, m["name"]))
    return pairs


def _placeholder_members(text: str) -> set[tuple[str, str]]:
    """``(ClassName, member_name)`` for every member still carrying the
    unfilled ``<<MANUAL_STUB>>`` placeholder as its body.

    Keyed on the pair, never on the bare name: two classes in one stub can
    both declare ``execute``, and a name-level comparison would let a
    legitimately-still-placeholder member on one class vouch for a member on
    the other that just lost its content. That is the failure-open shape this
    whole check exists to close.
    """
    out: set[tuple[str, str]] = set()
    for key, nodes in _member_groups(text).items():
        start, end = _group_span(text, nodes)
        if _MANUAL_STUB_PLACEHOLDER in text[start:end]:
            out.add(key)
    return out


def placeholder_regressions(old_text: str, new_text: str) -> list[str]:
    """``Class.member`` for each member that carried hand-written content in
    *old_text* and comes back as the bare placeholder in *new_text*.

    gh-765: content loss of exactly this shape was observed once in ~14
    identical runs of `jm apply` on doppler — `execute_ctrl_max_out` and
    `delay_max_out` in `resample.pyi` both replaced by the placeholder, with
    the same code and the same input that produced a clean tree the other
    thirteen times. Six fixed `PYTHONHASHSEED` values did not reproduce it,
    so the trigger is order- or environment-dependent and may never be found.

    This is the durable answer to that, and it is deliberately a *check* on
    the outcome rather than a fix for any particular cause: whatever makes
    the transplant miss — an unsorted traversal, a scratch/real race, a `cfg`
    read while a manifest fragment was mid-write and so missing the
    `manual_stub` entry that marks the member hand-owned — the loss has the
    same signature at the end, and jm can refuse to write it.

    A stub the transplant simply does not reach is exactly the dangerous
    case, since :func:`_splice_manual_stub_bodies` returns the fresh render
    untouched whenever it recognises nothing. So this compares the *final*
    text against the old file rather than instrumenting the splice, and it is
    called on every path out.

    Silent on a first render (no *old_text*) and on a newly declared
    ``manual_stub`` method, both of which introduce a placeholder correctly:
    only a member that demonstrably *had* content is reported.
    """
    if not old_text.strip() or _MANUAL_STUB_PLACEHOLDER not in new_text:
        return []
    old_placeholders = _placeholder_members(old_text)
    old_members = set(_member_groups(old_text))
    lost = [
        f"{cls}.{name}"
        for cls, name in _placeholder_members(new_text)
        if (cls, name) in old_members and (cls, name) not in old_placeholders
    ]
    return sorted(lost)


def _splice_manual_stub_bodies(
    cfg: dict, old_text: str, new_text: str, *, path=None
) -> str:
    """Preserve every hand-owned member of *old_text*, refusing to lose one.

    The transplant itself is :func:`_splice_hand_owned`; this wraps it in the
    gh-765 check. The wrapper exists because the splice has four exits — two
    of them the "I recognised nothing, keep the fresh render" early returns
    that are precisely how content goes missing — and a guard that has to be
    repeated at each one is a guard that will eventually be forgotten at a
    fifth. Checking the outcome once, here, also means all seven callers
    (`_apply`, `_glue`, `_object`, `_method`, `_bind`, `_remove`, and this
    module) are covered by construction rather than by each remembering.

    Raises ``ValueError`` rather than warning: the stub is jm-owned and
    drift-gated, so a caller who restores the lost text by hand has `jm
    status` call it drift and the next `apply` strip it again. There is no
    downstream recovery, which is the same reason gh-426's dropped-symbol
    check is non-suppressible.

    gh-785 is the sibling case and gets the **opposite** handling. When the
    old stub does not parse there is no member map, so the check above has
    nothing to compare and passes: `_placeholder_members(old_text)` and
    `_member_groups(old_text)` are both empty, and every `lost` candidate is
    filtered out by `(cls, name) in old_members`. Refusing there would be
    wrong anyway — a stub that is not valid Python is itself broken, and
    regenerating it is the repair — so this says what the repair costs and
    proceeds. `path` only names the file in that message; the splice does not
    read it.
    """
    report = describe_unparseable(
        cfg, old_text, where=str(path) if path else "the previous stub"
    )
    if report:
        # Its own block rather than a `_report.warn` mark. The two weights
        # answer "will `jm status --check` fail on this?", and after this
        # write the answer is no: the stub jm is about to lay down parses, so
        # the condition is gone and a `!` would be a claim the next status run
        # contradicts. `jm status` carries the gate, where the finding is
        # still live and still recoverable.
        print(f"\nWARNING: {report}", file=_sys.stderr)
    out = _splice_hand_owned(cfg, old_text, new_text)
    lost = placeholder_regressions(old_text, out)
    if lost:
        raise ValueError(
            "refusing to write a stub that would replace hand-written "
            "content with the <<MANUAL_STUB>> placeholder:\n"
            + "".join(f"  {name}\n" for name in lost)
            + "This is gh-765 — an intermittent failure of the manual_stub\n"
            "transplant, not something you did. Nothing has been written.\n"
            "Re-run the command; if it recurs, the .pyi on disk is still\n"
            "intact and worth attaching to gh-765."
        )
    return out


def _splice_hand_owned(cfg: dict, old_text: str, new_text: str) -> str:
    """Preserve every hand-owned member of *old_text* across *new_text*'s
    fresh render.

    A ``(ClassName, member_name)`` group is hand-owned when it is EITHER
    flagged ``manual_stub`` in the manifest OR marked with a ``# jm:hand``
    comment directly above it in *old_text* -- the latter needs no manifest
    entry at all (gh-428's re-scope: a hand-added CPython overload with
    nothing to declare it against).

    A hand-owned group whose name also exists in the freshly rendered
    *new_text* (a manifest-derived member the user then hand-edited in
    place) has its rendered span replaced -- verbatim old text, marker
    comment included so the next regen still recognizes it. A hand-owned
    group with **no** counterpart in *new_text* (no manifest entry
    generates it at all) is instead appended after the last member of its
    class, so a purely hand-written addition survives even though jm never
    emits a placeholder for it to land on. Either way, a first apply (no
    prior text) or a renamed member (no matching old group) leaves the
    fresh render as-is, same limitation the `_ext.c` splicer already
    accepts for renames.
    """
    old_groups = _member_groups(old_text)
    if not old_groups:
        return new_text
    manifest_pairs = _manual_stub_pairs(cfg)
    old_lines = old_text.splitlines()

    def _block(key: tuple[str, str], nodes: list[ast.AST]) -> str:
        """Old-text span to transplant for *key*, marker line included
        when it was `# jm:hand`-marked (so the marker survives the
        transplant and next apply still recognizes it).

        Always anchored at the full start of its first line (never a
        node's mid-line column offset) -- the target replacement below
        anchors the same way, so the block supplies its own indentation
        wholesale instead of stacking on top of what's already there.
        """
        _, end = _group_span(old_text, nodes)
        member_lineno = _group_start_lineno(nodes)
        marker_lineno = _hand_marker_start(old_lines, member_lineno)
        start_lineno = (
            member_lineno if marker_lineno is None else marker_lineno
        )
        start = _line_start_offset(old_text, start_lineno)
        return old_text[start:end]

    hand_owned: dict[tuple[str, str], str] = {}
    for key, nodes in old_groups.items():
        marked = (
            _hand_marker_start(old_lines, _group_start_lineno(nodes))
            is not None
        )
        if key in manifest_pairs or marked:
            hand_owned[key] = _block(key, nodes)
    if not hand_owned:
        return new_text

    new_groups = _member_groups(new_text)
    replacements: list[tuple[int, int, str]] = []
    append_by_class: dict[str, list[str]] = {}
    for key, block in hand_owned.items():
        cls, _name = key
        if key in new_groups:
            new_nodes = new_groups[key]
            _, end = _group_span(new_text, new_nodes)
            start = _line_start_offset(
                new_text, _group_start_lineno(new_nodes)
            )
            replacements.append((start, end, block))
        else:
            append_by_class.setdefault(cls, []).append(block)

    out = new_text
    if replacements:
        # Back-to-front so earlier offsets stay valid across replacements.
        replacements.sort(key=lambda r: r[0], reverse=True)
        for start, end, block in replacements:
            out = out[:start] + block + out[end:]

    if append_by_class:
        try:
            tree = ast.parse(out)
        except SyntaxError:
            return out
        insertions: list[tuple[int, str]] = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name in append_by_class:
                _, end = _node_span(out, node)
                combined = "".join(
                    f"\n\n{block}" for block in append_by_class[node.name]
                )
                insertions.append((end, combined))
        insertions.sort(key=lambda r: r[0], reverse=True)
        for offset, block in insertions:
            out = out[:offset] + block + out[offset:]

    # gh-557: reinstate any top-of-file import a transplanted hand member
    # references but the fresh render dropped (it was hand-added for that
    # member, so jm never emits it).
    out = _inject_imports(
        out,
        _imports_for_hand_members(old_text, out, list(hand_owned.values())),
    )
    return out


_ARRAY_RE = _re.compile(r"^([\w\s_]+)\[(\d+)\]$")


def _py_default_stub(ctype: str, default: str) -> str:
    """Convert a C default literal to a Python literal (stub helper).

    An absent default renders as the ``...`` sentinel rather than as the empty
    string (gh-515). Every consumer already understands ``...`` to mean "no
    literal jm can seed": the construction example is suppressed by the
    ``"..." in py_create_args`` guard, and the emitted signature stays valid
    Python. Returning the raw empty default instead produced ``x: int = `` —
    a SyntaxError that broke the whole stub for any downstream ``mypy`` run or
    ``pytest --doctest-glob='*.pyi'`` sweep.
    """
    if ctype not in _CTYPE_TO_PY or not default.strip():
        return "..."
    if ctype == "bool":
        # gh-610: "bool" isn't in kind_map (it falls to the generic "int"
        # bucket below), so the C/TOML spelling `true`/`false` passed
        # straight through into generated Python — a NameError.
        return "True" if default.strip().lower() == "true" else "False"
    kind_map = {
        "float": "float",
        "double": "float",
        "float _Complex": "complex",
        "double _Complex": "complex",
    }
    kind = kind_map.get(ctype, "int")
    if kind == "float":
        s = default.rstrip("fF")
        if "." not in s and "e" not in s.lower():
            s += ".0"
        return s
    if kind == "complex":
        return "0j"
    return default


def _doctest_out(ctype: str, default: str) -> str | None:
    """Expected repr from a getter call, or None if not safe for doctests."""
    m = _ARRAY_RE.match(ctype.strip())
    if m:
        return None  # array fields: no scalar getter
    if ctype not in _CTYPE_TO_PY:
        return None
    kind_map = {
        "float": "float",
        "double": "float",
        "float _Complex": "complex",
        "double _Complex": "complex",
    }
    kind = kind_map.get(ctype, "int")
    if kind == "int":
        val = _py_default_stub(ctype, default)
        try:
            int(val)
            return val
        except ValueError:
            return None
    if kind == "float":
        s = default.rstrip("fF")
        try:
            v = float(s)
            if v == int(v):
                return repr(v)
        except ValueError:
            pass
        return None
    if kind == "complex":
        return "0j"
    return None


def _ctor_demo_lines(Component: str, py_create_args: str) -> list[str]:
    """The ``>>> obj = Component(...)`` demo, wrapped if it does not fit.

    gh-744. A component with many init-params produced a 257-column example
    line (doppler's ``MpskReceiver``). Unlike an author's ``@code`` block --
    which jm preserves byte-for-byte, trailing comment alignment and all --
    this line is jm's own, so jm owns its width.

    A doctest continues with ``...``, so the wrapped form is still a runnable
    example and still one statement. Shown here with the prompts spelled out
    rather than as a live block, since this illustrates the *output* and is
    not itself an example to run::

        [PS1] obj = Thing(
        [PS2]     alpha=1,
        [PS2]     beta=2,
        [PS2] )

    where ``[PS1]`` is ``>>>`` and ``[PS2]`` is ``...``.

    Parameters
    ----------
    Component : str
        Class name to construct.
    py_create_args : str
        Rendered keyword arguments, comma-separated.

    Returns
    -------
    list of str
        Doctest lines at the class docstring's 4-space indent.
    """
    from ._pyfmt import _split_top_level

    flat = f"    >>> obj = {Component}({py_create_args})"
    if len(flat) <= STUB_TARGET_WIDTH:
        return [flat]
    parts = _split_top_level(py_create_args)
    if not parts:  # a single unsplittable argument -- leave it readable
        return [flat]
    return (
        [f"    >>> obj = {Component}("]
        + [f"    ...     {p}," for p in parts]
        + ["    ... )"]
    )


def _build_class_docstring(
    Component: str,
    state_vars: list,
    no_state: bool,
    init_params: list,
    import_line: str,
    py_create_args: str,
    brief: str = "",
    custom_reset: bool = False,
    create_blk=None,
) -> list[str]:
    """Return lines for a numpy-style class docstring (indented 4 spaces).

    *brief* — when supplied (from the create()'s ``@brief`` in the sacred
    header) — becomes the summary line in place of the generic
    ``"<Component> component."``.

    *create_blk* — the parsed create() ``DocBlock``; its ``@param`` descriptions
    document each init-param.  Per-param precedence is the manifest ``doc=``
    override, then the create ``@param``, then a generic stub.
    """
    summary = brief or f"{Component} component."
    # gh-744: this renderer is not `_docstring._numpy_sections` -- it is the
    # class docstring's own builder, and it wrapped nothing at all, which is
    # where the longest measured overflow came from (a 1222-column `@param`
    # description). gh-747: the wrapping and layout now live once, in
    # `_docstring.class_docstring`, which `_composer` and `_handle` share --
    # what stays here is deriving each parameter's type line and notes, plus
    # the Examples section, which is this producer's alone.
    # `class_runtime_doc` is derived from this function's output rather than
    # rebuilt beside it, so both faces wrap once.

    def _pdesc(name: str, manifest_doc: str, required: bool) -> str:
        stub = (
            f"{name} constructor parameter (required)."
            if required
            else f"{name} constructor parameter."
        )
        hdr = create_blk.param_desc(name) if create_blk else None
        return manifest_doc or hdr or stub

    # Parameters section. init_params win when present (they are what create()
    # actually takes — the #69 contract); state vars are documented only for a
    # plain --state object with no init_params.
    params: list[ClassParam] = []
    if init_params:
        for name, ctype, dflt, *rest in init_params:
            # init_params 10-tuple minus (name, type, default) leaves rest =
            # (default_raw, real_type, real_create_fn, optional, create_fn,
            # required, doc) — optional lives at rest[3], not rest[4] (that
            # was create_fn, always falsy for a plain scalar so the "or None"
            # annotation was only silently wrong for an optional-array param).
            optional = rest[3] if len(rest) >= 4 else False
            required = rest[5] if len(rest) >= 6 else False
            manifest_doc = rest[6] if len(rest) >= 7 else ""
            py_t = _py(ctype)
            if optional:
                py_t = f"{py_t} or None"
            # gh-515/gh-565: a path or bytes blob is required by construction,
            # whatever the flag says.
            if required or ctype == "path" or ctype == "bytes":
                # gh-266: no default — document it as a required parameter.
                params.append(
                    ClassParam(
                        f"{name} : {py_t}",
                        (_pdesc(name, manifest_doc, True),),
                    )
                )
                continue
            if ctype.startswith("string_enum:"):
                py_d = f'"{dflt}"' if dflt else "..."
            else:
                py_d = _py_default_stub(ctype, dflt)
            # gh-515: a param with no default carries no ", default …" clause.
            # numpydoc already reads a bare `name : type` as having no default,
            # whereas the trailing `, default ` jm used to emit was neither
            # readable prose nor a literal anyone could copy into a call.
            params.append(
                ClassParam(
                    f"{name} : {py_t}"
                    + (f", default {py_d}" if dflt.strip() else ""),
                    (_pdesc(name, manifest_doc, False),),
                )
            )
    elif state_vars and not no_state:
        for name, ctype, dflt in state_vars:
            m = _ARRAY_RE.match(ctype.strip())
            if m:
                elem, size = m.group(1).rstrip(), m.group(2)
                npt = _CTYPE_TO_NP.get(elem, "Any")
                params.append(
                    ClassParam(
                        f"{name} : NDArray[{npt}]",
                        (f"Length-{size} array, zero-initialised.",),
                    )
                )
            else:
                py_t = _CTYPE_TO_PY.get(ctype, "Any")
                py_d = _py_default_stub(ctype, dflt)
                params.append(
                    ClassParam(
                        f"{name} : {py_t}, default {py_d}",
                        (f"{name} state variable.",),
                    )
                )

    def _close(trailer: list[str]) -> list[str]:
        """Hand summary + params + *trailer* to the shared layout (gh-747).

        ``blank_before_close`` keeps this producer's long-standing blank line
        after the ``Parameters`` block, which the other two callers do not
        emit — normalising it would rewrite the class docstring of every
        existing project for no benefit.
        """
        return class_docstring(
            summary,
            params=params,
            trailer=trailer,
            blank_before_close=True,
        )

    # Examples section: construction + safe getter calls + reset demo
    scalar_getters: list[tuple[str, str]] = []
    if state_vars and not no_state:
        for name, ctype, dflt in state_vars:
            out = _doctest_out(ctype, dflt)
            if out is not None:
                scalar_getters.append((name, out))

    # Only emit a runnable construction example when every constructor
    # argument has a safe literal. An array/no-default arg renders as `...`
    # (ellipsis), which is not a valid call — emitting it would produce a
    # doctest that raises TypeError. A *required* init-param with no default
    # (gh-273) has no safe seed either, even when its type's zero literal is
    # non-empty (double -> "0.0"), so key off `required`, not the rendered
    # text. In either case skip the Examples block rather than ship a broken
    # example. (This suppression was previously only in the standalone
    # pyi_examples path; folding it here fixes the module path's latent copy
    # of the same gh-273 bug too.)
    # gh-624: a @code block on create() IS the class's example. jm's
    # synthesised "Create with defaults" demo is a fallback for a header that
    # says nothing, not something an author should have to override -- and it
    # can only ever demonstrate construction, never what the type is *for*.
    #
    # Checked before the suppression below on purpose: an object whose ctor jm
    # cannot fabricate a call for (an array arg, or a required init-param with
    # no default -- gh-273) previously got no Examples section at all, which is
    # exactly the object whose author most needs to show a real one.
    # The Examples section is passed to `class_docstring` as a ready-indented
    # trailer rather than built by it: these lines are doctests, and wrapping
    # or re-indenting them would break the very thing they assert.
    _authored = list(getattr(create_blk, "examples", None) or [])
    if _authored:
        # gh-691: the trailing blank line is load-bearing, not cosmetic. Under
        # a text-mode `.pyi` doctest run (pytest --doctest-glob='*.pyi', which
        # griffe-style consumers use) a doctest's expected output runs to the
        # next blank line -- so without one the last example swallows the
        # closing quotes AND the following declaration, and can never match.
        # The synthesised demo below has always emitted it; the authored path
        # added in gh-624 did not.
        return _close(
            ["    Examples", "    --------"]
            + [f"    {ln}".rstrip() for ln in _authored]
            + [""]
        )

    if "..." in py_create_args or Ctx._unseedable_required(init_params):
        return _close([])

    ex: list[str] = [
        "    Examples",
        "    --------",
        "    Create with defaults:",
        "",
        f"    >>> {import_line}",
        *_ctor_demo_lines(Component, py_create_args),
    ]
    for name, out in scalar_getters[:3]:
        ex += [f"    >>> obj.get_{name}()", f"    {out}"]

    # The "reset restores defaults" demo assumes reset() zeroes the first state
    # var. A custom reset_impl (#51) may deliberately preserve config (e.g. a
    # waveform `type` set by create_impl), so skip the demo there.
    if scalar_getters and not custom_reset:
        first_name, first_out = scalar_getters[0]
        first_ct = next(ct for n, ct, _ in state_vars if n == first_name)
        kind_map = {
            "float": "float",
            "double": "float",
            "float _Complex": "complex",
            "double _Complex": "complex",
        }
        kind = kind_map.get(first_ct, "int")
        set_val = (
            "0"
            if (kind == "int" and first_out != "0")
            else "42"
            if kind == "int"
            else "0.0"
            if first_out != "0.0"
            else "1.0"
        )
        ex += [
            "",
            "    Reset restores defaults:",
            "",
            f"    >>> obj.set_{first_name}({set_val})",
            "    >>> obj.reset()",
            f"    >>> obj.get_{first_name}()",
            f"    {first_out}",
        ]

    ex.append("")
    return _close(ex)


def class_docstring_block(
    obj: str,
    Component: str,
    state_vars: list,
    no_state: bool,
    init_params: list,
    import_line: str,
    py_create_args: str,
    *,
    doc_blocks: dict | None = None,
    manifest_doc: str = "",
    custom_reset: bool = False,
    create_fn: str | None = None,
) -> str:
    """Assemble a component's class docstring block (the whole ``\"\"\"...\"\"\"``,
    4-space indented, ready to drop under ``class X:``).

    The single entry point for BOTH ``.pyi`` generators — the standalone
    ``COMPONENT_PYI`` template's ``<<class_docstring>>`` slot and the module
    aggregator — so the class summary and ``Parameters`` never drift between
    them (the recurring two-generator bug; see gh-446). Summary and params
    derive from the sacred ``<obj>_create`` Doxygen (``@brief`` -> summary,
    ``@param`` -> each ``Parameters`` entry) — or, when the object declares a
    ``create_fn`` override (gh-602), from *that* function's Doxygen instead,
    since it is the constructor ``tp_init`` actually calls. jm's own scaffold
    boilerplate is already filtered out of *doc_blocks* upstream
    (``_is_scaffold_brief``), so an un-enriched header falls back to the
    generic ``"<Component> component."`` and produces byte-identical output
    to the pre-unification template.
    """
    create_blk = (doc_blocks or {}).get(create_fn or f"{obj}_create")
    brief = manifest_doc or (
        create_blk.brief if (create_blk and create_blk.brief) else ""
    )
    return "\n".join(
        _build_class_docstring(
            Component,
            state_vars,
            no_state,
            list(init_params),
            import_line,
            py_create_args,
            brief=brief,
            custom_reset=custom_reset,
            create_blk=create_blk,
        )
    )


def class_runtime_doc(
    obj: str,
    Component: str,
    state_vars: list,
    no_state: bool,
    init_params: list,
    import_line: str,
    py_create_args: str,
    *,
    doc_blocks: dict | None = None,
    manifest_doc: str = "",
    custom_reset: bool = False,
    create_fn: str | None = None,
) -> list[str]:
    """The class docstring as runtime ``tp_doc`` lines (gh-642).

    Takes exactly the arguments :func:`class_docstring_block` takes and
    returns its text with the stub-only parts removed: the 4-space class
    indent and the ``\"\"\"`` delimiters. It is *derived from* that function
    rather than rebuilt beside it, which is the whole point — a class summary
    and ``Parameters`` block that agree between ``help(Obj)`` and ``Obj.pyi``
    by construction cannot drift the way the two ``.pyi`` generators once did
    (gh-446).

    The dedent is safe because the block's shape is fixed by its producer:
    line 0 is ``    \"\"\"<summary>``, the last line is ``    \"\"\"``, and every
    other non-blank line carries at least the class indent.

    Returns
    -------
    list of str
        Docstring lines with no indent and no delimiters, trailing blanks
        trimmed — ready for ``_build_ml_doc``.
    """
    lines = class_docstring_block(
        obj,
        Component,
        state_vars,
        no_state,
        init_params,
        import_line,
        py_create_args,
        doc_blocks=doc_blocks,
        manifest_doc=manifest_doc,
        custom_reset=custom_reset,
        create_fn=create_fn,
    ).split("\n")
    out = [lines[0].lstrip()[3:]] + [
        ln[4:] if ln.startswith("    ") else ln for ln in lines[1:-1]
    ]
    while out and not out[-1].strip():
        out.pop()
    return out


def _method_doc_lines(
    block,
    m_name: str,
    py_params: list[tuple[str, str]],
    ret_ann: str,
    override: str = "",
    raises: "list[tuple[str, str]] | None" = None,
    skeleton_fallback: bool = False,
) -> list[str]:
    """Return indented `.pyi` docstring lines for an object method.

    *skeleton_fallback* selects what an UNDOCUMENTED member falls back to, and
    it is a caller's choice because the callers genuinely want different
    things. A module OBJECT's method passes True, to match what the standalone
    face emits for the same member -- without it an undocumented `close` read
    `Close.` here and `close.` there, the same member capitalised differently
    for living in a module (gh-867). A VIEW's method leaves it False: gh-685
    pins the capitalised name stub as a deliberate guarantee, and flipping the
    shared helper broke that test rather than the module face.

    Which spelling is better is a separate and real question -- numpydoc wants
    a capitalised summary, so both object faces are arguably wrong together
    now. Deliberately not smuggled into a parity fix.

    *raises* is `_context._diagnostics.raises_doc` for the method — the same
    list the standalone stub and the runtime ``PyMethodDef`` pass, because
    this is the second ``.pyi`` producer and wiring a value into one and not
    its sibling is the habit `tests/test_face_parity_gate.py` exists to catch
    (gh-869).
    """
    from ._docstring import render_numpy_doc

    return render_numpy_doc(
        block,
        m_name,
        py_params,
        ret_ann,
        override,
        indent=8,
        raises=raises,
        skeleton_fallback=skeleton_fallback,
    )


def _obj_stream_pyi(cfg: dict, obj: str) -> str:
    """Return the ``stream()`` / ``__iter__`` ``.pyi`` block for *obj*.

    Empty string when the object is not ``--streamable`` or has no resolvable
    block producer.  Reuses ``make_stream_ctx`` so the module ``.pyi`` matches
    the standalone ``component.pyi`` stub exactly (gh-203).
    """
    from ._context import make_stream_ctx

    Component = C.class_name(cfg, obj) or _title(obj)
    return make_stream_ctx(
        obj,
        Component,
        Component,
        streamable=C.is_streamable(cfg, obj),
        async_stream=C.is_async_stream(cfg, obj),
        methods=C.methods(cfg, obj),
        arg_type=C.arg_type(cfg, obj),
        return_type=C.return_type(cfg, obj),
        default_block=C.stream_block_default(cfg, obj),
    )["pyi_stream_methods"]


def _view_doc_blocks(cfg: dict, obj: str, synth: str) -> dict:
    """The parent's header blocks, re-keyed under a view's synthetic name.

    gh-685. A view shares its parent's ``_core.c`` and calls the same C
    functions, so ``ddc_execute_ctrl``'s Doxygen documents
    ``MatchedDDC.execute_ctrl`` exactly as it documents ``DDC.execute_ctrl``.
    The stub builder keys its lookups on the component it is rendering, which
    for a view is a synthetic id, so without this every inherited member missed
    and fell back to its name-based stub.

    Only the ``<obj>_`` prefix is rewritten; anything else (a module-level
    name, a view's own ``create_fn``) is left alone for the caller to merge.
    """
    blocks = cfg.get(obj, {}).get("_doc_blocks", {}) or {}
    pre = f"{obj}_"
    out = {
        f"{synth}_{k[len(pre) :]}": v
        for k, v in blocks.items()
        if k.startswith(pre)
    }
    # gh-761: the `_max_out` arity set rides this map under a reserved key, so
    # the prefix filter above drops it — and its entries are full C names
    # (`ddcr_execute_max_out`) that the view looks up under its synthetic id.
    # Re-key both. Without this a view of a state-only parent renders the
    # count-bearing stub while its parent renders the correct one, which is
    # the same stub/binding disagreement one level down.
    state_only = blocks.get(max_out_arity_key())
    if state_only:
        out[max_out_arity_key()] = frozenset(
            f"{synth}_{n[len(pre) :]}" if n.startswith(pre) else n
            for n in state_only
        )
    return out


def _obj_stub(cfg: dict, obj: str, pkg: str = "", module: str = "") -> str:
    Component = C.class_name(cfg, obj) or _title(obj)
    state_vars = C.state_vars(cfg, obj)
    arg_type = C.arg_type(cfg, obj)
    return_type = C.return_type(cfg, obj)
    obj_methods = C.methods(cfg, obj)
    obj_props = C.properties(cfg, obj)
    # Doxygen blocks parsed from the sacred header, stashed on cfg by
    # _object._regenerate_module. Maps C function name -> DoxyBlock.
    doc_blocks = cfg.get(obj, {}).get("_doc_blocks", {}) or {}
    state_names = {n for n, _, _ in state_vars}
    ip = C.init_params(cfg, obj)
    no_step = C.is_no_step(cfg, obj)
    no_reset = C.is_no_reset(cfg, obj)
    no_state = C.is_no_state(cfg, obj)
    # Controllable per-call overrides (gh-240): step() shows them positional-
    # only (trailing `/`, since its binding rejects keyword calls); steps()
    # shows them keyword-capable. Empty unless a field is controllable.
    _ctrl = C.controllable_state_vars(cfg, obj)
    _ctrl_kw = "".join(f", {n}: {_py(ct)} = ..." for n, ct in _ctrl)
    _ctrl_posonly = ", /" if _ctrl else ""

    def _builtin_doc(
        cfn,
        py_params,
        ret_ann,
        fallback_doc,
        param_fallback="Input sample.",
        return_fallback="Output sample.",
    ):
        """Docstring lines for a built-in method: the header Doxygen for *cfn*
        when present (so reset/step/steps are documentable), else the canned
        summary *fallback_doc* over the full section skeleton.

        gh-867: the skeleton is the point. This used to return a bare
        ``\"\"\"<fallback_doc>\"\"\"``, so the same object documented LESS for
        living in a module -- `step` lost its `Parameters` and `Returns`
        entirely, and `steps` lost the second half of its summary. Since a
        module object is the common shape (nearly every doppler object is
        one), the abbreviated stub was what most users actually got.

        gh-877: the skeleton is no longer per built-in. It used to be, to
        mirror a standalone face that rendered sections for `step` and a bare
        summary for `steps` and `reset` -- correct for a parity fix, and an
        unsatisfying end state, since `steps` is where the types are least
        guessable and it was the one going without. Both faces now render the
        skeleton for every built-in.

        There is nothing to special-case for the shapes that have less to say:
        the renderer omits a section it cannot fill, so a member with no
        parameters gets no `Parameters` and a `None` return gets no `Returns`.
        `reset` has neither and so still renders its summary alone -- not an
        exception to the rule, but the rule applied to a member with nothing
        else to state.
        """
        blk = doc_blocks.get(cfn)
        if blk is not None:
            return _method_doc_lines(blk, cfn, py_params, ret_ann)
        from ._docstring import render_numpy_doc

        return render_numpy_doc(
            None,
            cfn,
            py_params,
            ret_ann,
            fallback_doc,
            indent=8,
            skeleton_fallback=True,
            param_fallback=param_fallback,
            return_fallback=return_fallback,
        )

    # Constructor arg string for doctest. init_params drive create() when
    # present (the #69 contract — even when scalar state vars also exist, which
    # are then hidden/bridged), so the example must use them; a string_enum
    # default renders as its quoted string, not the enum index.
    scalar_vars = [
        (n, ct, dflt)
        for n, ct, dflt in state_vars
        if not _ARRAY_RE.match(ct.strip())
    ]

    def _ctor_literal(ct: str, dflt: str, required: bool = False) -> str:
        if ct.startswith("string_enum:"):
            return f'"{dflt}"' if dflt else "..."
        # gh-273: a required scalar with no default has no value jm can seed —
        # a validating constructor would reject the type's zero. Render `...`
        # (for any type, including floats whose zero literal is `.0`) so
        # _build_class_docstring suppresses the construction doctest rather than
        # emitting one that raises under `pytest --doctest-glob='*.pyi'`.
        if required and not dflt:
            return "..."
        lit = _py_default_stub(ct, dflt)
        # A non-required no-default scalar keeps the historic zero seed.
        return lit if lit != "" else _py_default_stub(ct, "0")

    def _ctor_arg(p) -> str:
        n, ct, dflt = p[0], p[1], p[2]
        return f"{n}={_ctor_literal(ct, dflt, required=len(p) > 8 and bool(p[8]))}"

    py_create_args = (
        # keyword args: order-independent against the binding's parse order, and
        # self-documenting (string_enums show their chosen string). gh-610:
        # the state-vars-only shape gets the same treatment for the same
        # reason — a positional example rots silently on any reorder.
        ", ".join(_ctor_arg(p) for p in ip)
        if ip
        else (
            ", ".join(
                f"{n}={_py_default_stub(ct, dflt)}"
                for n, ct, dflt in scalar_vars
            )
            if (scalar_vars and not no_state)
            else ""
        )
    )

    import_line = (
        f"from {pkg}.{module} import {Component}"
        if pkg and module
        else f"from {pkg} import {Component}"
        if pkg
        else f"from ... import {Component}"
    )

    # Class docstring — same shared builder the standalone COMPONENT_PYI path
    # uses (class_docstring_block), so the two .pyi generators never drift.
    # init_params imply a create_impl that derives state from the params (the
    # #69 contract), so the first state var is config — not guaranteed zeroed by
    # reset(); skip the "reset restores defaults" demo there. gh-542: `no_reset`
    # removes the method, so the demo would be a failing doctest under
    # `pytest --doctest-glob='*.pyi'`, not just stale prose.
    doc_lines = class_docstring_block(
        obj,
        Component,
        state_vars,
        no_state,
        list(ip),
        import_line,
        py_create_args,
        doc_blocks=doc_blocks,
        manifest_doc=cfg.get(obj, {}).get("doc", ""),
        custom_reset=bool(ip) or no_reset,
        create_fn=C.object_create_fn(cfg, obj),
    ).split("\n")
    # A generated object type is `Py_TPFLAGS_DEFAULT` — not `BASETYPE` — so it
    # cannot be subclassed at runtime; the stub says so with @final. (Composer
    # types, which do set BASETYPE, are stubbed on a separate path.)
    lines: list[str] = ["@final", f"class {Component}:"] + doc_lines

    # __init__
    # gh-530: init_params take precedence over state vars, because the runtime
    # constructor is init_params-based whenever both are declared (the gh-69
    # contract: init_params drive create(); scalar state stays internal, set
    # from defaults and reachable only via getters/setters). This ordering
    # mirrors the class docstring's Parameters block (_build_class_docstring,
    # which checks `if init_params:` first) -- the two had drifted, so an
    # object with both `[[state]]` and `[[init_params]]` documented the
    # init_params while the signature listed the state fields, and the two
    # halves of the same stub disagreed. The standalone `component.pyi` never
    # had this bug: `make_state_ctx` already overrides its signature slot with
    # the init_params one. This is the module-aggregated peer.
    if ip:
        # gh-266: a required scalar has no default, so it is emitted without a
        # `= ...` placeholder and hoisted ahead of every defaulted parameter —
        # a default-less stub arg after a defaulted one is a syntax error, and
        # this mirrors the constructor's positional-before-`|` ordering.
        req_parts: list[str] = []
        parts_init: list[str] = []
        for param in ip:
            n, t = param[0], param[1]
            dflt = param[2] if len(param) > 2 else ""
            optional = param[6] if len(param) > 6 else False
            required = param[8] if len(param) > 8 else False
            # gh-611 (module peer of _context/_state.py's arr_ip): a 1-D/2-D
            # array with NO declared default is a required positional in the
            # C ABI — the generated kwlist hoists it ahead of every defaulted
            # scalar (`_context/_state.py`'s `required_entries`) regardless of
            # the `required` flag, which is only ever consulted for scalars.
            # A defaulted array (`default = "[]"`, gh-611's def_arr_ip) is
            # genuinely optional and keeps its declared position below — only
            # the default-less array is hoisted here.
            is_required_array = t.endswith("[]") and not optional and not dflt
            # gh-845: a capsule init-param is required-POSITIONAL whatever its
            # nullability — `_context/_state.py`'s `required_entries` adds
            # every capsule unconditionally, so the binding puts it before the
            # `|` either way. Tested directly rather than inferred from
            # `required`, which caught it only by accident while every capsule
            # was also mandatory. gh-805 §H made a nullable one `required =
            # false` and this fell through to the defaulted branch: the stub
            # then advertised `clock: Any = ...` for a positional the binding
            # demands, AND left it in declaration order behind a defaulted
            # scalar the kwlist hoists it above — so `Capn(4096)` bound 4096 to
            # `clock` while the stub promised `n`. Same accidental coupling as
            # `allow_none`/`explain_type_error` in `_context/_parse.py`.
            capsule = param[10] if len(param) > 10 else ""
            # gh-515: a path is required-positional by construction (a
            # filesystem path has no sensible default), so it is hoisted with
            # the other default-less params rather than given a `= ...`.
            # gh-565: a bytes blob is required-positional for the same reason.
            if capsule:
                # gh-790/gh-845: `object`, not `Any` — the binding accepts the
                # capsule or anything exposing `._capsule`, and `Any` would
                # type-check the int a reader might try. `| None` when
                # nullable, and never a `= None`: accepting None and being
                # omittable are different axes. Matches the standalone
                # producer in `_context/_state.py`, which gh-805 §H fixed and
                # this one was missed by — jm has five `.pyi` producers.
                req_parts.append(
                    f"{n}: object | None" if not required else f"{n}: object"
                )
            elif (
                t == "path"
                or t == "bytes"
                or is_required_array
                or (required and not t.endswith("[]"))
            ):
                req_parts.append(f"{n}: {_py(t)}")
            elif optional:
                parts_init.append(f"{n}: {_py(t)} | None = None")
            elif t.startswith("string_enum:"):
                parts_init.append(
                    f'{n}: {_py(t)} = "{dflt}"'
                    if dflt
                    else f"{n}: {_py(t)} = ..."
                )
            else:
                parts_init.append(f"{n}: {_py(t)} = ...")
        init_params_str = ", ".join(req_parts + parts_init)
        lines.append(f"    def __init__(self, {init_params_str}) -> None: ...")
    elif state_vars and not no_state:
        # State-only object (no init_params): the scalar state vars ARE the
        # constructor, each with a default.
        # The real default, not `...`. This producer already has
        # `_py_default_stub` and uses it elsewhere in this file; the
        # constructor was the one place it discarded the value and emitted the
        # sentinel, so the same object advertised `gain: float = 0.0`
        # standalone and `gain: float = ...` in a module. It falls back to
        # `...` on its own when there is no literal to seed (gh-515), so the
        # sentinel still appears exactly where it should.
        init_params_str = ", ".join(
            f"{n}: {_py(t)} = {_py_default_stub(t, d)}"
            for n, t, d in scalar_vars
        )
        lines.append(f"    def __init__(self, {init_params_str}) -> None: ...")
    else:
        lines.append("    def __init__(self, /, *args, **kwargs) -> None: ...")

    # gh-131: skip the built-in reset() stub when the user declared a
    # [[methods]] entry named "reset"; that entry's stub appears below in
    # the extra-methods loop and must not be duplicated here.
    # gh-542: `no_reset` removes it outright — the stub must not advertise a
    # method the extension does not define, or a type checker green-lights a
    # call that raises AttributeError at runtime.
    _user_has_reset = any(m["name"] == "reset" for m in obj_methods)
    if not _user_has_reset and not no_reset:
        lines += ["", "    def reset(self) -> None:"]
        lines += _builtin_doc(
            f"{obj}_reset", [], "None", "Reset state to post-create defaults."
        )

    # step() / steps()
    if no_step:
        pass
    elif arg_type.endswith("[]") and return_type.endswith("[]"):
        # Blockwise (array-in / array-out): there is no step(); the object
        # exposes steps(x[, out]). A controllable state field adds an optional,
        # keyword-capable per-call override that defaults to the live field
        # (gh-240) — rendered as `name: <pytype> = ...` after `out`.
        ctrl = C.controllable_state_vars(cfg, obj)
        params = [
            "        self,",
            f"        x: NDArray[{_np(arg_type)}],",
            f"        out: NDArray[{_np(return_type)}] | None = None,",
        ]
        params += [f"        {n}: {_py(ct)} = ..." for n, ct in ctrl]
        lines += [
            "",
            "    def steps(",
            *params,
            f"    ) -> NDArray[{_np(return_type)}]:",
        ]
        lines += _builtin_doc(
            f"{obj}_steps",
            [("x", f"NDArray[{_np(arg_type)}]")],
            f"NDArray[{_np(return_type)}]",
            "Apply the blockwise transform to the input array.",
        )
    elif arg_type.endswith("[]"):
        lines += [
            "",
            f"    def step(self, x: {_py(arg_type)}{_ctrl_kw}"
            f"{_ctrl_posonly}) -> {_py(return_type)}:",
        ]
        lines += _builtin_doc(
            f"{obj}_step",
            [("x", _py(arg_type))],
            _py(return_type),
            "Process one buffer of samples.",
        )
    elif arg_type != "void":
        lines += [
            "",
            f"    def step(self, x: {_py(arg_type)}{_ctrl_kw}"
            f"{_ctrl_posonly}) -> {_py(return_type)}:",
        ]
        lines += _builtin_doc(
            f"{obj}_step",
            [("x", _py(arg_type))],
            _py(return_type),
            (
                "Process one input sample."
                if return_type != "void"
                else "Consume one input sample (no output)."
            ),
        )
        if return_type != "void":
            lines += [
                "",
                f"    def steps(self, x: NDArray[{_np(arg_type)}],"
                f" out: NDArray[{_np(return_type)}] | None = None"
                f"{_ctrl_kw})"
                f" -> NDArray[{_np(return_type)}]:",
            ]
            lines += _builtin_doc(
                f"{obj}_steps",
                [("x", f"NDArray[{_np(arg_type)}]")],
                f"NDArray[{_np(return_type)}]",
                # gh-867: the standalone face's exact wording. Two
                # spellings of one canned summary is the same drift one
                # layer down.
                "Process a samples array. Returns ndarray, or fills out= if "
                "supplied.",
            )
        else:
            lines += [
                "",
                f"    def steps(self, x: NDArray[{_np(arg_type)}]"
                f"{_ctrl_kw}) -> None:",
            ]
            lines += _builtin_doc(
                f"{obj}_steps",
                [("x", f"NDArray[{_np(arg_type)}]")],
                "None",
                # gh-881: the standalone face's wording. These canned
                # summaries drifted per shape; the standalone is the
                # reference face, and for the void-return shapes it is also
                # the accurate one.
                "Process a block of input samples.",
            )
    else:
        lines += [
            "",
            f"    def step(self{_ctrl_kw}{_ctrl_posonly})"
            f" -> {_py(return_type)}:",
        ]
        lines += _builtin_doc(
            f"{obj}_step",
            [],
            _py(return_type),
            (
                "Generate one output sample from internal state."
                if return_type != "void"
                else "Advance state by one tick (no I/O)."
            ),
        )
        if return_type != "void":
            lines += [
                "",
                # gh-527 fixed this default on the standalone face only:
                # without it `obj.steps()` type-checks against one stub
                # and fails against the other, for the same object.
                f"    def steps(self, n: int = 1{_ctrl_kw})"
                f" -> NDArray[{_np(return_type)}]:",
            ]
            lines += _builtin_doc(
                f"{obj}_steps",
                [("n", "int")],
                f"NDArray[{_np(return_type)}]",
                "Generate n output samples.",
                param_fallback="Number of samples to generate.",
            )
        else:
            lines += [
                "",
                f"    def steps(self, n: int = 1{_ctrl_kw}) -> None:",
            ]
            lines += _builtin_doc(
                f"{obj}_steps",
                [("n", "int")],
                "None",
                "Run n iterations.",  # gh-881: standalone wording
                param_fallback="Number of iterations to run.",
            )

    # extra methods
    for m in obj_methods:
        m_name = m["name"]
        _blk = doc_blocks.get(f"{obj}_{m_name}")
        if m.get("varargs"):
            _va_doc = (
                m.get("doc")
                or (_blk.brief if (_blk and _blk.brief) else "")
                or f"{m_name.replace('_', ' ').capitalize()}."
            )
            lines += [
                "",
                f"    def {m_name}(self, *args: Any, **kwargs: Any) -> Any:",
                f'        """{_va_doc}"""',
            ]
            continue
        if m.get("manual_stub"):
            lines += [
                "",
                f"    def {m_name}(self, *args: Any, **kwargs: Any) -> Any:",
                f'        """{_MANUAL_STUB_PLACEHOLDER} hand-write this'
                " signature/docstring in the .pyi — jm preserves it"
                ' verbatim on future regens."""',
            ]
            continue
        # codec-pack method (gh-554): the SAME renderer the standalone stub
        # uses (via _codec.render_pack), so the two .pyi peers cannot drift.
        if _codec.is_codec_method(m):
            cdc = C.codecs(cfg).get(m["codec"])
            if cdc is not None:
                lines += ["", *_codec.render_method_pyi(m, cdc)]
            continue
        m_ret = m.get("return_type", "void")
        m_params = m.get("params", [])
        m_arg = m.get("arg_type", "void")
        m_var = m.get("variable_output", False)
        m_multi = m.get("multi_output", [])
        m_result_fields = m.get("result_fields", [])
        m_py_return_type = m.get("py_return_type", "")

        param_parts: list[str] = []
        # gh-385: a variable_output method consumes a *block* of arg_type
        # elements — its generated binding parses a numpy array (PyArray_FROM_
        # OTF) and passes PyArray_DATA as the input block, and its output is
        # already rendered as an NDArray below — so a non-array (element)
        # arg_type means an array input here, not a scalar.
        _x_ann = ""
        if m_arg != "void":
            _x_ann = (
                f"NDArray[{_np(m_arg)}]"
                if (m_var and not m_arg.endswith("[]"))
                else _py(m_arg)
            )
            param_parts.append(f"x: {_x_ann}")
        for p in m_params:
            # gh-432: a capsule param takes the named PyCapsule, a wrapper
            # exposing `_capsule`, or None (detach).
            if p.get("capsule"):
                param_parts.append(f"{p['name']}: object | None")
                continue
            # gh-240: a defaulted param renders as an optional kwarg.
            pann = f"{p['name']}: {_py(p['type'])}"
            if p.get("default"):
                pann += f" = {p['default']}"
            param_parts.append(pann)

        if m_py_return_type:
            ret_ann = m_py_return_type
        elif m.get("status_return"):
            # gh-432: status returns bind as None (raise on failure).
            #
            # gh-805 §B: `error_negative` is deliberately NOT folded in here.
            # It also raises, but the int is the VALUE on success, so its
            # annotation is the ordinary scalar one and it must fall through.
            # The two keys are mutually exclusive at declaration time.
            ret_ann = "None"
        elif m_result_fields and not (m_var and m.get("record_dtype")):
            # gh-244: a `single` method returns ONE record, not a list of them.
            # gh-646: and that record is a declared class, not a bare tuple —
            # `make_module_pyi` emits it from the same `_record` builder the
            # standalone stub and the C descriptor use.
            # gh-788: a `record_dtype` method carries `result_fields` too, but
            # they are the columns of ONE structured ndarray rather than a
            # list of per-row tuples — peer of the same guard in
            # make_methods_ctx, and the two must move together.
            field_types = ", ".join(_py(f["type"]) for f in m_result_fields)
            ret_ann = (
                _record.public_name(m)
                if m.get("single")
                else f"list[tuple[{field_types}]]"
            )
        elif m_var:
            # gh-788: a structured row has no scalar numpy scalar-type to
            # name, so it annotates as NDArray[Any] — which is what `_np`
            # already yields for a type it does not know.
            all_rts = [m.get("record_dtype") or m_ret] + list(m_multi)
            ndarrays = [f"NDArray[{_np(rt)}]" for rt in all_rts]
            ret_ann = (
                f"tuple[{', '.join(ndarrays)}]"
                if len(ndarrays) > 1
                else ndarrays[0]
            )
        elif m.get("out_type"):
            # gh-529: peer of the `out_type` branch in
            # make_methods_ctx's return-annotation resolution -- a method
            # with out_type returns a fresh ndarray, not the scalar m_ret.
            # This is the module-aggregated .pyi; the standalone one lives in
            # _context/_methods.py, and the two are pinned together by
            # tests/test_gh529_method_out_type_pyi.py.
            ret_ann = f"NDArray[{_np(m['out_type'])}]"
        else:
            ret_ann = _py(m_ret)

        # gh-423: mirror make_methods_ctx's _enable_out/_single_array_param
        # (gh-219) here -- this loop is a separate stub generator for the
        # module-aggregated .pyi and was never taught the out=/_max_out()
        # shape, so it kept emitting the pre-#219 signature after that fix.
        _m_single_array_param = (
            m_arg == "void"
            and len(m_params) == 1
            and m_params[0]["type"].endswith("[]")
        )
        _stub_enable_out = (
            m_var and not m_multi and (not m_params or _m_single_array_param)
        )
        # gh-527: a variable_output method with no input to size from is the
        # generator shape -- make_methods_ctx seeds a leading `count` for it
        # (kwlist {"count", "out"} when an out= is offered, a positional "|n"
        # otherwise). The seed is `1` unless the method declares
        # `count_default`, which gh-657 made settable because that default is
        # the whole behaviour of the zero-arg call. The stub omitted it
        # entirely, so the call that actually works (`obj.run(4)`) failed to
        # type-check while `obj.run(out=...)` passed. `count` precedes `out`
        # to match the kwlist order.
        _stub_count_arg = m_var and m_arg == "void" and not m_params
        if _stub_count_arg:
            param_parts.append("count: int = 1")
        if _stub_enable_out:
            param_parts.append(f"out: {ret_ann} | None = None")

        sig = ", ".join(param_parts)
        # (name, annotation) for the Python-facing args, for the doc builder.
        _py_params: list[tuple[str, str]] = []
        if m_arg != "void":
            _py_params.append(("x", _x_ann))
        for p in m_params:
            _py_params.append(
                (
                    p["name"],
                    "object | None" if p.get("capsule") else _py(p["type"]),
                )
            )
        _doc = _method_doc_lines(
            _blk,
            m_name,
            _py_params,
            ret_ann,
            override=m.get("doc", ""),
            raises=_raises_doc(m),
        )
        header = (
            f"    def {m_name}(self, {sig}) -> {ret_ann}:"
            if sig
            else f"    def {m_name}(self) -> {ret_ann}:"
        )
        lines += ["", header, *_doc]
        if _stub_enable_out:
            # gh-607: mirror make_methods_ctx's `_max_out_count_param_ctx` —
            # `*_max_out()` now takes the same count the binding is about to
            # pass to the kernel; the all-scalar-params shape has none to
            # mirror and stays the original zero-arg form.
            _stub_moc_name: str | None = None
            if m_arg != "void":
                _stub_moc_name = "n_in"
            elif m_params:
                for p in m_params:
                    if p["type"].endswith("[]"):
                        _stub_moc_name = f"{p['name']}_len"
                        break
            else:
                _stub_moc_name = "n"
            # gh-761: the header's prototype overrides all of the above, the
            # same way it does for the binding — a state-only
            # `<obj>_<m>_max_out(<obj>_state_t *)` takes no count, and this
            # module-aggregated stub must say so too. Rendering it from the
            # method's own params is what let the stub and the binding
            # disagree on 48 of doppler's surfaces.
            if max_out_is_state_only(doc_blocks, f"{obj}_{m_name}_max_out"):
                _stub_moc_name = None
            # gh-684: header block wins; _gluedoc is the fallback. Same
            # lookup the standalone path uses, so the faces cannot drift.
            _mo_blk = doc_blocks.get(f"{obj}_{m_name}_max_out")
            _mo_gm = _max_out_method(
                m_name, _stub_moc_name or "", int(m.get("max_out", 0) or 0)
            )
            if _mo_blk is not None:
                _mo_gm = _replace(_mo_gm, block=_mo_blk)
            _mo_sig = (
                f"self, {_stub_moc_name}: int" if _stub_moc_name else "self"
            )
            lines += ["", f"    def {m_name}_max_out({_mo_sig}) -> int:"]
            lines += _mo_gm.pyi_doc()

    # serializable (gh-400): state-blob triplet, sibling to reset. The module
    # .pyi is assembled here independently of make_methods_ctx's
    # pyi_extra_methods (which drives the standalone COMPONENT_PYI), so the
    # triplet must be emitted in both paths to keep the type stub complete.
    if C.is_serializable(cfg, obj):
        # gh-647: same prose as the standalone path and the runtime method
        # table, from the one definition in _gluedoc.
        _glue = glue_methods(Component)
        for _n, _sig in (
            ("state_bytes", "self) -> int"),
            ("get_state", "self) -> bytes"),
            ("set_state", "self, blob: bytes) -> None"),
        ):
            lines += ["", f"    def {_n}({_sig}:"]
            lines += _glue[_n].pyi_doc()

    # State get_/set_ accessors. The module runtime emits these for every
    # non-opaque state var (via make_state_ctx, the same builder that generates
    # the standalone object), but this stub writer historically emitted none of
    # them — so `obj.get_gain()` was importable yet invisible to a type checker.
    # Reuse the ONE builder the standalone `.pyi` uses (state_accessor_stubs),
    # computing the accessor set exactly as make_state_ctx does: scalars are the
    # _CTYPE_META state vars, arrays the fixed-length ones; opaque fields (in
    # neither set) get no accessor, matching the C.
    _acc_scalars = [
        (n, ct, d) for n, ct, d in state_vars if ct in T._CTYPE_META
    ]
    _acc_arrays: list[tuple[str, str, int]] = []
    for n, ct, _ in state_vars:
        _parsed = T.parse_array_type(ct)
        if _parsed:
            _acc_arrays.append((n, _parsed[0], _parsed[1]))
    # gh-684: the module-aggregated stub derives accessors too, from the
    # same blocks the standalone path uses.
    _acc_pyi = Ctx.state_accessor_stubs(
        _acc_scalars, _acc_arrays, obj, doc_blocks
    )
    if _acc_pyi:
        lines += _acc_pyi.rstrip("\n").lstrip("\n").split("\n")

    # Properties — rendered by make_properties_ctx, the same builder that emits
    # the C getset table and the standalone .pyi. This used to be an
    # independent second implementation, and the two had diverged in exactly
    # the way gh-446 warned about:
    #
    #   - It treated a property aliasing a state var as writable
    #     (`or p_name in state_names`), while the C emits NULL for the setter.
    #     The stub advertised `@x.setter` for a read-only property, so mypy
    #     passed and the assignment raised AttributeError at runtime. State
    #     vars produce no property at all, so the clause compensated for
    #     nothing — it just lied.
    #   - It annotated with `_py()`, which has no buf_field notion, so a
    #     `--buf-field` property was typed as a scalar instead of NDArray.
    #
    # One renderer means those can't drift again.
    _prop_pyi = Ctx.make_properties_ctx(
        obj,
        Component,
        obj_props,
        frozenset(state_names),
        doc_blocks=doc_blocks,
        enums=C.enums(cfg),  # gh-519: `enum` properties annotate as Literal
        codecs=C.codecs(cfg),  # gh-554: codec properties annotate as the union
    )["property_stubs_pyi"]
    if _prop_pyi:
        lines += _prop_pyi.rstrip("\n").split("\n")

    # Stream generator (gh-203): a streamable object grows stream()/__iter__.
    _stream_pyi = _obj_stream_pyi(cfg, obj)
    if _stream_pyi:
        lines.append(_stream_pyi.rstrip("\n"))

    # gh-541/gh-544: the teardown stubs come from the ONE builder the
    # standalone `.pyi` template also uses. There are two stub generators in
    # this repo (this one for module-aggregated types, `_context._methods` /
    # COMPONENT_PYI for standalone) and this repo has repeatedly shipped a fix
    # to only one of them — so this deliberately shares the renderer rather
    # than restating the method list. `pyi_destroy_methods` is
    # "\n    def <name>...\n" per name, whose split reproduces the previous
    # literal list exactly when nothing is declared.
    _dctx = Ctx.make_destroy_ctx(
        obj,
        Component,
        C.destroy_spec(cfg, obj),
        C.methods(cfg, obj),  # gh-856
    )
    lines += _dctx["pyi_destroy_methods"].split("\n")
    # gh-647: the context-manager protocol used to be the one part of the
    # generated surface with no docstring on either face, so `help()` showed
    # nothing at all for it.
    # gh-864: the MODULE-AGGREGATED stub is a second doc-face producer, and
    # gh-805 §H wired only the one in `_context/_destroy`. So a module object
    # with `exit` kept "releasing the X" / "Equivalent to calling destroy()"
    # here — false on both counts, over a body that finalizes and leaves the
    # object usable. jm has five `.pyi` producers; fixing one is how gh-747
    # happened, and this is the same shape again.
    #
    # gh-869: so it stopped being a producer. It used to rebuild the pair from
    # `glue_methods` — the same call `make_destroy_ctx` had *just* made, three
    # lines above, with the same arguments — and that second call is what kept
    # needing the same fix twice: __exit__'s declared `Raises` reached the
    # slot and not the restatement, so a module object documented no exception
    # over the identical raising body. The slots are read instead.
    lines += ["", f'    def __enter__(self) -> "{Component}":']
    lines += _dctx["pyi_enter_doc"].split("\n")
    # Signature from the same param list that drives the documented
    # Parameters section, so griffe never sees a documented name the
    # signature lacks.
    lines += ["", f"    def __exit__({_dctx['pyi_exit_sig']}) -> None:"]
    lines += _dctx["pyi_exit_doc"].split("\n")

    return "\n".join(lines)


# ── module-level function stub ────────────────────────────────────────────────


def fn_py_surface(fn: dict) -> tuple[str, list[tuple[str, str]], list[str]]:
    """The Python-facing surface of one module free function.

    Returns the three things both faces need, derived once (gh-643). The
    ``.pyi`` stub below uses all three; ``_render.make_functions_ctx`` takes
    the first two to render the runtime ``PyMethodDef`` doc from the same
    header block, so ``help(fn)`` and ``fn`` in the stub cannot document
    different arguments or a different return type.

    Returns
    -------
    tuple
        ``(ret_ann, py_params, signature_parts)`` — the return annotation,
        the ``(name, annotation)`` pairs the ``Parameters`` section
        documents, and the ``"name: ann = default"`` strings the stub
        signature needs (defaults belong to the signature alone).
    """
    out_type = fn.get("out_type")
    if fn.get("check_return"):
        # gh-363: the int status is consumed by a raise-on-non-zero; the Python
        # surface is "succeeds or raises", i.e. returns None.
        ret = "None"
    elif out_type:
        # Strip optional [param_name] length suffix (e.g. "float64[M]" → "float64")
        _ot_base = _re.sub(r"\[[A-Za-z_][A-Za-z_0-9]*\]$", "", out_type)
        # Resolve numpy dtype aliases (e.g. "float64" → "double") for _py().
        _ot_ctype = _DTYPE_TO_CTYPE.get(_ot_base, _ot_base)
        ret = _py(f"{_ot_ctype}[]")
    else:
        ret = _py(fn.get("return_type", "void"))
    # gh-240: a param with a `default` is optional — surface it in the stub
    # (`name: type = <default>`) so type-checkers and readers see the default.
    parts: list[str] = []
    py_params: list[tuple[str, str]] = []
    for p in fn.get("params", []):
        # gh-353: a path arg accepts str | os.PathLike (via _py, which now
        # spells it for every surface — gh-623); an enum arg (type "int" with
        # an `enum` name) accepts the choice string.
        if p.get("enum"):
            ann = "str"
        else:
            ann = _py(p["type"])
        py_params.append((p["name"], ann))
        part = f"{p['name']}: {ann}"
        if p.get("default") not in (None, ""):
            # An enum default is a choice string — quote it; scalar defaults are
            # C literals shown verbatim (gh-240 behavior).
            dflt = repr(p["default"]) if p.get("enum") else p["default"]
            part += f" = {dflt}"
        parts.append(part)
    return ret, py_params, parts


def _fn_stub(fn: dict, block=None) -> str:
    name = fn["name"]
    doc = fn.get("doc", "")
    ret, py_params, parts = fn_py_surface(fn)
    sig = f"def {name}({', '.join(parts)}) -> {ret}:"
    # gh-384: when the module header carries Doxygen for this free function,
    # synthesize the full numpy docstring (brief + params + a runnable Examples
    # doctest from @code), same as object methods. With no block, keep the
    # historical one-line stub so a manifest-only/scaffold rebuild is unchanged.
    if block is not None:
        from ._docstring import render_numpy_doc

        doc_lines = render_numpy_doc(
            block, name, py_params, ret, override=doc, indent=4
        )
        return f"{sig}\n" + "\n".join(doc_lines)
    one_liner = (
        doc.split("\n")[0]
        if doc
        else name.replace("_", " ").capitalize() + "."
    )
    return f'{sig}\n    """{one_liner}"""'


# ── numpy import decision ─────────────────────────────────────────────────────


def _uses_any(cfg: dict, module: str) -> bool:
    """True if any object in this module needs ``Any`` in its stub.

    Two sources: a varargs/manual_stub method (gh-428), which renders as
    ``(*args: Any, **kwargs: Any) -> Any``; and (gh-543) a container property
    whose ``value_type`` is ``object``, which renders as ``dict[str, Any]`` /
    ``list[Any]`` / ``tuple[Any, ...]`` because the core -- not jm -- decides
    each value's Python type.

    View properties are scanned alongside the object's own, mirroring
    ``_uses_literal``: a container declared only on a view still lands in this
    module's stub, and missing it would emit an undefined ``Any``.
    """
    for obj in C.module_objects(cfg, module):
        for m in C.methods(cfg, obj):
            if m.get("varargs") or m.get("manual_stub"):
                return True
        props = list(C.properties(cfg, obj))
        for v in C.views(cfg, obj):
            props += C.view_properties(v)
        for prop in props:
            if (
                T.is_container_type(prop.get("type", ""))
                and (prop.get("value_type") or T.OBJECT_VALUE_TYPE)
                == T.OBJECT_VALUE_TYPE
            ):
                return True
    return False


def _uses_literal(cfg: dict, module: str) -> bool:
    """Return True if the module's stub needs ``Literal``.

    Two independent sources: a ``string_enum:`` init param, and (gh-519) a
    property that decodes through the ``[[enum]]`` SSOT — both annotate as
    ``Literal[...]``."""
    enum_reg = C.enums(cfg)
    for obj in C.module_objects(cfg, module):
        for param in C.init_params(cfg, obj):
            if param[1].startswith("string_enum:"):
                return True
        props = list(C.properties(cfg, obj))
        for v in C.views(cfg, obj):
            props += C.view_properties(v)
        for prop in props:
            if prop.get("enum") in enum_reg:
                return True
    return False


def _uses_os(cfg: dict, module: str) -> bool:
    """Return True if any path surface in this module needs ``os`` (gh-353).

    A path annotates as ``str | os.PathLike``, so the stub must ``import os``.
    Both surfaces count: a ``jm function`` param (gh-353) and an object
    init-param (gh-623 — before that, an init-param annotated bare ``str`` and
    so needed no import, which is exactly the narrowness gh-623 fixed)."""
    for fn in C.module_functions(cfg, module):
        for p in fn.get("params", []):
            if p["type"] == "path":
                return True
    for obj in C.module_objects(cfg, module):
        # init_params are 10-tuples; [1] is the type (see C.init_params).
        if any(ip[1] == "path" for ip in C.init_params(cfg, obj)):
            return True
    return False


def _uses_numpy(cfg: dict, module: str) -> bool:
    """Return True if any object in this module uses numpy (steps or arrays)."""
    for obj in C.module_objects(cfg, module):
        at = C.arg_type(cfg, obj)
        rt = C.return_type(cfg, obj)
        # Any non-void arg/return → steps() uses NDArray
        if at not in ("void",) or rt not in ("void",):
            return True
        for m in C.methods(cfg, obj):
            if m.get("variable_output"):
                return True
            for p in m.get("params", []):
                if p["type"].endswith("[]"):
                    return True
    for fn in C.module_functions(cfg, module):
        if fn.get("out_type"):
            return True
        for p in fn.get("params", []):
            if p["type"].endswith("[]"):
                return True
    return False


# ── public entry point ────────────────────────────────────────────────────────


def make_module_pyi(cfg: dict, module: str, root=None) -> str:
    """Return the full __init__.pyi content for *module*.

    Example output (module='dsp', objects=['filt'], functions=['apply'])::

        # dsp/__init__.pyi — type stubs for the dsp C extension.
        import numpy as np
        from numpy.typing import NDArray

        class Filt:
            def __init__(self, coeff: float = ...) -> None: ...
            def step(self, x: float) -> float: ...
            def steps(self, x: NDArray[np.float32]) -> NDArray[np.float32]: ...
            def reset(self) -> None: ...
            @property
            def gain(self) -> float: ...
            @gain.setter
            def gain(self, value: float) -> None: ...

        def apply(x: float) -> float: ...
    """
    pkg = C.project_name(cfg)
    objects = C.module_objects(cfg, module)
    # The .pyi sits beside the .so at src/<pkg>/<pypath>/<leaf>.pyi — or, when
    # the module declares a gh-523 `package`, inside that package instead.
    mp = C.module_paths(module)
    out_pkg = C.module_package(cfg, module) or mp.pypath

    needs_numpy = _uses_numpy(cfg, module)
    needs_literal = _uses_literal(cfg, module)
    needs_any = _uses_any(cfg, module)
    needs_os = _uses_os(cfg, module)  # gh-353: a path param -> os.PathLike
    # gh-203: a streamable object's stub references Callable + Iterator.
    needs_stream = any(_obj_stream_pyi(cfg, o) for o in objects)
    # An async-streamable object's stub also references AsyncIterator (its
    # `__aiter__`). The module import assembly omitted it, so an async stream
    # in a module emitted `-> AsyncIterator[...]` with no import -- an
    # undefined name (the standalone template's stream slot never had this
    # gap; only the module-aggregated peer did). Caught by the stub-conformance
    # gate's async-stream shape.
    needs_async = any(C.is_async_stream(cfg, o) for o in objects)
    # gh-554: a codec-pack method's variant arg is typed with the codec's
    # Python union in `Sequence` form (it accepts a scalar or any sequence), so
    # `Sequence` must be imported whenever the module has one — else the stub
    # references an undefined name (the same class of gap as needs_async).
    _codec_tbl = C.codecs(cfg)
    needs_sequence = any(
        "Sequence["
        in _codec.codec_py_union(_codec_tbl[m["codec"]], seq="Sequence")
        for o in objects
        for m in C.methods(cfg, o)
        if _codec.is_codec_method(m) and m.get("codec") in _codec_tbl
    )
    parts: list[str] = [
        f"# {out_pkg}/{mp.leaf}.pyi — type stubs for the {module} C extension."
    ]
    # Every object class is @final (a Py_TPFLAGS_DEFAULT extension type cannot
    # be subclassed), so `final` is imported whenever the module has objects.
    needs_final = bool(objects)
    if needs_literal or needs_any or needs_stream or needs_final:
        typing_imports = ", ".join(
            x
            for x in [
                "Any" if needs_any else "",
                "AsyncIterator" if needs_async else "",
                "Callable" if needs_stream else "",
                "final" if needs_final else "",
                "Iterator" if needs_stream else "",
                "Literal" if needs_literal else "",
            ]
            if x
        )
        parts.append(f"from typing import {typing_imports}")
    if needs_sequence:
        parts.append("from collections.abc import Sequence")
    if needs_os:
        parts.append("import os")
    if needs_numpy:
        parts.append("import numpy as np")
        parts.append("from numpy.typing import NDArray")
    functions = C.module_functions(cfg, module)
    # gh-384: header Doxygen for free functions, stashed transiently on cfg by
    # build_component_ctxs() (mirrors the per-object _doc_blocks). Empty when
    # the module has no header / hand-written function comments.
    # gh-384: synthesize free-function docstrings (incl. @code Examples) from
    # the module header Doxygen, same as object methods. Only when a project
    # root is supplied (the apply/regenerate path); direct callers without a
    # root keep the historical one-line stubs. Local import avoids a cycle
    # (_object imports _stubs); the loader honours _object's apply-replay
    # _DOC_ROOT_OVERRIDE just like the per-object blocks.
    if root is not None:
        from ._object import _load_module_doc_blocks

        fn_doc_blocks = _load_module_doc_blocks(root, module)
    else:
        fn_doc_blocks = {}

    if objects:
        parts.append("")
    # gh-646: a single-record method annotates its return with the record's own
    # class, so every such class is declared here, above the object classes
    # that reference it. Deduplicated across the whole module: two objects
    # returning the same record declare it once.
    _rec_block = _record.pyi_classes(
        [
            m
            for obj in objects
            # A view's own method is stubbed on the view class below and can
            # return a record the parent never does; leaving it out would emit
            # a `-> ToneMetrics` annotation with no ToneMetrics declared.
            for m in C.methods(cfg, obj)
            + [n for v in C.views(cfg, obj) for n in C.view_methods(v)]
        ],
        {
            k: v
            for obj in objects
            for k, v in (cfg.get(obj, {}).get("_doc_blocks", {}) or {}).items()
        },
    )
    if _rec_block:
        parts.append(_rec_block)
    for obj in objects:
        parts.append(_obj_stub(cfg, obj, pkg=pkg, module=module))
        parts.append("")
        # gh-504: each view is a second class over the same core. Render it via
        # the same _obj_stub, driven by an overlay cfg key that swaps in the
        # view's class_name / init_params / (filtered) properties. The synthetic
        # key never reaches output — a .pyi carries no C symbols — so this reuses
        # _obj_stub unchanged.
        for view in C.views(cfg, obj):
            excl = C.view_exclude_properties(view)
            excl_m = C.view_exclude_methods(view)
            synth = f"{obj}__view_{view['class_name'].lower()}"
            overlay = dict(cfg.get(obj, {}))
            overlay["class_name"] = view["class_name"]
            if view.get("init_params"):
                # The view's constructor takes its own params (it shares the
                # parent's state struct but builds it differently). _obj_stub
                # prefers state_vars over init_params for __init__, so drop the
                # inherited `state` here to make the view's init_params drive
                # __init__ — matching the C _init that parses exactly them. An
                # inheriting view (no own init_params) keeps `state` so its
                # __init__ mirrors the parent's.
                overlay["init_params"] = view["init_params"]
                overlay.pop("state", None)
            # gh-504: the view's surface = parent minus excludes, with the
            # view's OWN members merged over by name (override) or appended
            # (add) — same merge as _make_view_ctx, so the .pyi matches the C.
            own_props = C.view_properties(view)
            own_prop_names = {p["name"] for p in own_props}
            overlay["properties"] = [
                p
                for p in C.properties(cfg, obj)
                if p["name"] not in excl and p["name"] not in own_prop_names
            ] + own_props
            own_methods = C.view_methods(view)
            own_method_names = {m["name"] for m in own_methods}
            overlay["methods"] = [
                m
                for m in C.methods(cfg, obj)
                if m["name"] not in excl_m
                and m["name"] not in own_method_names
            ] + own_methods
            # gh-685: a view's methods call the PARENT's C functions, so the
            # parent's header blocks are theirs. _obj_stub looks blocks up as
            # `<component>_<member>` and the component here is a synthetic
            # name, so every lookup missed and every inherited method fell
            # back to the name-based stub -- while the identical method on the
            # parent, from the identical block, derived fully.
            #
            # Alias rather than replace: a view with its own `create_fn` has a
            # block under that real name, looked up directly.
            _parent_blocks = cfg.get(obj, {}).get("_doc_blocks", {}) or {}
            _view_blocks = _view_doc_blocks(cfg, obj, synth)
            overlay["_doc_blocks"] = {**_view_blocks, **_parent_blocks}
            # gh-761: the reserved `_max_out` arity key is a *set*, not a
            # block, so the parent-wins merge above would drop the
            # synthetic-id entries `_view_doc_blocks` just re-keyed. Union
            # both spellings instead: the view looks itself up under `synth`,
            # while a view with its own `create_fn` still resolves the real
            # name.
            _arity = frozenset(
                _view_blocks.get(max_out_arity_key()) or ()
            ) | frozenset(_parent_blocks.get(max_out_arity_key()) or ())
            if _arity:
                overlay["_doc_blocks"][max_out_arity_key()] = _arity
            # gh-648: the view's own `doc=` owns its class docstring. Every
            # other overlay key was set and this one was not, so a view whose
            # class-level semantics genuinely differ from its parent's --
            # doppler's Acquisition / BurstAcquisition, MatchedDDC /
            # MatchedDdcr: two front doors over one core -- described itself
            # correctly at runtime (`tp_doc` reads the view `doc=`) and
            # inherited the parent's text in the stub beside it.
            #
            # Absent, the parent's `doc` stays in the overlay, so a view that
            # declares nothing is unchanged.
            if view.get("doc"):
                overlay["doc"] = view["doc"]
            cfg_v = {**cfg, synth: overlay}
            parts.append(_obj_stub(cfg_v, synth, pkg=pkg, module=module))
            parts.append("")

    for fn in functions:
        parts.append(_fn_stub(fn, fn_doc_blocks.get(fn["name"])))
        parts.append("")

    # strip trailing blank line
    while parts and parts[-1] == "":
        parts.pop()

    # gh-744: the module aggregator's own exit, matching
    # `_render.render_component_pyi` for the standalone stub. Both stub
    # producers reflow, so neither face of the drift gate sees raw text.
    from ._pyfmt import reflow_pyi

    return reflow_pyi("\n".join(parts)) + "\n"
