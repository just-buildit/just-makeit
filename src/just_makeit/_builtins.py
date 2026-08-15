"""The method names an object's *generated* code already occupies.

gh-994. A component's Python surface is written down as ``[[<obj>.methods]]``
entries, and nothing stops one of those entries being named after something jm
emits itself. doppler declares ``reset`` that way in 28 objects. Before this
module existed jm answered that collision by emitting both — two definitions of
one C symbol into a create-only ``_core.c``, two ``PyMethodDef`` rows, two
``.pyi`` entries — so the tree it had just written did not compile.

There are exactly seven shapes of built-in, and the set is *derived* per
component rather than listed as reserved words, because six of the seven are
conditional: ``--no-step`` removes ``step``/``steps``, ``--no-reset`` and
``--no-state`` remove ``reset``, and ``get_``/``set_`` exist only for the
scalar state fields the object actually declares. A hardcoded list would call
``step`` a collision on an object that has no ``step``, and would miss
``get_gain`` entirely.

The consumers ask three different questions of the same derivation, and the
distinctions between them are all load-bearing:

- :func:`builtin_method_names` — *is there a C symbol behind this name?*
  ``_method.already_provides`` asks it before concluding that a definition it
  found in the tree is the built-in's rather than a method stub.
- :func:`absorbable_members` — *is there a Python member behind it?* Not the
  same set. ``<comp>_destroy`` is always a symbol, but ``destroy()`` is only a
  member while the teardown binding still answers to that name.
- :func:`reserved_python_members` — *is the name taken by something a method
  entry cannot be describing?* (gh-996.) ``create``, the context-manager
  protocol, the stream generator, the serializable triplet, a renamed
  teardown, a declared property. Those are refused rather than absorbed: jm
  accepted them and bound something other than what was asked for, which is
  the same silence, one layer up.
"""

from __future__ import annotations

from ._types import _CTYPE_META

__all__ = [
    "absorbable_members",
    "builtin_method_names",
    "builtin_owned_members",
    "is_builtin_symbol",
    "overridden_builtin_slots",
    "reserved_python_members",
]

#: For each built-in that a declared method can genuinely *replace*, the
#: render slots that carry it — the ``_core.c`` body first, then the header
#: declaration, then the Python glue.
#:
#: Only ``reset`` and ``steps`` are here, and the omissions are the point.
#: ``create`` is the constructor ``tp_init`` calls, ``destroy`` is what
#: ``tp_dealloc`` calls, ``step`` is the ``static inline`` the built-in
#: ``steps`` loop calls, and the ``get_``/``set_`` accessors back the getset
#: descriptors — jm's own generated C calls each of those with a fixed
#: signature, so there is no coherent reading of "the method replaced it".
#: Those collisions resolve the other way instead: the built-in keeps the
#: symbol and ``_method.already_provides`` skips the method's stub.
#:
#: ``builtin_reset_*`` is gh-131's original set, which suppressed the glue and
#: the header and left the ``_core.c`` body behind — the fifth slot, and the
#: whole of gh-994.
_OVERRIDE_SLOTS: "dict[str, tuple[str, ...]]" = {
    "reset": (
        "reset_c_open",
        "reset_assignments",
        "reset_c_close",
        "builtin_reset_decl",
        "builtin_reset_c",
        "builtin_reset_pmd",
        "builtin_reset_pyi",
    ),
    "steps": (
        "steps_c_impl",
        "steps_c_decl",
        "steps_ext_fn",
        "steps_def_entry",
        "pyi_steps_method",
    ),
}

#: The ctx slot holding each overridable built-in's own C declaration. Read
#: to decide whether a declared method *replaces* the built-in or merely
#: *names* it — see :func:`overridden_builtin_slots`.
_DECL_SLOT = {"reset": "builtin_reset_decl", "steps": "steps_c_decl"}


def builtin_method_names(
    state_vars: "list[tuple[str, str, str]]",
    *,
    no_state: bool = False,
    no_step: bool = False,
    no_reset: bool = False,
) -> "frozenset[str]":
    """Bare member names an object's generated code already provides.

    Parameters
    ----------
    state_vars
        ``(name, ctype, default)`` triples, exactly as
        :func:`just_makeit._config.state_vars` returns them. Only the scalar
        ones grow accessors — an array field is exposed as a memoryview, not
        as ``get_``/``set_`` — which mirrors ``make_state_ctx``'s own
        ``scalar_vars`` filter.
    no_state, no_step, no_reset
        The object's suppression flags. Each removes the members its own
        generator would otherwise emit.

    Returns
    -------
    frozenset of str
        Bare names, not C symbols: ``{"create", "destroy", "reset", "step",
        "steps", "get_gain", "set_gain"}``. The caller prefixes the component
        when it wants the C symbol, so a method whose ``fn`` overrides the
        symbol is compared against the right thing.

    Examples
    --------
    >>> sorted(builtin_method_names([("gain", "double", "1.0")]))
    ['create', 'destroy', 'get_gain', 'reset', 'set_gain', 'step', 'steps']
    >>> sorted(builtin_method_names([], no_step=True, no_reset=True))
    ['create', 'destroy']

    An array field is state, but not an accessor pair:

    >>> sorted(builtin_method_names([("taps", "float[8]", "0.0f")],
    ...                             no_step=True, no_reset=True))
    ['create', 'destroy']
    """
    names = {"create", "destroy"}
    if not (no_state or no_reset):
        names.add("reset")
    if not no_step:
        names |= {"step", "steps"}
    for name, ctype, _ in state_vars:
        if ctype in _CTYPE_META:
            names |= {f"get_{name}", f"set_{name}"}
    return frozenset(names)


def is_builtin_symbol(component: str, c_fn: str, builtins: "frozenset[str]"):
    """Whether *c_fn* is the C symbol of one of *builtins*.

    The inverse of the ``<component>_<member>`` naming convention every
    generator here follows, and the reason it is a function rather than an
    ``in`` test at the call site: a method that overrides its C symbol with
    ``fn`` is *not* a collision even when its Python name is ``reset``, and
    that only falls out if the comparison happens on the symbol.

    Examples
    --------
    >>> b = builtin_method_names([("gain", "double", "1.0")])
    >>> is_builtin_symbol("osc", "osc_reset", b)
    True
    >>> is_builtin_symbol("osc", "osc_get_gain", b)
    True
    >>> is_builtin_symbol("osc", "osc_tune", b)
    False

    An `fn` override moves the symbol out of the component's namespace
    entirely, so it cannot collide:

    >>> is_builtin_symbol("osc", "legacy_reset", b)
    False
    """
    prefix = f"{component}_"
    if not c_fn.startswith(prefix):
        return False
    return c_fn[len(prefix) :] in builtins


def overridden_builtin_slots(
    component: str, declared_methods: "list[dict] | None", ctx: dict
) -> "list[str]":
    """Render slots to blank because a declared method *replaces* a built-in.

    Called once, while an object is being scaffolded, on the only path that
    knows the object's methods before its create-only ``_core.c`` is written:
    ``jm apply`` replaying a manifest. On the incremental path (``jm object``
    then ``jm method``) the built-in body is already on disk in a file jm must
    not rewrite, and the collision resolves the other way — see
    :func:`just_makeit._method.already_provides`.

    Naming a built-in is not the same as replacing it. doppler declares
    ``reset`` in 28 objects purely so the member appears in the manifest's
    description of the Python surface; the entry adds no parameters and no
    output shape, so the prototype it implies is *byte-identical* to the
    built-in's and the built-in's body — which actually restores the declared
    defaults — is the better scaffold. A ``reset(start)``, or a ``steps``
    turned into a variable-output method, implies a different prototype and
    genuinely does replace it.

    That question is settled by comparing prototypes rather than by a rule of
    thumb about params, because the built-in declaration in *ctx* is the very
    text being replaced: there is nothing for the comparison to drift from.

    Returns
    -------
    list of str
        Slot names to set to ``""``. Empty for every project that declares no
        colliding method, which is nearly all of them.
    """
    from ._init import _normalize_decl
    from ._method import _build_method_prototype

    out: list[str] = []
    for m in declared_methods or []:
        slots = _OVERRIDE_SLOTS.get(m.get("name", ""))
        if slots is None or m.get("fn"):
            # An `fn` override moves the symbol elsewhere: no collision, and
            # nothing to suppress.
            continue
        proto = _build_method_prototype(
            component,
            m["name"],
            m.get("arg_type", "void"),
            m.get("return_type", "void"),
            bool(m.get("variable_output")),
            m.get("multi_output") or [],
            [(p["name"], p["type"]) for p in (m.get("params") or [])],
            m.get("out_type"),
            pass_capacity=bool(m.get("pass_capacity")),
            batch=bool(m.get("batch")),
            result_fields=m.get("result_fields") or [],
            single=bool(m.get("single")),
            record_dtype=m.get("record_dtype", ""),
        )
        builtin = ctx.get(_DECL_SLOT[m["name"]], "")
        # The built-in's slot may carry a Doxygen block above the declaration;
        # only the declaration itself is being compared.
        builtin = builtin.strip().splitlines()[-1] if builtin.strip() else ""
        if _normalize_decl(proto) == _normalize_decl(builtin):
            continue
        out.extend(slots)
    return out


#: For each overridable built-in, the ctx slots whose rendered text *is* its
#: body in ``_core.c``, in file order. Concatenated, they are the exact
#: substring :func:`withdraw_overridden_builtin` looks for.
_BODY_SLOTS = {
    "reset": ("reset_c_open", "reset_assignments", "reset_c_close"),
    "steps": ("steps_c_impl",),
}


def withdraw_overridden_builtin(
    root, cfg, pkg: str, component: str, method: dict
) -> "tuple[bool, str]":
    """Retract a built-in's body so a declared method can take its symbol.

    gh-994, the incremental half. Scaffolding from a manifest, jm knows about
    the method before it writes ``_core.c`` and simply does not emit the
    built-in (:func:`overridden_builtin_slots`). Running ``jm method`` against
    an object that already exists, the built-in's body is already in a
    create-only file — and jm is nevertheless about to *replace its
    declaration* in the sacred header, because a `steps` turned into a
    variable-output method is doppler's universal idiom (``Despreader.steps(x)``)
    and the manifest is authoritative about the signature. Leaving the body
    behind under a declaration that no longer describes it is not a
    conservative choice; it is the duplicate-symbol bug this issue is about,
    one file over.

    So the body is withdrawn — but only while it is still jm's own untouched
    scaffold, compared against the very text a fresh render produces. Once the
    author has written a real implementation there, the comparison fails and
    this returns a warning instead: jm keeps the built-in, skips the method's
    stub, and says which two lines the author must reconcile. Nothing
    hand-written is ever deleted.

    Returns
    -------
    (withdrawn, warning)
        ``(True, "")`` — the body is gone and the method now owns the symbol.
        ``(False, "")`` — the method merely *names* the built-in (identical
        prototype); there is nothing to withdraw and nothing wrong.
        ``(False, msg)`` — an override jm could not make room for.
    """
    from . import _glue

    name = method.get("name", "")
    slots = _BODY_SLOTS.get(name)
    if slots is None or method.get("fn"):
        return False, ""
    ctx = _glue.component_ctx(cfg, component, pkg, root)
    if not overridden_builtin_slots(component, [method], ctx):
        return False, ""  # names the built-in rather than replacing it

    core_c = root / "native" / "src" / component / f"{component}_core.c"
    if not core_c.is_file():
        return False, ""
    text = core_c.read_text(encoding="utf-8")
    body = "".join(ctx.get(s, "") for s in slots)
    if not body or body not in text:
        return False, (
            f"{core_c.relative_to(root)} still defines {component}_{name}(),"
            f" and its body is no longer jm's scaffold — so it was left"
            f" alone.\n"
            f"  The declared method '{name}' has a different signature, and"
            f" jm has skipped its stub rather than write a second definition."
            f"\n  Move the body into the new signature by hand, or run"
            f" `jm regenerate {component}` to rebuild this component's C from"
            f" the manifest."
        )
    core_c.write_text(text.replace(body, "", 1), encoding="utf-8")
    print(f"  update  {core_c}  (withdrew the built-in {name}())")
    return True, ""


def absorbable_members(cfg, component: str) -> "frozenset[str]":
    """The built-in names a declared method entry may legitimately claim.

    gh-994's six, but stated as Python **members** rather than as C symbols,
    and the difference is not academic. ``<comp>_destroy`` is always a C
    symbol; ``destroy()`` is only a member while the teardown binding still
    answers to that name. Rename it with ``[<obj>.destroy] name = "close"``
    and an entry called ``destroy`` collides with nothing — it is an ordinary
    method — while ``close`` becomes the reserved one.

    Conflating the two bound nothing at all: the entry was treated as
    describing a built-in that no longer existed, so its glue was suppressed
    and no ``PyMethodDef`` row took its place. The same silent-nothing
    ``create`` produces, reached from the other side.

    ``create`` is absent for that reason: it is a C symbol with no member.
    """
    from . import _config as C
    from ._context._destroy import destroy_py_names

    names = set(
        builtin_method_names(
            C.state_vars(cfg, component),
            no_state=C.is_no_state(cfg, component),
            no_step=C.is_no_step(cfg, component),
            no_reset=C.is_no_reset(cfg, component),
        )
    )
    names.discard("create")
    if "destroy" not in destroy_py_names(C.destroy_spec(cfg, component)):
        names.discard("destroy")
    return frozenset(names)


def builtin_owned_members(root, cfg, component: str) -> "frozenset[str]":
    """Declared methods whose C symbol a built-in kept (gh-994).

    The glue counterpart of :func:`overridden_builtin_slots`, and the reason
    it reads the tree rather than the manifest: which of the two owns the
    symbol is a fact about what was *written*, and the two paths settle it
    differently. Scaffolding from a manifest, the built-in is suppressed
    before ``_core.c`` is written and the method owns it. Adding a method to
    an existing object, ``_core.c`` is create-only and already holds the
    built-in's body, so the built-in keeps it and the method's stub is
    skipped — at which point the method must not bind a second Python member
    either, or ``_ext.c`` carries two definitions of one wrapper.

    Returns the bare member names, ready for ``make_methods_ctx``'s
    ``builtin_members``. Empty — the overwhelmingly common case — costs one
    manifest lookup and no file read.
    """
    from . import _config as C
    from ._method import already_provides

    declared = C.methods(cfg, component)
    if not declared:
        return frozenset()
    builtins = builtin_method_names(
        C.state_vars(cfg, component),
        no_state=C.is_no_state(cfg, component),
        no_step=C.is_no_step(cfg, component),
        no_reset=C.is_no_reset(cfg, component),
    )
    # Members, not symbols: a built-in that owns `<comp>_destroy` but binds
    # it under another Python name provides no `destroy()` for the entry to
    # describe, so suppressing the entry's glue there would leave the member
    # unbound entirely.
    members = absorbable_members(cfg, component)
    owned = set()
    for m in declared:
        name = m.get("name", "")
        if name not in builtins or name not in members:
            continue
        c_fn = m.get("fn", "") or f"{component}_{name}"
        if already_provides(root, component, c_fn, builtins):
            owned.add(name)
    return frozenset(owned)


def reserved_python_members(cfg, component: str) -> "dict[str, str]":
    """Names on the generated class a method entry can only *collide* with.

    gh-996, the other half of gh-994. That issue settled the collisions where
    a declared method and a built-in are plausibly the *same* member — the
    entry describes what jm already emits (doppler's ``reset``), or replaces
    it outright (a ``reset(start)``). This is the set where no such reading
    exists, and where jm therefore has nothing to do but say so.

    Measured on 0.60.2, one method entry per name, on a fresh object:

    ===================  ==========================================
    ``create``           accepted; **0** PyMethodDef rows, **0**
                         ``.pyi`` entries. There is no ``create()``
                         member to absorb into — the constructor's
                         Python face is ``__init__`` — so the entry
                         bound nothing at all.
    ``__enter__``        accepted; **2** rows. The second shadows
                         jm's, so ``with obj:`` silently stopped
                         returning the object.
    ``__exit__``         accepted; **2** rows. Same shape: the
                         context manager stopped tearing down.
    ``stream``           accepted; **2** rows on a ``--streamable``
                         object, shadowing gh-201's generator.
    ``state_bytes``      accepted; **2** rows and ``_ext.c` did not
    ``get_state``        compile — ``redefinition of
    ``set_state``        Obj_state_bytes`` and friends (gh-400).
    a renamed teardown   accepted; **2** rows, so the binding
                         ``[<obj>.destroy] name``/``aliases``
                         reached was whichever came second.
    a declared property  accepted; **3** ``.pyi`` entries for one
                         name, the descriptor and the method.
    ===================  ==========================================

    Every one is a mistake with no other reading, so every one is refused
    rather than absorbed: silently binding something other than what the
    manifest asked for is the failure mode this whole issue is about.

    The six names gh-994 *does* absorb are deliberately absent — ``reset``,
    ``destroy``, ``step``, ``steps`` and the ``get_``/``set_`` accessors are a
    supported pattern, and doppler writes ``reset`` that way in 28 objects.
    Note the interaction with a renamed teardown: ``[<obj>.destroy] name =
    "close"`` moves the member, so ``close`` becomes reserved and ``destroy``
    stops being a member at all.

    Returns
    -------
    dict
        ``{member name: (what holds it, a sentence of advice)}``, ready
        to drop into an error message. Derived per component, never a word list:
        five of the eight sources are conditional.

        The remedy is carried per source because there is no general one.
        ``--fn`` is the obvious-looking answer and it is **wrong**: it renames
        the C symbol, while every collision here is on the *Python* name, so
        the generated ``PyMethodDef`` table still ends up with two rows under
        one key. Measured, after this message first said otherwise.
    """
    from . import _config as C
    from ._context._destroy import destroy_py_names

    taken: dict[str, tuple[str, str]] = {
        # `tp_init` calls `<comp>_create`; the Python face of it is __init__.
        "create": (
            "the constructor",
            "Its Python face is __init__, and `jm view` is how an object"
            " gets a second one.",
        ),
        "__enter__": (
            "the context-manager protocol",
            "jm implements __enter__/__exit__ itself.",
        ),
    }
    taken["__exit__"] = taken["__enter__"]
    for name in destroy_py_names(C.destroy_spec(cfg, component)):
        taken[name] = (
            "the teardown binding",
            f"Or move it, with [{component}.destroy] name/aliases.",
        )
    if C.is_streamable(cfg, component):
        _stream = (
            "the stream generator",
            "Or drop --streamable and write the generator yourself.",
        )
        taken["stream"] = _stream
        taken["__iter__"] = _stream
        if C.is_async_stream(cfg, component):
            taken["__aiter__"] = _stream
    if C.is_serializable(cfg, component):
        for name in ("state_bytes", "get_state", "set_state"):
            taken[name] = (
                "the serializable state-blob binding",
                "Or drop --serializable and write the binding yourself.",
            )
    for prop in C.properties(cfg, component):
        taken[prop["name"]] = (
            "a declared property",
            "Or rename the property.",
        )

    # The absorbable six come out last, so a source above can never
    # accidentally reserve one of them — the default teardown name is
    # `destroy`, which is exactly that case and would otherwise refuse the
    # pattern gh-131 made supported. One derivation, shared with
    # `builtin_owned_members`, so the name a method may claim and the name
    # whose glue is then suppressed cannot come to disagree.
    for name in absorbable_members(cfg, component):
        taken.pop(name, None)
    return taken
