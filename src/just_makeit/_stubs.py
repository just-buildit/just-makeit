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

from . import _config as C
from . import _context as Ctx

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
    return _CTYPE_TO_PY.get(ctype, "Any")


def _np(ctype: str) -> str:
    """Return the numpy dtype string for a C type (scalar or array) for NDArray hints."""
    elem = ctype
    while elem.endswith("[]"):
        elem = elem[:-2]
    return _CTYPE_TO_NP.get(elem, "Any")


# ── per-object class stub ─────────────────────────────────────────────────────


def _title(name: str) -> str:
    return "".join(w.title() for w in name.split("_"))


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


def _splice_manual_stub_bodies(cfg: dict, old_text: str, new_text: str) -> str:
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
    return out


_ARRAY_RE = _re.compile(r"^([\w\s_]+)\[(\d+)\]$")


def _py_default_stub(ctype: str, default: str) -> str:
    """Convert a C default literal to a Python literal (stub helper)."""
    if ctype not in _CTYPE_TO_PY:
        return "..."
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
    lines: list[str] = [f'    """{summary}', ""]

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
    param_lines: list[str] = []
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
            if required:
                # gh-266: no default — document it as a required parameter.
                param_lines += [
                    f"    {name} : {py_t}",
                    f"        {_pdesc(name, manifest_doc, True)}",
                ]
                continue
            if ctype.startswith("string_enum:"):
                py_d = f'"{dflt}"' if dflt else "..."
            else:
                py_d = _py_default_stub(ctype, dflt)
            param_lines += [
                f"    {name} : {py_t}, default {py_d}",
                f"        {_pdesc(name, manifest_doc, False)}",
            ]
    elif state_vars and not no_state:
        for name, ctype, dflt in state_vars:
            m = _ARRAY_RE.match(ctype.strip())
            if m:
                elem, size = m.group(1).rstrip(), m.group(2)
                npt = _CTYPE_TO_NP.get(elem, "Any")
                param_lines += [
                    f"    {name} : NDArray[{npt}]",
                    f"        Length-{size} array, zero-initialised.",
                ]
            else:
                py_t = _CTYPE_TO_PY.get(ctype, "Any")
                py_d = _py_default_stub(ctype, dflt)
                param_lines += [
                    f"    {name} : {py_t}, default {py_d}",
                    f"        {name} state variable.",
                ]

    if param_lines:
        lines += ["    Parameters", "    ----------"] + param_lines + [""]

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
    # doctest that raises TypeError. In that case, skip the Examples block
    # entirely rather than ship a broken example.
    if "..." in py_create_args:
        lines.append('    """')
        return lines

    ex: list[str] = [
        "    Examples",
        "    --------",
        "    Create with defaults:",
        "",
        f"    >>> {import_line}",
        f"    >>> obj = {Component}({py_create_args})",
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
    lines += ex
    lines.append('    """')
    return lines


def _numpy_doc_lines(
    block,
    name: str,
    py_params: list[tuple[str, str]],
    ret_ann: str,
    override: str = "",
    *,
    indent: int = 8,
) -> list[str]:
    """Return `.pyi` numpy-docstring lines, indented by *indent* spaces.

    Shared by object methods (``indent=8``, inside a class) and module-level
    free functions (``indent=4``, top level). Summary precedence: *override*
    (TOML ``doc``) > the Doxygen *block*'s ``@brief`` > name fallback. With an
    override or a block, emit a numpy-style docstring (summary + Parameters +
    Returns + a runnable ``Examples`` doctest from ``@code``); otherwise fall
    back to the historical one-line name-based stub.
    """
    pad = " " * indent
    pad2 = " " * (indent + 4)
    if block is None and not override:
        return [f'{pad}"""{name.replace("_", " ").capitalize()}."""']
    from ._docstring import _wrap, render_numpy_method_doc

    if block is not None:
        summary, body, descs, ret, examples = render_numpy_method_doc(
            block, py_params
        )
    else:
        summary, body, descs, ret, examples = "", [], {}, "", []
    summary = override or summary
    if not summary:
        summary = name.replace("_", " ").capitalize() + "."
    out = [f'{pad}"""{summary}']
    for para in body:  # extended description — flowing, wrapped paragraphs
        out.append("")
        out += [f"{pad}{w}" for w in _wrap(para, 72)]
    if py_params:
        out += ["", f"{pad}Parameters", f"{pad}----------"]
        for pname, ann in py_params:
            out.append(f"{pad}{pname} : {ann}")
            out.append(f"{pad2}{descs.get(pname) or 'Input.'}")
    if ret_ann != "None":
        out += [
            "",
            f"{pad}Returns",
            f"{pad}-------",
            f"{pad}{ret_ann}",
            f"{pad2}{ret or 'Output.'}",
        ]
    if examples:  # @code ... @endcode -> runnable doctest
        out += ["", f"{pad}Examples", f"{pad}--------"]
        out += [f"{pad}{ex}".rstrip() for ex in examples]
        # Trailing blank: under pytest --doctest-glob the .pyi is parsed as a
        # text file, where expected output runs until a blank line — without
        # this the closing `"""` is swallowed into the last example's output.
        out.append("")
    out.append(f'{pad}"""')
    return out


def _method_doc_lines(
    block,
    m_name: str,
    py_params: list[tuple[str, str]],
    ret_ann: str,
    override: str = "",
) -> list[str]:
    """Return indented `.pyi` docstring lines for an object method."""
    return _numpy_doc_lines(
        block, m_name, py_params, ret_ann, override, indent=8
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
    no_state = C.is_no_state(cfg, obj)
    # Controllable per-call overrides (gh-240): step() shows them positional-
    # only (trailing `/`, since its binding rejects keyword calls); steps()
    # shows them keyword-capable. Empty unless a field is controllable.
    _ctrl = C.controllable_state_vars(cfg, obj)
    _ctrl_kw = "".join(f", {n}: {_py(ct)} = ..." for n, ct in _ctrl)
    _ctrl_posonly = ", /" if _ctrl else ""

    def _builtin_doc(cfn, py_params, ret_ann, fallback_doc):
        """Docstring lines for a built-in method: the header Doxygen for *cfn*
        when present (so reset/step/steps are documentable), else the canned
        one-liner *fallback_doc*."""
        blk = doc_blocks.get(cfn)
        if blk is not None:
            return _method_doc_lines(blk, cfn, py_params, ret_ann)
        return [f'        """{fallback_doc}"""']

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
        # self-documenting (string_enums show their chosen string).
        ", ".join(_ctor_arg(p) for p in ip)
        if ip
        else (
            ", ".join(
                _py_default_stub(ct, dflt) for _, ct, dflt in scalar_vars
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

    # Class docstring
    _create_blk = doc_blocks.get(f"{obj}_create")
    _class_brief = cfg.get(obj, {}).get("doc") or (
        _create_blk.brief if (_create_blk and _create_blk.brief) else ""
    )
    doc_lines = _build_class_docstring(
        Component,
        state_vars,
        no_state,
        list(ip),
        import_line,
        py_create_args,
        brief=_class_brief,
        # init_params imply a create_impl that derives state from the params
        # (the #69 contract), so the first state var is config — not guaranteed
        # zeroed by reset(). Skip the "reset restores defaults" demo there.
        # (init_params survive the apply-path cfg; reset_impl/create_impl don't.)
        custom_reset=bool(ip),
        create_blk=_create_blk,
    )
    lines: list[str] = [f"class {Component}:"] + doc_lines

    # __init__
    if state_vars and not no_state:
        init_params_str = ", ".join(
            f"{n}: {_py(t)} = ..." for n, t, _ in scalar_vars
        )
        lines.append(f"    def __init__(self, {init_params_str}) -> None: ...")
    elif ip:
        # gh-266: a required scalar has no default, so it is emitted without a
        # `= ...` placeholder and hoisted ahead of every defaulted parameter —
        # a default-less stub arg after a defaulted one is a syntax error, and
        # this mirrors the constructor's positional-before-`|` ordering.
        req_parts: list[str] = []
        parts_init: list[str] = []
        for param in ip:
            n, t = param[0], param[1]
            optional = param[6] if len(param) > 6 else False
            required = param[8] if len(param) > 8 else False
            if required and not t.endswith("[]"):
                req_parts.append(f"{n}: {_py(t)}")
            elif optional:
                parts_init.append(f"{n}: {_py(t)} | None = None")
            elif t.startswith("string_enum:"):
                dflt = param[2] if len(param) > 2 else ""
                parts_init.append(
                    f'{n}: {_py(t)} = "{dflt}"'
                    if dflt
                    else f"{n}: {_py(t)} = ..."
                )
            else:
                parts_init.append(f"{n}: {_py(t)} = ...")
        init_params_str = ", ".join(req_parts + parts_init)
        lines.append(f"    def __init__(self, {init_params_str}) -> None: ...")
    else:
        lines.append("    def __init__(self, /, *args, **kwargs) -> None: ...")

    # gh-131: skip the built-in reset() stub when the user declared a
    # [[methods]] entry named "reset"; that entry's stub appears below in
    # the extra-methods loop and must not be duplicated here.
    _user_has_reset = any(m["name"] == "reset" for m in obj_methods)
    if not _user_has_reset:
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
            "Process one input sample.",
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
                "Process a samples array.",
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
                "Process a samples array.",
            )
    else:
        lines += [
            "",
            f"    def step(self{_ctrl_kw}{_ctrl_posonly})"
            f" -> {_py(return_type)}:",
        ]
        lines += _builtin_doc(
            f"{obj}_step", [], _py(return_type), "Generate one output sample."
        )
        if return_type != "void":
            lines += [
                "",
                f"    def steps(self, n: int{_ctrl_kw})"
                f" -> NDArray[{_np(return_type)}]:",
            ]
            lines += _builtin_doc(
                f"{obj}_steps",
                [("n", "int")],
                f"NDArray[{_np(return_type)}]",
                "Generate n output samples.",
            )
        else:
            lines += [
                "",
                f"    def steps(self, n: int{_ctrl_kw}) -> None:",
            ]
            lines += _builtin_doc(
                f"{obj}_steps",
                [("n", "int")],
                "None",
                "Advance state by n ticks.",
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
                '        """<<MANUAL_STUB>> hand-write this signature/'
                "docstring in the .pyi — jm preserves it verbatim on"
                ' future regens."""',
            ]
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
            ret_ann = "None"
        elif m_result_fields:
            field_types = ", ".join(_py(f["type"]) for f in m_result_fields)
            # gh-244: a `single` method returns ONE record, not a list of them.
            ret_ann = (
                f"tuple[{field_types}]"
                if m.get("single")
                else f"list[tuple[{field_types}]]"
            )
        elif m_var:
            all_rts = [m_ret] + list(m_multi)
            ndarrays = [f"NDArray[{_np(rt)}]" for rt in all_rts]
            ret_ann = (
                f"tuple[{', '.join(ndarrays)}]"
                if len(ndarrays) > 1
                else ndarrays[0]
            )
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
            _blk, m_name, _py_params, ret_ann, override=m.get("doc", "")
        )
        header = (
            f"    def {m_name}(self, {sig}) -> {ret_ann}:"
            if sig
            else f"    def {m_name}(self) -> {ret_ann}:"
        )
        lines += ["", header, *_doc]
        if _stub_enable_out:
            lines += [
                "",
                f"    def {m_name}_max_out(self) -> int:",
                f'        """Max output length {m_name}() can produce'
                f' for the current state."""',
            ]

    # serializable (gh-400): state-blob triplet, sibling to reset. The module
    # .pyi is assembled here independently of make_methods_ctx's
    # pyi_extra_methods (which drives the standalone COMPONENT_PYI), so the
    # triplet must be emitted in both paths to keep the type stub complete.
    if C.is_serializable(cfg, obj):
        lines += [
            "",
            "    def state_bytes(self) -> int:",
            '        """Serialized state size in bytes."""',
            "    def get_state(self) -> bytes:",
            '        """Serialize the engine\'s mutable state to bytes."""',
            "    def set_state(self, blob: bytes) -> None:",
            '        """Restore mutable state from a get_state() blob."""',
        ]

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
    )["property_stubs_pyi"]
    if _prop_pyi:
        lines += _prop_pyi.rstrip("\n").split("\n")

    # Stream generator (gh-203): a streamable object grows stream()/__iter__.
    _stream_pyi = _obj_stream_pyi(cfg, obj)
    if _stream_pyi:
        lines.append(_stream_pyi.rstrip("\n"))

    lines += [
        "",
        "    def destroy(self) -> None:",
        '        """Release C resources immediately."""',
        "",
        '    def __enter__(self) -> "' + Component + '": ...',
        "",
        "    def __exit__(self, *args: object) -> None: ...",
    ]

    return "\n".join(lines)


# ── module-level function stub ────────────────────────────────────────────────


def _fn_stub(fn: dict, block=None) -> str:
    name = fn["name"]
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
    params = fn.get("params", [])
    doc = fn.get("doc", "")
    # gh-240: a param with a `default` is optional — surface it in the stub
    # (`name: type = <default>`) so type-checkers and readers see the default.
    parts = []
    py_params: list[tuple[str, str]] = []
    for p in params:
        # gh-353: a path arg accepts str | os.PathLike; an enum arg (type "int"
        # with an `enum` name) accepts the choice string.
        if p["type"] == "path":
            ann = "str | os.PathLike"
        elif p.get("enum"):
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
    sig = f"def {name}({', '.join(parts)}) -> {ret}:"
    # gh-384: when the module header carries Doxygen for this free function,
    # synthesize the full numpy docstring (brief + params + a runnable Examples
    # doctest from @code), same as object methods. With no block, keep the
    # historical one-line stub so a manifest-only/scaffold rebuild is unchanged.
    if block is not None:
        doc_lines = _numpy_doc_lines(
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
    """True if any object in this module has a varargs/manual_stub method
    (gh-428) — both render a ``(*args: Any, **kwargs: Any) -> Any`` stub."""
    for obj in C.module_objects(cfg, module):
        for m in C.methods(cfg, obj):
            if m.get("varargs") or m.get("manual_stub"):
                return True
    return False


def _uses_literal(cfg: dict, module: str) -> bool:
    """Return True if any object in this module has a string_enum init param."""
    for obj in C.module_objects(cfg, module):
        for param in C.init_params(cfg, obj):
            if param[1].startswith("string_enum:"):
                return True
    return False


def _uses_os(cfg: dict, module: str) -> bool:
    """Return True if any module function has a ``path`` param (gh-353).

    A path param annotates as ``str | os.PathLike``, so the stub must
    ``import os``."""
    for fn in C.module_functions(cfg, module):
        for p in fn.get("params", []):
            if p["type"] == "path":
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
    # The .pyi sits beside the .so at src/<pkg>/<pypath>/<leaf>.pyi.
    mp = C.module_paths(module)

    needs_numpy = _uses_numpy(cfg, module)
    needs_literal = _uses_literal(cfg, module)
    needs_any = _uses_any(cfg, module)
    needs_os = _uses_os(cfg, module)  # gh-353: a path param -> os.PathLike
    # gh-203: a streamable object's stub references Callable + Iterator.
    needs_stream = any(_obj_stream_pyi(cfg, o) for o in objects)
    parts: list[str] = [
        f"# {mp.pypath}/{mp.leaf}.pyi — type stubs for the "
        f"{module} C extension."
    ]
    if needs_literal or needs_any or needs_stream:
        typing_imports = ", ".join(
            x
            for x in [
                "Any" if needs_any else "",
                "Callable" if needs_stream else "",
                "Iterator" if needs_stream else "",
                "Literal" if needs_literal else "",
            ]
            if x
        )
        parts.append(f"from typing import {typing_imports}")
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
            overlay["properties"] = [
                p for p in C.properties(cfg, obj) if p["name"] not in excl
            ]
            cfg_v = {**cfg, synth: overlay}
            parts.append(_obj_stub(cfg_v, synth, pkg=pkg, module=module))
            parts.append("")

    for fn in functions:
        parts.append(_fn_stub(fn, fn_doc_blocks.get(fn["name"])))
        parts.append("")

    # strip trailing blank line
    while parts and parts[-1] == "":
        parts.pop()

    return "\n".join(parts) + "\n"
