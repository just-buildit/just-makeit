"""_retain.py -- a capsule method param whose pointer the C side KEEPS.

gh-2327. A method param declared with ``capsule = "<name>"`` is BORROWED for
the call (gh-432): the binding unwraps the pointer, drops its reference to the
capsule, and calls the kernel. That is right for a kernel that uses the
pointer only while it runs. It is wrong for one that STORES it -- doppler's
``set_telemetry`` keeps the ``dp_tlm_t *`` and writes through it on every
later ``push`` -- because nothing then keeps the Python object that owns the
pointer alive::

    pool.set_telemetry(Telemetry(1 << 20), "p")   # Telemetry freed here
    pool.push(x)                                  # writes into freed memory

``retain = true`` on the param says the kernel keeps it. The binding then
holds a strong reference to what the caller passed, in one slot per retained
param on the object (:func:`field`):

- a successful call REPLACES the slot with the object just passed;
- passing ``None`` -- the detach idiom the capsule param already has --
  CLEARS it;
- a REFUSED call leaves it as it was, because a kernel that refuses has not
  taken the new pointer and may still be using the old one;
- dealloc drops it, AFTER the C state is destroyed, so the kernel never
  outlives the pointer it holds.

What is retained is the object the caller passed: the owning wrapper (a
``Telemetry``), which keeps its pointer alive for as long as it lives, or
its bare capsule (``tlm._capsule``), which since gh-2328 holds the object
that lent it (``_coerce.CAPSULE_LEND_C``).

Why a key and not a rule
------------------------
The issue offered either. A rule ("a ``void`` or status-returning method
keeps its capsule") would have changed every existing capsule method at once
and guessed at the ones that only use the pointer during the call. A key
says what the author knows about their kernel, and borrowing stays the
default for the call-scoped uses.

Where it is honoured
--------------------
On every object (and view) method whose binding jm writes. The swap
(:func:`swap_c`) goes at the one place each shape knows the kernel has
accepted the call: after the call, and after its declared refusal when it
has one -- a ``status_return`` / ``error_negative`` code, a
``variable_output`` count's ``error_sentinel`` / signed refusal or its
``error_on_empty``, a ``borrow``'s NULL. A failure jm raises after that
point (an allocation, an overrun guard) is not the kernel refusing, and the
kernel may already hold the pointer, so the owner is kept. A ``batch``
method takes no params, so it has nothing to retain.

:func:`errors` refuses the key at load where it would keep nothing alive:

- on a param that is not a capsule -- there is no owner to keep;
- on a ``varargs`` / ``manual_stub`` method, whose binding is the author's
  own C, which jm does not write;
- on a method a composer source delegates (``_composer._delegation_refusal``):
  the source rebuilds the generator the pointer was lent to.
"""

from __future__ import annotations

#: The param key.
KEY = "retain"

#: Every owner slot's name starts with this. `_docsync` reads a hand-owned
#: fragment for it, so a wrapper rendered before the param was retained is
#: named rather than passed over.
FIELD_PREFIX = "_jm_retain_"


def obj_var(param: str) -> str:
    """The ``PyObject *`` local a capsule param is parsed into.

    The capsule parse (``_context/_parse._build_params_parse``) declares it
    and the swap (:func:`swap_c`) reads it, so both ask this one function.

    >>> obj_var("tlm")
    'tlm_obj'
    """
    return f"{param}_obj"


def is_retained(p: dict) -> bool:
    """Whether method param *p* is a capsule the kernel keeps.

    >>> is_retained({"name": "t", "capsule": "x.t", "retain": True})
    True
    >>> is_retained({"name": "t", "capsule": "x.t"})
    False
    """
    return bool(p.get("capsule")) and p.get(KEY) is True


def retained(m: dict) -> "list[dict]":
    """The retained params of method *m*, in declaration order.

    >>> m = {"name": "set_t", "params": [
    ...     {"name": "t", "type": "t_t *", "capsule": "x.t", "retain": True},
    ...     {"name": "prefix", "type": "const char *"}]}
    >>> [p["name"] for p in retained(m)]
    ['t']
    """
    params = m.get("extra_args") or m.get("params") or []
    return [p for p in params if isinstance(p, dict) and is_retained(p)]


def field(method: str, param: str) -> str:
    """The object struct member holding *param*'s owner for *method*.

    Keyed by method AND param: two methods may each retain a param of one
    name, and each keeps what it was passed.

    >>> field("set_telemetry", "tlm")
    '_jm_retain_set_telemetry_tlm'
    """
    return f"{FIELD_PREFIX}{method}_{param}"


def fields_c(m: dict) -> str:
    """The struct members method *m* needs, one line each, or ``""``."""
    return "".join(
        f"    PyObject *{field(m['name'], p['name'])};"
        f"  /* keeps {p['name']}'s owner alive */\n"
        for p in retained(m)
    )


def free_c(m: dict) -> str:
    """The dealloc lines releasing what *m* retains, or ``""``.

    Emitted into the ``extra_buf_free`` slot, which the type's dealloc runs
    after it has destroyed the C state -- the kernel goes first, then the
    pointers it held.
    """
    return "".join(
        f"    Py_XDECREF(self->{field(m['name'], p['name'])});\n"
        for p in retained(m)
    )


def swap_c(m: dict, indent: str = "    ") -> str:
    """The C that keeps what a successful call of *m* was passed, or ``""``.

    Placed where the call is known to have succeeded: after it, and after
    its refusal check when the binding has one. The new owner is
    referenced before the old one is released, so passing the object
    already held is safe.

    >>> m = {"name": "attach", "params": [
    ...     {"name": "t", "type": "t_t *", "capsule": "x.t", "retain": True}]}
    >>> print(swap_c(m))
        /* retain: the kernel keeps `t`, so this object keeps its owner
           until the next call replaces it or None detaches it. */
        if (t_obj != Py_None)
            Py_INCREF(t_obj);
        Py_XSETREF(self->_jm_retain_attach_t,
                   t_obj != Py_None ? t_obj : NULL);
    <BLANKLINE>

    A param that also declares ``required = true`` never reaches the swap
    as ``None`` -- the binding refused it up front -- so it is kept
    unconditionally, with no detach branch to read as reachable:

    >>> m["params"][0]["required"] = True
    >>> print(swap_c(m))
        /* retain: the kernel keeps `t`, so this object keeps its owner
           until the next call replaces it. */
        Py_INCREF(t_obj);
        Py_XSETREF(self->_jm_retain_attach_t, t_obj);
    <BLANKLINE>
    """
    # Imported here: `_context._parse` imports this module at load.
    from ._context._parse import capsule_nullable

    i = indent
    out = []
    for p in retained(m):
        n = p["name"]
        o = obj_var(n)
        if not capsule_nullable(p):
            out.append(
                f"{i}/* retain: the kernel keeps `{n}`, so this object keeps"
                f" its owner\n"
                f"{i}   until the next call replaces it. */\n"
                f"{i}Py_INCREF({o});\n"
                f"{i}Py_XSETREF(self->{field(m['name'], n)}, {o});\n"
            )
            continue
        out.append(
            f"{i}/* retain: the kernel keeps `{n}`, so this object keeps its"
            f" owner\n"
            f"{i}   until the next call replaces it or None detaches it. */\n"
            f"{i}if ({o} != Py_None)\n"
            f"{i}    Py_INCREF({o});\n"
            f"{i}Py_XSETREF(self->{field(m['name'], n)},\n"
            f"{i}           {o} != Py_None ? {o} : NULL);\n"
        )
    return "".join(out)


def shape_refusal(m: dict) -> str:
    """Why jm cannot keep a retained param of method *m*, or ``""``.

    >>> shape_refusal({"name": "f", "manual_stub": True})
    'its binding is hand-written, so jm writes no call to keep it after'
    >>> shape_refusal({"name": "f", "variable_output": True})
    ''
    """
    if m.get("varargs") or m.get("manual_stub"):
        return (
            "its binding is hand-written, so jm writes no call to keep it "
            "after"
        )
    return ""


def errors(cfg: dict) -> "list[str]":
    """Every ``retain`` jm would not honour, as a message naming its row.

    Asked at load (`_config.load`), over every object's and view's method
    params, so a key that would keep nothing alive stops before anything is
    written.

    Examples
    --------
    >>> cfg = {"o": {"methods": [{"name": "attach", "params": [
    ...     {"name": "n", "type": "int", "retain": True}]}]}}
    >>> print(errors(cfg)[0])
    [[o.methods.params]] attach.n: retain keeps the owner of a capsule param, and n declares no `capsule`; drop the key
    >>> cfg["o"]["methods"][0]["params"][0].update(capsule="x.t", type="t *")
    >>> errors(cfg)
    []
    >>> cfg["o"]["methods"][0]["manual_stub"] = True
    >>> print(errors(cfg)[0])
    [[o.methods.params]] attach.n: retain cannot be kept by jm, because its binding is hand-written, so jm writes no call to keep it after; keep a reference to the owner in that binding
    """
    from ._config import declared_methods

    out: "list[str]" = []
    for table, m in declared_methods(cfg):
        params = m.get("extra_args") or m.get("params") or []
        for p in params:
            if not isinstance(p, dict) or KEY not in p:
                continue
            row = (
                f"[[{table}.params]] {m.get('name', '?')}.{p.get('name', '?')}"
            )
            value = p[KEY]
            if not isinstance(value, bool):
                out.append(f"{row}: retain = {value!r} must be true or false")
            elif not value:
                continue
            elif not p.get("capsule"):
                out.append(
                    f"{row}: retain keeps the owner of a capsule param, and "
                    f"{p.get('name', '?')} declares no `capsule`; drop the key"
                )
            else:
                why = shape_refusal(m)
                if why:
                    out.append(
                        f"{row}: retain cannot be kept by jm, because {why}; "
                        "keep a reference to the owner in that binding"
                    )

    return out
