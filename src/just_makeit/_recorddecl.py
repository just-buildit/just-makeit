"""_recorddecl.py -- `just-makeit record`: name a C struct and its columns.

gh-1405. A record is a struct the AUTHOR writes in the sacred header; this
declares its name and field list so jm can describe the same bytes to numpy
on both sides of the boundary::

    [[ring.records]]
    name = "iq16_t"
    fields = [
        { name = "i", type = "int16_t" },
        { name = "q", type = "int16_t" },
    ]

Declared ONCE and referenced twice — ``arg_type = "iq16_t[]"`` on the method
that writes rows, ``record_dtype = "iq16_t"`` on the one that reads them. The
whole reason the table exists is that a restatement drifts: doppler's ring
buffer has three hand-written faces that disagree today about what
``I16Buffer.wait`` returns.

A CLI command rather than TOML alone, because a declaration only reachable by
hand-editing the manifest is the foot-gun this project refuses: every shape
jm supports has a command that produces it.

The struct's *layout* is never taken from this list — the generated dtype
reads ``offsetof``/``sizeof`` from the compiler (`_record.dtype_c`), so a
padded struct cannot be described wrongly here. What this list decides is
which fields are exposed, and under what names.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

from . import _config as C
from . import _csym as CSYM
from . import _glue
from . import _object
from . import _record
from . import _report
from . import _script
from . import _types as T


def parse_field(spec: str) -> dict:
    """``"i:int16_t"`` -> ``{"name": "i", "type": "int16_t"}``.

    Raises
    ------
    ValueError
        When the spelling is not ``name:type``, or the type is not one jm
        can map to a numpy format.

    Examples
    --------
    >>> parse_field("i:int16_t")
    {'name': 'i', 'type': 'int16_t'}
    >>> parse_field("bad")
    Traceback (most recent call last):
    ValueError: --field wants name:type, got 'bad'
    """
    if spec.count(":") != 1:
        raise ValueError(f"--field wants name:type, got {spec!r}")
    name, ctype = (part.strip() for part in spec.split(":"))
    if not name or not ctype:
        raise ValueError(f"--field wants name:type, got {spec!r}")
    if ctype not in T._CTYPE_META:
        raise ValueError(
            f"field '{name}': type '{ctype}' is not a registered scalar.\n"
            f"A record column must be one of: {', '.join(sorted(T._CTYPE_META))}"
        )
    return {"name": name, "type": ctype}


def speakers(cfg: dict, comp: str, element: str) -> "list[tuple[str, str]]":
    """``(member, view)`` for each member of *comp* that speaks *element*.

    gh-2055. *view* is the class name of the view whose own method it is,
    or ``""`` for the object's own; a view's method spells the element the
    same way and binds a C function in the same ``_core.c``. In manifest
    order, which is the order a re-add has to restore.

    Examples
    --------
    >>> cfg = {"o": {"methods": [
    ...     {"name": "write", "arg_type": "sample[]"},
    ...     {"name": "step2", "arg_type": "double"},
    ...     {"name": "wait", "return_type": "sample", "borrow": True}]}}
    >>> speakers(cfg, "o", "sample")
    [('write', ''), ('wait', '')]
    """
    out = [
        (str(m["name"]), "")
        for m in C.methods(cfg, comp)
        if _record.speaks(m, element)
    ]
    for v in C.views(cfg, comp):
        out += [
            (str(m["name"]), str(v.get("class_name") or ""))
            for m in C.view_methods(v)
            if _record.speaks(m, element)
        ]
    return out


def _retype_refusal(
    cfg: dict,
    comp: str,
    old: dict,
    new: dict,
    who: "list[tuple[str, str]]",
) -> str:
    """Why a change of an element's C type under its speakers is refused,
    and the commands that make the change instead.

    gh-2055. The type an element stands for is in the C prototype of every
    member that speaks it. ``apply`` re-declares those prototypes in the
    header (gh-632) and never touches the author's definitions, so a
    re-declare under them leaves a header and a ``_core.c`` that disagree:
    measured, the core stops compiling (standalone), or the binding keeps
    the old type while the contract asserts the new one (module). `jm
    method` refuses the same change to one member outright.

    The route is printed as commands, rendered by `jm script`'s own flag
    writers from the manifest, so it re-adds each member exactly as
    declared -- when the element keeps its KIND. A switch between scalar
    and struct changes how a member spells it too (a struct is read back
    through ``--record-dtype``), and translating one spelling into the
    other is a guess, so that route stops at the re-declare and says how
    the new kind is spoken (:func:`_record.spoken_as`). Its gate runs every
    route as printed, through build and `jm test`.
    """
    element = str(new["name"])
    labels = [f"{v}.{n}" if v else n for n, v in who]
    listed = (
        labels[0]
        if len(labels) == 1
        else ", ".join(labels[:-1]) + " and " + labels[-1]
    )
    head = textwrap.fill(
        f"'{element}' on '{comp}' is spoken by {listed}, so changing its C "
        f"type ({_record.element_ctype(old)} -> "
        f"{_record.element_ctype(new)}) changes their C prototypes, and jm "
        "never rewrites your definitions of them: re-declared under them, "
        "the header and your _core.c would disagree, and the tree would "
        "not build.",
        width=72,
        break_on_hyphens=False,
    )
    mod = C.component_module(cfg, comp)
    stem = CSYM.stem(cfg, comp)
    # gh-2066: a view's own method is removed and re-added with `--view`,
    # as `jm script` replays it, so a view speaker has the same route.
    members = {(str(m["name"]), ""): m for m in C.methods(cfg, comp)} | {
        (str(m["name"]), str(v.get("class_name") or "")): m
        for v in C.views(cfg, comp)
        for m in C.view_methods(v)
    }

    def _view(v: str) -> "list[str]":
        return [_script._flag("--view", v)] if v else []

    lines = [
        f"just-makeit remove method {n} --object {comp}"
        + (f" --view {v}" if v else "")
        + "\n"
        for n, v in who
    ]
    # The C each remove leaves: a view's entry reusing a parent method's
    # name without `fn` binds the parent's (gh-2498), and its remove's note
    # names nothing; any other binds its own.
    syms = [
        f"{C.method_c_symbol(stem, members[n, v])}()"
        for n, v in who
        if not v or members[n, v].get("fn") or (n, "") not in members
    ]
    lines.append(
        f"# then delete {' and '.join(syms)} by hand, as each remove's "
        "note says\n"
    )
    lines.append(
        _script._render_cmd(
            ["just-makeit", "record", comp, element],
            _script._record_decl_flags(new),
        )
    )
    struct = not _record.is_scalar_element(new)
    if struct != (not _record.is_scalar_element(old)):
        kind = "a struct" if struct else "a scalar"
        if struct:
            lines.append(f"# then declare `{element}` in the sacred header\n")
        lines.append(
            f"# and add {' and '.join(labels)} back as {kind} "
            "element is spoken:\n"
        )
        lines.append(f"#   {_record.spoken_as(element, struct)}\n")
    else:
        for n, v in who:
            m = members[n, v]
            lines += _script._method_notes(m)
            lines.append(
                _script._render_cmd(
                    ["just-makeit", "method", comp, n],
                    _script._method_flags(m, mod) + _view(v),
                )
            )
    route = "".join("  " + ln for ln in "".join(lines).splitlines(True))
    return (
        f"{head}\nTo change it, take them out, re-declare, and add them "
        f"back:\n{route.rstrip()}"
    )


def run(
    root: Path,
    object_name: str,
    record_name: str,
    fields: list[dict],
    *,
    doc: str = "",
    elem_type: str = "",
) -> None:
    """Declare element *record_name* on *object_name*.

    Parameters
    ----------
    root : Path
        Project root (the directory holding ``just-makeit.toml``).
    object_name : str
        The component that speaks this element.
    record_name : str
        The element's name. For a struct element that is the C struct's own
        name, as the sacred header declares it; for a scalar element it is a
        jm-level alias, because the element a ring buffer carries is usually
        a plain ``float _Complex`` with no typedef to point at.
    fields : list of dict
        ``{"name", "type"}`` rows, in the order they should be exposed. A
        STRUCT element; mutually exclusive with *elem_type*.
    doc : str, optional
        One line describing what a row is.
    elem_type : str, optional
        A registered scalar C type. A SCALAR element; mutually exclusive
        with *fields* (gh-1404).

    Raises
    ------
    Refusal
        When the re-declaration changes the C type of an element that a
        member already speaks (gh-2055); the message names the members and
        the commands that make the change instead.

    Notes
    -----
    Re-declaring an existing element REPLACES it, so a column added to the
    struct reaches the manifest by running the command again rather than by
    hand-editing.

    gh-2055: the members that speak the element are re-rendered then,
    through `_glue.regenerate`, the re-render `jm property`, `jm warning`
    and `jm error` use. Before, none of them were, and `status --check`
    stayed red until `jm apply`. That covers every re-declaration that
    keeps the C type the element stands for: a struct's columns, or a
    scalar's doc. A re-declaration that CHANGES that type, under members
    that speak it, is refused instead -- see :func:`_retype_refusal`.

    gh-1404 widened this from "a record" to "a named element type", because
    the two are the same declaration: a width family states its element once
    and every member reads it from there, whether that element happens to
    have columns or not. The three rows of doppler's buffer family --
    ``complex64``, ``complex128``, and a two-field ``int16`` record -- are
    then one spelling rather than two.
    """
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\n"
            "Run 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)
    cfg = C.load(root)
    if object_name not in C.components(cfg):
        print(
            f"error: unknown object '{object_name}'.",
            file=sys.stderr,
        )
        sys.exit(1)
    # gh-2116: a record is read only by the members that speak it, and on
    # an object in a `no_generate` module those are the author's.
    from . import _regenerate

    _regenerate.refuse_hand_written_member(
        cfg,
        C.component_module(cfg, object_name),
        f"declare record '{record_name}' on '{object_name}'",
    )
    # gh-1404: the two kinds are declared by different keys, so asking for
    # both is asking for two elements under one name -- and asking for
    # neither describes nothing at all.
    if fields and elem_type:
        print(
            "error: --field and --type are the two KINDS of element.\n"
            "--field declares a struct's columns; --type declares a scalar.\n"
            "Pick one.",
            file=sys.stderr,
        )
        sys.exit(1)
    if elem_type and elem_type not in T._CTYPE_META:
        print(
            f"error: --type '{elem_type}' is not a scalar jm can map to "
            "numpy.\n"
            f"Supported: {', '.join(sorted(T._CTYPE_META))}\n"
            "To declare a struct's columns instead, use --field name:type.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not fields and not elem_type:
        print(
            "error: an element needs --type <scalar> or at least one "
            "--field name:type.\n"
            "An empty element describes no bytes, so nothing could be "
            "generated from it.",
            file=sys.stderr,
        )
        sys.exit(1)

    # A field named twice is refused by `C.save` below, through the one
    # duplicate check every manifest write asks (gh-2805), before anything
    # is written.
    entry: dict = (
        {"name": record_name, "type": elem_type}
        if elem_type
        else {"name": record_name, "fields": list(fields)}
    )
    if doc:
        entry["doc"] = doc

    section = cfg.setdefault(object_name, {})
    rows = list(section.get("records", []))
    # gh-2055: who already speaks this element decides what a re-declaration
    # may do. Nothing has been written yet, so a refusal leaves the tree as
    # it was, byte for byte.
    old = _record.declared(rows, record_name)
    who = speakers(cfg, object_name, record_name) if old else []
    if who and _record.element_ctype(old) != _record.element_ctype(entry):
        raise _report.Refusal(
            _retype_refusal(cfg, object_name, old, entry, who)
        )
    # gh-2068: a member already reading this name as a STRUCT -- through
    # `record_dtype`, which spells `<name> *out` in its prototype -- cannot
    # read it once it is a scalar alias no C type carries. `jm method` and
    # `apply` ask the same predicate for the same pair from the other side.
    readers = list(C.methods(cfg, object_name))
    for v in C.views(cfg, object_name):
        readers += C.view_methods(v)
    for m in readers:
        why = _record.record_dtype_why_not(
            f"method '{object_name}.{m.get('name', '?')}'",
            str(m.get("record_dtype") or ""),
            [entry],
        )
        if why:
            raise _report.Refusal(why)
    # gh-2889: a struct's declaration is in scope for EVERY component's
    # members (`C.record_scope`), so a member anywhere that still states
    # these columns itself would be a second copy -- refused, naming each,
    # as `jm method` refuses it from the other side (`restated_columns`).
    # And every component whose members return the struct is re-rendered
    # below, not only this one.
    elsewhere: "list[str]" = []
    fn_modules: "list[str]" = []
    if not elem_type:
        restating = [
            f"'{comp}.{m.get('name', '?')}'"
            for comp in C.components(cfg)
            for m in list(C.methods(cfg, comp))
            + [n for v in C.views(cfg, comp) for n in C.view_methods(v)]
            if _record.restated_columns(m, [entry])
        ]
        if restating:
            raise _report.Refusal(
                f"{', '.join(restating)} state{'s' if len(restating) == 1 else ''}"
                f" the columns of '{record_name}' in their own result_fields,"
                " which this declaration replaces: drop those result_fields"
                " from the manifest and declare the record in the same"
                " edit, then run `jm apply` (gh-2889)."
            )
        elsewhere = [
            comp
            for comp in C.components(cfg)
            if comp != object_name and speakers(cfg, comp, record_name)
        ]
        # A module function returning the struct binds in its module's
        # `_ext.c`, which no component above need own: a functions-only
        # module, or one whose objects never name the struct.
        fn_modules = [
            mod
            for mod in C.modules(cfg)
            if any(
                _record.c_struct(f) == record_name
                for f in C.module_functions(cfg, mod)
            )
        ]
    for i, existing in enumerate(rows):
        if str(existing.get("name") or "") == record_name:
            rows[i] = entry
            break
    else:
        rows.append(entry)
    section["records"] = rows
    C.save(root, cfg)

    print(f"just-makeit: element '{record_name}' on '{object_name}'")
    cols = ", ".join(f"{f['name']}:{f['type']}" for f in fields)
    print(f"  type    {elem_type}" if elem_type else f"  fields  {cols}")
    print()
    # gh-2889: every other component returning the struct reads these
    # columns too, so its glue moves with this one's.
    redone: "set[str]" = set()
    for comp in elsewhere:
        mod = C.component_module(cfg, comp)
        if mod in redone:
            continue
        _glue.regenerate(root, cfg, comp, mod, C.project_name(cfg))
        if mod:
            redone.add(mod)
    for mod in fn_modules:
        if mod not in redone and not (
            who and mod == C.component_module(cfg, object_name)
        ):
            _object._regenerate_module(root, cfg, mod, C.project_name(cfg))
            redone.add(mod)
    if who:
        # gh-2055: the same C type, so every member keeps its prototype and
        # only jm's glue moves -- the binding, the stub, the contract.
        _glue.regenerate(
            root,
            cfg,
            object_name,
            C.component_module(cfg, object_name),
            C.project_name(cfg),
        )
        print()
        done = (
            f"Done!  Re-rendered {', '.join(n for n, _v in who)}, which "
            "speak it."
        )
        if not elem_type:
            done += (
                f"\n       Give `{record_name}` in the sacred header the "
                "same columns: the binding\n       reads each one's offset "
                "from the struct, so a column it lacks will not build."
            )
        print(done)
        return
    if elem_type:
        print(
            f"Done!  Reference `{record_name}` from every member that speaks "
            f"it:\n"
            f"       {_record.spoken_as(record_name, False)}.\n"
            "       Both read the width from here, so they cannot disagree."
        )
        return
    print(
        f"Done!  Declare `{record_name}` in the sacred header, then reference "
        f"it with\n"
        f"       {_record.spoken_as(record_name, True)}."
    )
