"""_destroy — context builder for the ``[<comp>.destroy]`` table.

Two gaps, one table (gh-541 + gh-544).

**gh-541 (data integrity).** An object's destructor was hardwired ``void``, so
a teardown that is part of the *work* — a writer patching a header field and
appending a trailing metadata block after the last sample — had no channel to
report failure. Worse, the generated ``__exit__`` ended in an unconditional
``Py_RETURN_NONE``, so the idiomatic ::

    with Writer(path) as w:
        w.write(x)

silently produced a corrupt file when the close failed. The workaround was to
hand-write ``close()`` and ``__exit__`` into the sacred binding fragment, which
fails *silently* if a regeneration ever drops it — the exact failure mode jm's
declarative slots exist to remove.

**gh-544 (naming).** The Python method was hardcoded ``destroy()``. A type
whose established public API is ``close()`` — anything constructed on a path,
where users expect the file-like vocabulary — had to hand-write ~20 lines per
type, each a place to get the ``self->handle = NULL`` wrong and double free.

The two overlap in implementation but not in need, so they are declared
together and rendered together::

    [wfm_writer.destroy]
    name          = "close"
    aliases       = ["destroy"]
    returns       = "int"
    error         = "OSError"
    error_message = "failed to finalise the capture"

Propagation matrix — the whole point of gh-541:

===================  =========================================================
Path                 On non-zero rc
===================  =========================================================
``close()``          set the exception, ``return NULL``
``__exit__``         set the exception, ``return NULL`` (the ``with`` raises)
``tp_dealloc``       **swallow** — a deallocator has no exception context
===================  =========================================================

The ``tp_dealloc`` swallow is deliberate and is not a gap: CPython calls
``tp_dealloc`` during refcount collapse, where there is no caller to raise to
and an in-flight exception must not be clobbered. Discarding the status there
is the only correct choice; the generated C says so in a comment.

Relationship to `_diagnostics.make_errors_ctx`: that builder is this one's
constructor-side sibling (gh-482/gh-514), translating a ``create()`` NULL into
a declared exception. This one translates a non-zero destructor status the same
way and shares its `_c_string_literal` message wrapper rather than growing a
peer copy of it.

Unlike ``create_error``, this table *does* change the sacred core signature:
``returns = "int"`` makes it ``int <comp>_destroy(<comp>_state_t *state)`` in
both ``_core.h`` and ``_core.c``. There is no way around that — a status has to
come from somewhere — so the slots cover the sacred templates too, and
``jm apply`` patches an already-scaffolded project's pair in place.
"""

from __future__ import annotations

import re

from .. import _config as C
from .._docstring import DESC_WIDTH, _wrap
from .._gluedoc import glue_methods
from ._diagnostics import _c_string_literal
from ._parse import _build_ml_doc

# A method name is interpolated straight into a C string literal *and* into
# Python stub source, so it is held to the Python identifier grammar.
_PY_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Message used when `returns = "int"` is declared without an `error_message`.
_DEFAULT_MSG = "{component}_destroy reported failure"

# Category used when `returns = "int"` is declared without an `error`. Matches
# the undeclared-create_error spirit: a generic runtime failure, not a guess at
# something more specific.
_DEFAULT_CATEGORY = "RuntimeError"


def validate_destroy_spec(
    component: str, spec: dict, methods: list[dict] | None = None
) -> None:
    """Reject an ill-formed ``[<comp>.destroy]`` table at generation time.

    Every check here exists because the alternative is a *silent* wrong
    result: a mistyped exception name reaches the user's compiler as an
    undeclared ``PyExc_Foo`` identifier rather than a jm diagnostic, and an
    ``error`` declared without ``returns = "int"`` is simply inert — this
    repository has shipped four separate bugs of exactly that second shape, so
    inert keys are refused rather than ignored.

    Parameters
    ----------
    component : str
        Component id, named in every message so a multi-object manifest points
        at the offending section.
    spec : dict
        The raw table. Empty/None is always valid (the undeclared default).
    methods : list of dict, optional
        The component's declared methods, so ``exit`` can be checked against
        them. ``None`` means "the caller has no method list here" and skips
        only that one check — every other check still runs.

    Raises
    ------
    ValueError
        On an unknown key, a bad ``returns``, a non-identifier ``name`` or
        alias, an ``error`` outside `_config.ERROR_CATEGORIES`, an
        ``error``/``error_message`` paired with a non-``int`` ``returns``, or
        an ``exit`` that is not a declared method distinct from the teardown.

    Examples
    --------
    >>> validate_destroy_spec("w", {})
    >>> validate_destroy_spec("w", {"returns": "int", "error": "OSError"})
    >>> try:
    ...     validate_destroy_spec("w", {"error": "OSError"})
    ... except ValueError as e:
    ...     print(str(e)[:43])
    object 'w': [w.destroy] error/error_message
    >>> validate_destroy_spec("c", {"exit": "close"}, [{"name": "close"}])
    >>> try:
    ...     validate_destroy_spec("c", {"exit": "flush"}, [{"name": "close"}])
    ... except ValueError as e:
    ...     print(str(e)[:52])
    object 'c': [c.destroy] exit 'flush' is not a declar
    >>> try:
    ...     validate_destroy_spec("c", {"exit": "destroy"})
    ... except ValueError as e:
    ...     print(str(e)[:50])
    object 'c': [c.destroy] exit 'destroy' names the t
    """
    from .. import _config as C

    if not spec:
        return

    known = {"name", "aliases", "returns", "error", "error_message", "exit"}
    unknown = sorted(set(spec) - known)
    if unknown:
        raise ValueError(
            f"object '{component}': [{component}.destroy] has unknown "
            f"key(s) {', '.join(unknown)}. "
            f"Supported: {', '.join(sorted(known))}."
        )

    returns = spec.get("returns", "") or ""
    if returns not in C.DESTROY_RETURNS:
        raise ValueError(
            f"object '{component}': [{component}.destroy] returns "
            f"'{returns}' is not supported. Use \"int\" (non-zero raises) "
            f'or omit it for the default "void".'
        )

    name = spec.get("name") or "destroy"
    if not _PY_IDENT.match(name):
        raise ValueError(
            f"object '{component}': [{component}.destroy] name '{name}' "
            f"is not a valid Python identifier."
        )
    for alias in spec.get("aliases", []):
        if not _PY_IDENT.match(alias):
            raise ValueError(
                f"object '{component}': [{component}.destroy] alias "
                f"'{alias}' is not a valid Python identifier."
            )

    # gh-514 did exactly this for a handle's create_error; same list, same
    # reason — PyExc_<name> is emitted verbatim into C.
    category = spec.get("error", "")
    if category and category not in C.ERROR_CATEGORIES:
        supported = ", ".join(sorted(C.ERROR_CATEGORIES))
        raise ValueError(
            f"object '{component}': [{component}.destroy] error "
            f"'{category}' is not a supported exception name. "
            f"Supported: {supported}."
        )

    if returns != "int" and (category or spec.get("error_message")):
        raise ValueError(
            f"object '{component}': [{component}.destroy] "
            f'error/error_message require returns = "int" — without a '
            f"status to test they would never be raised."
        )

    # gh-805 §H: `exit` names a DECLARED method, not a C symbol. Checked here
    # rather than left to the compiler because the failure it prevents is not
    # a link error: a typo'd name that happens to match nothing would fall
    # back to the destroy body and produce a binding whose __exit__ frees
    # while both its doc faces say it finalizes — the silent-wrong shape.
    exit_name = spec.get("exit") or ""
    if exit_name and not _PY_IDENT.match(exit_name):
        raise ValueError(
            f"object '{component}': [{component}.destroy] exit "
            f"'{exit_name}' is not a valid Python identifier."
        )
    if exit_name and exit_name in destroy_py_names(spec):
        raise ValueError(
            f"object '{component}': [{component}.destroy] exit "
            f"'{exit_name}' names the teardown itself, which is what "
            f"__exit__ already calls. Point it at a separate finalizing "
            f"method — the key exists to split finalize from free."
        )
    if exit_name and methods is not None:
        declared = [m.get("name", "") for m in methods]
        if exit_name not in declared:
            known_names = ", ".join(sorted(n for n in declared if n)) or "none"
            raise ValueError(
                f"object '{component}': [{component}.destroy] exit "
                f"'{exit_name}' is not a declared method. "
                f"Declared: {known_names}."
            )


def destroy_py_names(spec: dict) -> list[str]:
    """Python names bound to the teardown function, in emission order.

    ``name`` first, then each alias, de-duplicated: a PyMethodDef table with a
    repeated key is a real bug (the second entry is unreachable), and
    ``aliases = ["destroy"]`` alongside the default ``name`` is the natural
    way to write "keep the standard name too".

    Examples
    --------
    >>> destroy_py_names({})
    ['destroy']
    >>> destroy_py_names({"name": "close", "aliases": ["destroy", "close"]})
    ['close', 'destroy']
    """
    out = [spec.get("name") or "destroy"]
    for alias in spec.get("aliases", []):
        if alias not in out:
            out.append(alias)
    return out


def _inherited_error(spec: dict, exit_method: dict) -> tuple[str, str]:
    """``(category, message)`` for the teardown, inherited when unstated.

    gh-805 §H review. Once ``exit`` splits the two calls apart they describe
    **one condition** reached by two routes — the finalizer's verdict is
    latched, and the destructor reports the same hole on the GC path. Left to
    the ordinary defaults, the minimal adoption (``exit`` alone) rendered a
    *different exception class and a different sentence* for that one
    condition: ``ValueError: the capture has a hole`` from ``__exit__`` and
    ``RuntimeError: <comp>_destroy reported failure`` from collection. Not a
    drift risk — the out-of-the-box result.

    So an unstated pair is inherited from the named finalizer. Both keys or
    neither: a half-inherited pair would pin someone else's message under the
    author's own category, which is a third message rather than one fewer.
    Declaring either key keeps both explicit, so saying something different
    on purpose stays possible.

    doppler maintains this identity **by hand today** — the same
    180-character ``error_message`` copied verbatim into the ``close`` method
    and the ``destroy`` table — which is the duplication this key removes
    everywhere else.
    """
    # gh-864: INDEPENDENTLY, not both-or-neither. This shipped as a pair
    # because a half-inherited pair "pins one declaration's message under the
    # other's category" — but that reasoning was wrong twice over. The message
    # describes the CONDITION ("the capture has a hole"), not the exception
    # class, so it stays accurate under a different class. And pairing left no
    # setting where all three surfaces were right: declaring `error` to fix
    # the stub silently dropped the message, which is the entire content of
    # the diagnostic gh-541 exists to preserve.
    return (
        spec.get("error") or exit_method.get("error") or _DEFAULT_CATEGORY,
        spec.get("error_message") or exit_method.get("error_message") or "",
    )


def _teardown_body(
    component: str, spec: dict, exit_method: dict | None = None
) -> str:
    """The shared ``close()``/``__exit__`` body.

    One string for both paths on purpose: the explicit method and the context
    manager must agree about whether a failed teardown raises, and gh-541 is
    precisely the bug where they did not.

    When ``exit`` redirects ``__exit__`` (gh-805 §H) the two are no longer one
    string — but they are still one *condition*, so an unstated
    ``error``/``error_message`` is inherited from the finalizer rather than
    falling back to the generic default. See `_inherited_error`.
    """
    if spec.get("returns") != "int":
        return (
            "    if (self->handle) {\n"
            f"        {component}_destroy(self->handle);\n"
            "        self->handle = NULL;\n"
            "    }\n"
            "    Py_RETURN_NONE;\n"
        )
    if exit_method:
        category, _inherited_msg = _inherited_error(spec, exit_method)
        message = _inherited_msg or _DEFAULT_MSG.format(component=component)
        return _teardown_int_body(component, category, message)
    category = spec.get("error") or _DEFAULT_CATEGORY
    message = spec.get("error_message") or _DEFAULT_MSG.format(
        component=component
    )
    return _teardown_int_body(component, category, message)


def _teardown_int_body(component: str, category: str, message: str) -> str:
    """The fallible teardown block, once — inherited and declared alike.

    Extracted so the gh-805 §H inheritance path and the ordinary declared
    path cannot render the destructor differently. Two copies of this block
    differing only in where their strings came from is precisely the shape
    this whole key is removing.
    """
    return (
        "    if (self->handle) {\n"
        f"        int rc = {component}_destroy(self->handle);\n"
        "        /* gh-541: clear the handle before reporting, so a second\n"
        "           call is a no-op rather than a double free — the state is\n"
        "           released whatever the status says. */\n"
        "        self->handle = NULL;\n"
        "        if (rc != 0) {\n"
        f"            PyErr_SetString(PyExc_{category},\n"
        f"{_c_string_literal(message, 28)});\n"
        "            return NULL;\n"
        "        }\n"
        "    }\n"
        "    Py_RETURN_NONE;\n"
    )


def _exit_body(component: str, method: dict) -> str:
    """``__exit__``'s body when ``exit`` names a finalizing method (gh-805 §H).

    Two differences from `_teardown_body`, and both are the point:

    - it calls the **finalizer**, not ``<comp>_destroy``;
    - it **leaves ``self->handle`` set**, so the object is still usable after
      the ``with`` block. That is the whole request: a capture's records and
      its drop verdict only become valid once the tail is drained, and the
      natural Python — run the block, then look at what you captured — was
      unreachable while ``__exit__`` freed.

    ``tp_dealloc`` still calls destroy and still discards the status (gh-541),
    so the memory is released exactly once, on the GC path, whether or not the
    ``with`` block ran.

    The failure test and the raise are the finalizer's own declared
    ``status_return`` / ``error`` / ``error_message`` — read from the method
    rather than re-declared, so the explicit ``cap.close()`` call and the
    implicit one at ``__exit__`` cannot disagree about whether a failed
    finalize raises. gh-541 is precisely the bug where they did.
    """
    from ._diagnostics import _rc_raise_c

    name = method.get("name", "")
    c_fn = method.get("fn", "") or f"{component}_{name}"

    # The handle guard is an early return rather than a wrapping `if`, so the
    # `_rc != 0` test lands at indent 4 — the shape `_rc_raise_c` renders its
    # block for. Re-indenting the shared emitter to suit this one caller would
    # have parameterised a difference instead of removing one.
    guard = (
        "    if (!self->handle)\n"
        "        Py_RETURN_NONE;\n"
        "    /* gh-805 §H: the handle deliberately SURVIVES this call —\n"
        "       finalize is not free, and the captured results only become\n"
        "       valid once it has run. The free stays in tp_dealloc. */\n"
    )

    if not method.get("status_return"):
        return (
            guard + f"    (void){c_fn}(self->handle);\n    Py_RETURN_NONE;\n"
        )

    category = method.get("error", "") or "ValueError"
    message = method.get("error_message", "") or f"{name} failed"
    return (
        guard
        + f"    int _rc = {c_fn}(self->handle);\n"
        + "    if (_rc != 0) {\n"
        + _rc_raise_c(category, message)
        + "    }\n"
        "    Py_RETURN_NONE;\n"
    )


def make_destroy_ctx(
    component: str,
    ComponentW: str,
    spec: "dict | None",
    methods: "list[dict] | None",
) -> dict[str, str]:
    """Build every slot the destructor touches (gh-541 / gh-544).

    Parameters
    ----------
    component : str
        Lowercase component id — the C symbol prefix, e.g. ``wfm_writer``.
    ComponentW : str
        The wrapper prefix used for the static C functions, e.g. ``WriterObj``.
        The *C* function name never changes; only the Python bindings do.
    spec : dict, optional
        The ``[<comp>.destroy]`` table. ``None``/``{}`` reproduces the
        pre-gh-541 hardcoded text byte for byte, which is what makes these
        slots safe to introduce into templates every existing project renders.
    methods : list of dict or None
        The component's declared methods, needed to resolve ``exit``
        (gh-805 §H). Omitting it while ``exit`` is declared **raises** rather
        than falling back to the destroy body: a silent fallback would emit a
        binding that frees while both its doc faces say it finalizes, which is
        the exact silent-wrong outcome the key was filed to remove.

        **Deliberately required, with no default** (gh-856). It shipped
        optional, and four of this function's *eight* call sites were left
        unwired — including the two the docstring above and at `_init.py`
        describe as the ``jm apply`` replay path. The result inverted the
        feature: an object with no methods rendered fine, and an object
        declaring the finalizer ``exit`` exists to name was refused *because*
        it declared it. An argument that must be supplied for correctness must
        not be silently omittable: with no default, a forgotten call site is a
        ``TypeError`` the suite hits immediately, instead of a wrong render
        nobody sees until a downstream project runs ``jm apply``. (Runtime,
        not static — this repo has no mypy gate.) Pass ``C.methods(cfg, comp)``
        wherever a
        manifest is in hand — it is correct for creation *and* replay, since
        an absent component yields ``[]`` — and ``[]`` only where the render
        genuinely predates any method (the template seeds).

    Returns
    -------
    dict
        ``destroy_dealloc_call``, ``destroy_method_body``,
        ``destroy_exit_body``, ``destroy_pymethoddef`` (the ``_ext.c`` slots),
        ``destroy_c_ret`` / ``destroy_ret_stmt`` / ``destroy_ret_doc`` (the
        sacred ``_core.h``/``_core.c`` signature), and
        ``pyi_destroy_methods`` (the type stub).

    Examples
    --------
    >>> ctx = make_destroy_ctx("acq", "AcqObj", None, [])
    >>> print(ctx["destroy_dealloc_call"], end="")
        if (self->handle)
            acq_destroy(self->handle);
    >>> ctx["destroy_c_ret"], ctx["destroy_ret_stmt"]
    ('void', '')
    >>> ctx = make_destroy_ctx("w", "WObj", {"name": "close",
    ...                                      "aliases": ["destroy"]}, [])
    >>> [ln for ln in ctx["destroy_pymethoddef"].splitlines()
    ...  if "PyCFunction" in ln]
    ['    {"close",  (PyCFunction)WObj_destroy,  METH_NOARGS,', \
'    {"destroy",  (PyCFunction)WObj_destroy,  METH_NOARGS,']

    Each entry carries the full glue docstring from :mod:`_gluedoc`, and names
    the teardown the way this object spells it:

    >>> "Equivalent to calling `close()`." in ctx["cm_exit_doc"]
    True
    """
    spec = dict(spec or {})
    validate_destroy_spec(component, spec)
    fallible = spec.get("returns") == "int"

    if fallible:
        # gh-541: the one path that must NOT raise. Stated in the generated C
        # so a reader does not "fix" it into a leak of the status.
        dealloc = (
            "    if (self->handle) {\n"
            "        /* gh-541: tp_dealloc has no exception context — there\n"
            "           is no caller to raise to, and an in-flight exception\n"
            "           must not be clobbered. Discarding the status is the\n"
            "           only correct choice here; the explicit teardown and\n"
            "           __exit__ paths do report it. */\n"
            f"        (void){component}_destroy(self->handle);\n"
            "    }\n"
        )
    else:
        dealloc = (
            "    if (self->handle)\n"
            f"        {component}_destroy(self->handle);\n"
        )

    # gh-805 §H: `exit` redirects __exit__ at a finalizing method. Resolved
    # BEFORE the teardown body, because the destructor inherits the
    # finalizer's error/message when it states none of its own — the two are
    # one condition reached by two routes.
    exit_name = (spec or {}).get("exit") or ""
    exit_method: dict = {}
    if exit_name:
        if methods is None:
            raise ValueError(
                f"object '{component}': [{component}.destroy] exit "
                f"'{exit_name}' was declared, but this render path did not "
                f"supply the method list, so the finalizer cannot be "
                f"resolved. Pass methods= to make_destroy_ctx."
            )
        for m in methods:
            if m.get("name") == exit_name:
                exit_method = m
                break
        if not exit_method:
            declared = ", ".join(
                sorted(m.get("name", "") for m in methods if m.get("name"))
            )
            raise ValueError(
                f"object '{component}': [{component}.destroy] exit "
                f"'{exit_name}' is not a declared method. "
                f"Declared: {declared or 'none'}."
            )

    body = _teardown_body(component, spec, exit_method or None)

    names = destroy_py_names(spec)
    # gh-647: one definition of the teardown prose, rendered to both faces.
    # These two had drifted into disagreement -- the runtime table said
    # "Release resources." while the stub said "Release C resources
    # immediately." -- which is exactly what a shared definition prevents.
    Component = C.default_class_name(component)
    _raises: list[str] = []
    if fallible:
        # gh-864: the RESOLVED category, not the raw key. The emitter went
        # through `_inherited_error` and this did not, so with `exit` set the
        # stub's Raises said RuntimeError over a body raising ValueError —
        # a type checker blessing the wrong except clause.
        category = (
            _inherited_error(spec, exit_method)[0]
            if exit_method
            else (spec.get("error") or _DEFAULT_CATEGORY)
        )
        # gh-744: the description wraps like every other numpy description.
        # It landed at 173 columns as one line -- the section is indented 4
        # inside a docstring already indented 8, so the budget is DESC_WIDTH.
        _raises = [
            "Raises",
            "------",
            f"{category}",
            *(
                f"    {w}"
                for w in _wrap(
                    "If the C destructor reports failure. Raised from an "
                    "explicit call and from ``__exit__`` alike, so a failing "
                    "teardown propagates out of a ``with`` block (gh-541).",
                    DESC_WIDTH,
                )
            ),
        ]

    def _doc_for(n: str) -> tuple[str, str]:
        """``(pyi_docstring, c_string_literal)`` for teardown name *n*."""
        gm = glue_methods(Component, close_name=n)[n]
        c_lines = gm.c_doc_lines()
        pyi_lines = gm.pyi_doc()
        if _raises:
            c_lines += [""] + _raises
            # Splice the Raises section in above the closing `"""`.
            pyi_lines = (
                pyi_lines[:-1]
                + [""]
                + [(" " * 8) + ln if ln else "" for ln in _raises]
                + pyi_lines[-1:]
            )
        return "\n".join(pyi_lines) + "\n", _build_ml_doc(c_lines)

    pmd = ""
    pyi = ""
    for n in names:
        _pyi_doc, _c_doc = _doc_for(n)
        pmd += (
            f'    {{"{n}",  (PyCFunction){ComponentW}_destroy,  METH_NOARGS,\n'
            f"     {_c_doc}}},\n"
        )
        pyi += f"\n    def {n}(self) -> None:\n{_pyi_doc}"

    # gh-647: the context-manager pair. Built here because this is where the
    # teardown's Python name is settled -- __exit__'s prose names it, and a
    # reader-shaped object calls it `close`, not `destroy`.
    #
    # gh-805 §H: when `exit` redirects __exit__ at a finalizer, the prose has
    # to follow the CALL, not the teardown. Both faces read `_cm`, so passing
    # the finalizer's name here is what keeps the runtime __doc__ and the
    # `.pyi` from saying "destroy" over a body that closes -- the failure the
    # issue calls out as undetectable, since a doc-parity gate compares the
    # two faces against each other and both would carry the same wrong word.
    _cm = glue_methods(
        Component,
        close_name=exit_name or names[0],
        finalizes=bool(exit_name),
    )

    # The `__exit__` signature is built from the same param list that drives
    # its documented Parameters section, so the two cannot disagree. Defaults
    # keep it as permissive as the METH_VARARGS binding actually is.
    _exit_sig = _cm["__exit__"].pyi_params(defaults=True)

    return {
        "cm_enter_doc": _build_ml_doc(_cm["__enter__"].c_doc_lines()),
        "cm_exit_doc": _build_ml_doc(_cm["__exit__"].c_doc_lines()),
        "pyi_enter_doc": "\n".join(_cm["__enter__"].pyi_doc()),
        "pyi_exit_doc": "\n".join(_cm["__exit__"].pyi_doc()),
        "pyi_exit_sig": _exit_sig,
        "destroy_dealloc_call": dealloc,
        "destroy_method_body": body,
        # gh-805 §H: the two were one string on purpose (gh-541 — the explicit
        # call and the context manager must agree about raising). They still
        # are whenever `exit` is absent; when it is present they are two
        # DIFFERENT C calls, and the agreement that matters moves with it —
        # __exit__ now shares its raise semantics with the finalizer it calls.
        "destroy_exit_body": (
            _exit_body(component, exit_method) if exit_method else body
        ),
        "destroy_pymethoddef": pmd,
        "destroy_c_ret": "int" if fallible else "void",
        "destroy_ret_stmt": "\n    return 0;" if fallible else "",
        "destroy_ret_doc": (
            "\n * @return 0 on success, non-zero on failure."
            if fallible
            else ""
        ),
        "pyi_destroy_methods": pyi,
    }
