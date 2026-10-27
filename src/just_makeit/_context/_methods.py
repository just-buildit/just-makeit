"""_context/_methods.py — method/property context builders.

Contains _bench_method_block, make_methods_ctx, and make_properties_ctx.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import NamedTuple

from .. import _codec as _codec
from .. import _config as C
from .. import _coerce
from .. import _csym as CSYM
from .. import _enumc
from .. import _extramethods
from .. import _inflight
from .. import _borrow
from .. import _outbuf
from .. import _record
from .. import _retain
from .. import _types as T
from .._builtins import result_local
from .._report import Refusal
from ._types import _py_default
from .. import _gluedoc
from .._types import (
    _CTYPE_META,
    _NP_ENUM,
    _CTYPE_TO_NPY,
    _KIND_PY_TEST_VAL,
    record_tuple_build,
    is_array_param_type,
    array_elem_ctype,
    c_param_parts,
)
from .._docstring import (
    EnumAnn,
    method_doc,
    name_summary,
    property_doc,
    class_import_line,
    max_out_is_declared,
    max_out_is_state_only,
    render_numpy_doc,
    render_runtime_doc,
    scaffold_doc_block,
    summary_docstring,
    authored_doc_lines,
    authored_docstring,
    authored_param_docs,
)

from .._gluedoc import glue_methods, max_out_method
from ._diagnostics import (
    # gh-1159: the non-optional half of `declared_raise`. `error_on_empty` is
    # one of that function's triggers, so under it the pair is never None --
    # and writing `_raise_pair or (<defaults>)` at the emitters restated the
    # defaults a third time behind a branch that cannot be taken.
    _raise_pair as raise_pair_of,
    _rc_raise_c,
    count_type as count_type_of,
    declared_raise,
    empty_is_none,
    empty_is_none_doc,
    empty_raise_c,
    fixed_result_ann,
    fixed_result_parts as _fixed_result_parts,
    optional_ann,
    property_declared_raise,
    property_raises_doc,
    raises_doc,
    setter_return_type,
)
from ._parse import (
    _build_ml_doc,
    _build_params_parse,
    _step_parse_block,
    borrow_view_c as _borrow_view_c,
    capsule_new_c as _capsule_new_c,
    enum_symbols as _enum_symbols,
    scalar_parse_c as _scalar_parse_c,
)


# Scalar C-kind -> Python annotation, shared by make_methods_ctx's param/
# return stubs and make_properties_ctx's property stubs — keyed off
# _CTYPE_META's "kind" rather than a parallel ctype table, so a new ctype
# only needs its _CTYPE_META entry (see gh-450, where a second table in
# _stubs.py drifted out of sync with this one).
def _pyi_scalar(ctype: str) -> str:
    # Thin alias for the canonical scalar→Python-builtin mapping in _types, so
    # the method-return and state-accessor annotations cannot drift.
    return T.scalar_py_annotation(ctype)


def _pyi_records(methods: list[dict], doc_blocks: dict | None) -> str:
    """The `.pyi` record-class block for a component's methods (gh-646).

    Rendered above the component class, since the class annotates a return with
    a record's name. `_stubs.make_module_pyi` emits the same block from the
    same builder for the module-aggregated stub.
    """
    body = _record.pyi_classes(methods, doc_blocks)
    return f"\n{body}\n" if body else ""


def _pyi_ndarray(ctype: str) -> str:
    elem = ctype[:-2] if ctype.endswith("[]") else ctype
    meta = _CTYPE_META.get(elem)
    return f"NDArray[{meta['py_type']}]" if meta else "NDArray[Any]"


def _stub_params(
    arg_type: str,
    params: list[dict],
    variable_output: bool = False,
    enums: "dict | None" = None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """The Python-facing arguments of one method, for signature and prose.

    Returns both renderings of the same list so they cannot disagree
    (gh-642). The runtime ``PyMethodDef`` doc and the ``.pyi`` stub must
    document the identical arguments in the identical order; before this was
    hoisted, the stub built its list several hundred lines below the point
    where each method shape emitted its runtime literal, so the runtime face
    had nothing to share and carried the ``@brief`` alone.

    Binding-level arguments (``count``, ``out=``) are **not** returned here:
    whether they exist depends on the method's shape, which the caller
    resolves. They are appended to the returned list at that point (gh-1042),
    not omitted from it — this used to read "deliberately absent... the
    ``Parameters`` section documents what the algorithm takes", and the cost
    of that rule was a generator-shaped method rendering a two-argument
    signature above no ``Parameters`` section at all, plus an authored
    ``@param count`` being silently discarded, since this list is also what
    filters the header's ``@param`` entries.

    Parameters
    ----------
    arg_type : str
        The method's primary input type, or ``"void"``. A non-void one is
        always Python-visible as ``x``.
    variable_output : bool
        Whether the method is ``variable_output``, whose binding consumes a
        BLOCK of ``arg_type`` elements: its ``x`` is then an array even when
        ``arg_type`` names one element (gh-385, the module peer's rule).
    params : list of dict
        Declared extra parameters, in manifest order.

    Returns
    -------
    tuple
        ``(signature_parts, doc_params)``. ``signature_parts`` are
        ``"name: annotation"`` strings carrying any default; ``doc_params``
        are ``(name, annotation)`` pairs with the default stripped, which is
        the form both docstring renderers take.

    Examples
    --------
    >>> _stub_params("float", [])
    (['x: float'], [('x', 'float')])
    >>> _stub_params("void", [{"name": "mu", "type": "double",
    ...                        "default": "0.5"}])
    (['mu: float = 0.5'], [('mu', 'float')])
    >>> _stub_params("void", [{"name": "kind", "type": "int",
    ...                        "enum": "stage", "default": "rs"}])
    (["kind: str = 'rs'"], [('kind', 'str')])
    """
    # (name, annotation, signature-only default suffix)
    fields: list[tuple[str, str, str]] = []
    if arg_type != "void":
        # gh-1724: an array is spelled by the one helper every face calls.
        # This said `x: float` for a variable-output method over a scalar
        # element, whose binding reads an ndarray -- the module peer in
        # `_stubs` already said so.
        ann = (
            T.array_param_annotation(arg_type)
            if arg_type.endswith("[]") or variable_output
            else _pyi_scalar(arg_type)
        )
        fields.append(("x", ann, ""))
    for p in params:
        pt = p["type"]
        if pt.endswith("[]"):
            fields.append(
                (
                    p["name"],
                    # gh-1724: the one helper. gh-1733: an `out` / `mutable`
                    # array takes only a writable ndarray, never a byte buffer.
                    T.array_param_annotation(pt, writable=T.param_writable(p)),
                    "",
                )
            )
        elif p.get("enum"):
            # gh-1021: an enum param is `int` in C and the choice STRING in
            # Python, so both faces must say `str` — the runtime docstring
            # said `int` beside a `>>> obj.m(0)` example that the generated
            # binding now rejects, which is the manifest-contradicts-surface
            # shape the issue is about. The default is a choice, not a C
            # literal, so it is quoted.
            # gh-2379: the annotation carries the choices, so the prose is
            # theirs and not the header's description of the C int.
            fields.append(
                (
                    p["name"],
                    EnumAnn((enums or {}).get(p["enum"]) or []),
                    f" = {p['default']!r}" if p.get("default") else "",
                )
            )
        elif p.get("capsule"):
            # gh-432: a capsule-typed param takes the named PyCapsule, any
            # wrapper exposing `_capsule`, or None (detach) unless it is
            # declared `required = true` (gh-2360).
            from ._parse import capsule_annotation

            fields.append((p["name"], capsule_annotation(p), ""))
        else:
            # gh-240: a defaulted scalar renders as an optional kwarg.
            # gh-1271: through `_py_default`, not verbatim. The C literal went
            # straight out as Python here, so `default = "0U"` rendered
            # `count: int = 0U` -- a SyntaxError that kills the whole file for
            # `mypy` and for `pytest --doctest-glob='*.pyi'`, not merely a
            # wrong value. The annotation widens with it, from the same
            # predicate the format char uses.
            _d = p.get("default") or ""
            # gh-1426 A: a release's count defaults to the OUTSTANDING
            # borrow's, which is a runtime value and has no literal. `...`
            # is what a stub says for exactly that.
            suffix = (
                " = ..."
                if p.get("stub_default")
                else (f" = {_py_default(pt, _d)}" if _d else "")
            )
            fields.append(
                (
                    p["name"],
                    T.py_param_annotation(_pyi_scalar(pt), pt, _d),
                    suffix,
                )
            )
    return (
        [f"{n}: {a}{s}" for n, a, s in fields],
        [(n, a) for n, a, _ in fields],
    )


# A cast-prefixed numpy buffer accessor inside a kernel call argument, e.g.
# ``(const float *)PyArray_DATA(x_arr)`` or ``(size_t)PyArray_SIZE(x_arr)``.
# The leading cast carries the C type, so it doubles as the hoisted-local type.
# gh-2259: the accessor's own argument may be cast too --
# ``(float *)PyArray_DATA((PyArrayObject *)out)``, how a binding reads an
# array it allocated as a ``PyObject *``. Unmatched, that accessor stayed in
# the call and ran inside the released block.
_NOGIL_PYARRAY_RE = re.compile(
    r"\((?P<cast>[^()]+?)\)\s*PyArray_(?:DATA|SIZE)"
    r"\((?:\([^()]+\)\s*)?\w+\)"
)


def _hoist_for_nogil(call_expr: str) -> tuple[str, str]:
    """Hoist every ``PyArray_DATA/SIZE`` out of a kernel-call expression.

    Releasing the GIL around the C kernel is only sound if **no Python C-API**
    runs while it is dropped — but the generated call inlines
    ``PyArray_DATA``/``PyArray_SIZE`` directly. This lifts each such (cast)
    expression into a local declared *before* the ``Py_BEGIN_ALLOW_THREADS``
    block and rewrites the call to reference it. Everything else
    (``self->handle``, ``self->_<m>_buf``, scalar params, ``(size_t)n``) is
    plain memory and is left in place.

    Returns ``(hoist_decls, rewritten_call_expr)``.
    """
    decls: list[str] = []

    def _sub(m: "re.Match[str]") -> str:
        local = f"_ng{len(decls)}"
        decls.append(f"    {m.group('cast')} {local} = {m.group(0)};\n")
        return local

    rewritten = _NOGIL_PYARRAY_RE.sub(_sub, call_expr)
    return "".join(decls), rewritten


def _nogil_call(
    decl: str,
    var: str,
    call_expr: str,
    nogil: bool,
    owner: str = "self",
    *,
    release: str,
    gone: str = "destroyed",
) -> str:
    """Emit ``<decl> <var> = <call>;``, with the GIL released when *nogil*.

    THE emitter for calling the author's kernel. It was two -- one for the
    ``size_t n_out`` shape and one for a by-value struct -- and a borrow
    then grew a THIRD copy inline, which is the one that silently dropped
    `nogil` (gh-1418): the key was recorded, accepted, and never read, so a
    blocking ``wait()`` ran with the GIL held and a threaded producer could
    not make progress. It did not fail; it hung.

    gh-2259: and it was still not every caller. A ``batch`` method and
    every fixed-output shape (a scalar, ``void``, ``status_return``,
    ``error_negative``, ``multi_output`` or ``out_type`` result) spelled
    their call inline, so ``nogil`` was accepted and ignored on all of
    them. Every kernel call in ``make_methods_ctx`` is now built inside a
    call to this function (or to the two wrappers over it), and
    ``tests/test_gh2259_batch_method_faces.py`` refuses one that is not.

    An empty *var* (with an empty *decl*) emits the bare call, for a kernel
    whose return the binding does not keep -- a ``void`` kernel.

    With *nogil* the numpy accessors are hoisted above the
    ``Py_BEGIN_ALLOW_THREADS`` block (:func:`_hoist_for_nogil`) and *var* is
    declared outside it, so it survives to the code below.

    gh-2272: the released block is counted on *owner*'s binding struct
    (`_inflight.region`), so the teardown the same binding generates waits
    for it instead of freeing under it; an object's ``self->handle`` is read
    before the GIL is dropped (`_inflight.hoist_handle`), because that
    teardown NULLs it first to refuse new calls. *owner* is the C pointer to
    that struct: ``self`` for an object, ``w`` for a capsule's wrapper.

    gh-2273: and the object is checked AGAIN here, after the last argument
    conversion. Converting can run Python (an ``__array__``), which can
    call ``destroy()``; the top-of-method guard has long passed, and the
    kernel would be handed a NULL state. *release* undoes what the call
    acquired (the array references, an allocated result) before the same
    ``RuntimeError("destroyed")`` the top guard raises. It is a required
    keyword, so a new call site has to say what it holds; ``""`` is a
    statement that it holds nothing. *gone* is the message, for a wrapper
    whose top guard words it differently. `_inflight.recheck` renders it
    and `tests/test_gh2273_recheck_one_emitter.py` refuses a copy.

    Examples
    --------
    >>> print(_nogil_call("int", "_rc", "k(self->handle)", False,
    ...                   release=""), end="")
        if (!self->handle) {
            PyErr_SetString(PyExc_RuntimeError, "destroyed");
            return NULL;
        }
        int _rc = k(self->handle);
    >>> print(_nogil_call("", "", "k(self->handle)", False,
    ...                   release="Py_DECREF(x);")[-17:], end="")
    k(self->handle);
    >>> body = _nogil_call(
    ...     "", "", "k(self->handle, (float *)PyArray_DATA(a))", True,
    ...     release="")
    >>> print(body[body.index("    float *"):], end="")
        float * _ng0 = (float *)PyArray_DATA(a);
        void *_jm_h = self->handle;
        jm_inflight_enter(&self->_jm_inflight);
        Py_BEGIN_ALLOW_THREADS
        k(_jm_h, _ng0);
        Py_END_ALLOW_THREADS
        jm_inflight_leave(&self->_jm_inflight);
    """
    again = _inflight.recheck(owner, call_expr, release, gone)
    if not nogil:
        lead = f"{decl} {var} = " if var else ""
        return f"{again}    {lead}{call_expr};\n"
    stmt = f"{var} = " if var else ""
    hoist, rewritten = _hoist_for_nogil(call_expr)
    h_decl, rewritten = _inflight.hoist_handle(rewritten)
    return (
        again
        + "    /* nogil: GIL released across the pure-C kernel. Two calls on\n"
        "     * one object may run at once only if its kernel allows it; a\n"
        "     * teardown waits for a running call (gh-2272). */\n"
        f"{hoist}"
        + (f"    {decl} {var};\n" if var else "")
        + h_decl
        + _inflight.region(owner, f"    {stmt}{rewritten};\n")
    )


def _reindent(blk: str) -> str:
    """*blk* one level (four spaces) deeper; a blank line stays blank.

    :func:`_nogil_call` emits at function-body depth, and a binding's
    ``out=`` path places that block inside ``if (out_obj ...) {`` -- a
    ``variable_output`` method's (gh-219) and a ``batch`` method's (gh-222).
    One helper for both, so the two paths indent the same block one way.

    >>> _reindent("    k();\\n\\n    r();\\n")
    '        k();\\n\\n        r();\\n'
    """
    return "".join(
        (("    " + ln) if ln.strip() else ln) + "\n" for ln in blk.splitlines()
    )


def _kernel_call_block(
    call_expr: str,
    nogil: bool,
    m: "dict | None" = None,
    *,
    release: str,
) -> str:
    """``size_t n_out = <call>;`` — the variable-output shape.

    gh-2012: a SIGNED count (`error_negative`, with the `count_type` it
    requires) lands in ``<count_type> _rc`` instead, because its sign has to
    be read before it is a length; :func:`_count_refusal_c` reads it and only
    then declares ``n_out``. Everything after that sees the same
    ``size_t n_out`` as before, so no later comparison mixes signedness.
    """
    if m and m.get("error_negative"):
        return _nogil_call(
            count_type_of(m), "_rc", call_expr, nogil, release=release
        )
    return _nogil_call("size_t", "n_out", call_expr, nogil, release=release)


def _count_refusal_c(m: dict, name: str, release: str, pad: str) -> str:
    """A ``variable_output`` count's declared refusal value (gh-2012).

    The kernel's one return value is the count, so a refusal has to be a
    value no count can be. Two forms, each chosen by the C library's own
    convention:

    ``error_negative`` (with a signed `count_type`)
        ``_rc < 0`` raises through `_rc_raise_c`, the raise the scalar
        ``error_negative`` already uses, so the message carries
        ``(rc=N)`` -- doppler's negative ``DP_ERR_*`` codes. ``_rc >= 0``
        becomes ``size_t n_out``.
    ``error_sentinel``
        ``n_out == (<sentinel>)`` raises through `empty_raise_c`: the value
        tested IS the sentinel, so there is no code worth appending. For a
        library that keeps ``size_t`` counts and cannot change an ABI.

    Emitted on BOTH call paths, right after the kernel returns and BEFORE
    `_coerce.returned_count_c`: a refusal value is past any capacity, so
    the overflow guard would otherwise answer first, as a ``RuntimeError``
    blaming the kernel for writing ``18446744073709551615`` elements. Each
    path passes the arrays IT holds as *release* -- ``out_arr`` on the
    ``out=`` path, ``arr0..`` on the allocate path (gh-1159's lesson).
    ``error_on_empty`` / ``none_on_empty`` still read zero, after the guard,
    independently of this.

    Empty when the method declares neither, so its binding is unchanged.
    """
    body = len(pad) + 4
    lead = " " * body
    if m.get("error_negative"):
        return (
            f"{pad}if (_rc < 0) {{\n"
            f"{lead}{release}\n"
            + _rc_raise_c(*raise_pair_of(m, name), indent=body + 13, pad=body)
            + f"{pad}}}\n"
            f"{pad}size_t n_out = (size_t)_rc;\n"
        )
    sentinel = m.get("error_sentinel")
    if sentinel:
        # Parenthesised: the sentinel is the author's C, and `== A | B`
        # would bind as `(n_out == A) | B`.
        return (
            f"{pad}if (n_out == ({sentinel})) {{\n"
            f"{lead}{release}\n"
            + empty_raise_c(
                *raise_pair_of(m, name), indent=body + 16, pad=body
            )
            + f"{pad}}}\n"
        )
    return ""


def _single_kernel_block(
    ret_disp: str, call_expr: str, nogil: bool, *, release: str
) -> str:
    """``<ret> _r = <call>;`` — a single record returned by value (gh-261)."""
    return _nogil_call(ret_disp, "_r", call_expr, nogil, release=release)


class CallConvention(NamedTuple):
    """How a method wrapper is called: its parameters, row flags and cast.

    ``params`` is the parameter list without its parentheses; ``flags`` and
    ``cast`` are what the wrapper's ``PyMethodDef`` row carries.
    """

    params: str
    flags: str
    cast: str


class MethodHost(NamedTuple):
    """The type a declared method's binding is rendered onto (gh-2259).

    `make_methods_ctx` renders an object's methods onto the object itself:
    ``self`` is a ``<Component>Object *``, the state is ``self->handle``,
    and a wrapper first refuses a destroyed object. A composer's generating
    source delegates a method of its generator, so the same wrapper is
    rendered onto the source type instead: these fields say how, and
    nothing else about the binding changes -- its parse and kernel call
    stay the generator's, and so do its docs and ``.pyi`` member, which
    still name the generator's class (`make_methods_ctx`'s *Component*)
    and gain only what *guard* can raise.
    """

    #: The function-name prefix of every wrapper and file-scope static.
    prefix: str
    #: ``self`` is a ``<self_class>Object *``.
    self_class: str
    #: The C expression of the state pointer the kernel is handed.
    state: str
    #: C each wrapper runs first: makes *state* valid or returns NULL.
    guard: str
    #: The class a wrapper's error messages name -- the one the caller
    #: called (``Synth.prod: ...``), where the docs name *Component*.
    who: str
    #: ``(exception, condition)`` entries *guard* can raise, appended to
    #: every wrapper's documented ``Raises`` on both faces.
    raises: "tuple[tuple[str, str], ...]" = ()


def call_convention(
    Component: str, *, args: bool, kwds: bool = False
) -> CallConvention:
    """The calling convention of a wrapper whose body reads *args* / *kwds*.

    One answer for every method shape `make_methods_ctx` renders, so a
    wrapper's signature, its ``METH_*`` flags and its row's cast are decided
    together, from what the body reads, in this one place:

    ``kwds``
        ``(self, args, kwds)``, ``METH_VARARGS | METH_KEYWORDS``. The row
        casts through ``void *``: a ``PyCFunctionWithKeywords`` cast
        straight to ``PyCFunction`` is an incompatible function-pointer
        cast, a warning today and an error under ``-Werror``.
    ``args`` alone
        ``(self, args)``, ``METH_VARARGS``.
    neither
        ``(self, Py_UNUSED(ignored))``, ``METH_NOARGS``: CPython refuses an
        argument before the wrapper runs, and the slot it still passes is
        marked unused -- the spelling of every no-argument wrapper jm emits.

    gh-1959: a no-argument method returning one record (``single``), or a
    list of them, took ``(self, args)`` under ``METH_VARARGS`` and never
    read ``args``. ``-Wall -Wextra`` reported the parameter, and the call
    accepted any positional arguments and dropped them, although its stub
    takes none.

    Parameters
    ----------
    Component : str
        The Python class name; ``self`` is a ``<Component>Object *``.
    args : bool
        The body parses positional arguments out of ``args``.
    kwds : bool, optional
        The body parses keywords too (implies *args*).

    Returns
    -------
    CallConvention
        ``params``, ``flags`` and ``cast``.

    Examples
    --------
    >>> none = call_convention("Meter", args=False)
    >>> none.params
    'MeterObject *self, PyObject *Py_UNUSED(ignored)'
    >>> none.flags
    'METH_NOARGS'
    >>> call_convention("Meter", args=True).flags
    'METH_VARARGS'
    >>> call_convention("Meter", args=True, kwds=True).cast
    '(PyCFunction)(void *)'
    """
    self_ = f"{Component}Object *self"
    if kwds:
        return CallConvention(
            f"{self_}, PyObject *args, PyObject *kwds",
            "METH_VARARGS | METH_KEYWORDS",
            "(PyCFunction)(void *)",
        )
    if args:
        return CallConvention(
            f"{self_}, PyObject *args", "METH_VARARGS", "(PyCFunction)"
        )
    return CallConvention(
        f"{self_}, PyObject *Py_UNUSED(ignored)",
        "METH_NOARGS",
        "(PyCFunction)",
    )


# ---------------------------------------------------------------------------
# _bench_method_block
# ---------------------------------------------------------------------------


#: The timing helper the no-`step()` bench template used to define itself.
#:
#: gh-840: with no benchable method it was dead code, and the compiler said
#: so — `-Wall -Wextra` on a generated stub gives four unused-symbol
#: warnings, so a project building its benchmarks with `-Werror` could not
#: compile a jm scaffold at all. Emitted only when something times.
#:
#: gh-1341: now EMPTY, because the helper lives in `jm_bench.h` as
#: `jm_bench_elapsed_sec` alongside the clock that feeds it. The slot stays
#: rather than being deleted: the template carries it, and a slot the render
#: stops filling is refused at write time (gh-1199), so removing it here
#: means removing it there in the same change or nothing builds.
_BENCH_ELAPSED_HELPER = ""

#: gh-1341: `uint64_t`, not `struct timespec` — the generated benchmark no
#: longer names a POSIX type. `struct timespec` is C11 on MSVC and was
#: optional before that.
_BENCH_TIMER_DECLS = "    uint64_t t0, t1;\n"


def bench_record_c(
    name: str,
    label: str,
    unit: str,
    *,
    iters: str = "BENCH_N",
    indent: str = "    ",
) -> str:
    """The C that records one timed benchmark row and prints its rate.

    Every generated benchmark times ``ITERATIONS`` rounds into
    ``_times_<name>`` and then does two things with them: hands them to
    ``jm_bench_add_unit`` -- the row ``jm_bench_write_json`` writes -- and
    prints the mean rate to stdout. This is the one spelling of both, for
    the ``step()``, ``steps()`` and method benchmarks alike.

    gh-2282: the row states what one of its ``iters`` counts. Before, every
    row went through ``jm_bench_add`` with no unit, and every printed rate
    said ``MSa/s`` whatever the row counted -- including a row of a single
    call per round that claimed ``BENCH_N`` of them. The unit is one of the
    unit constants ``jm_bench.h`` defines for jm's generated shapes, and the
    printed label is that header's ``jm_bench_rate_label`` -- the one place a
    rate is named -- so the stdout, the JSON and ``jm bench``'s table agree.

    Parameters
    ----------
    name : str
        The row's name in the JSON; ``_times_<name>`` holds its timings.
    label : str
        The stdout label, padded as the caller aligns its rows
        (``"step()  "``).
    unit : str
        The C unit expression: ``JM_BENCH_SAMPLE`` for a round over
        ``BENCH_N`` samples, ``JM_BENCH_CALL`` for a round of calls.
    iters : str
        The C expression for the units in one round.
    indent : str
        The indentation of the emitted statements.

    Returns
    -------
    str
        The statements, without a trailing newline.

    Examples
    --------
    >>> print(bench_record_c("steps", "steps() ", "JM_BENCH_SAMPLE"))
        jm_bench_add_unit(&_bench, "steps", _times_steps, ITERATIONS,
                          BENCH_N, JM_BENCH_SAMPLE);
        {
            double _s = 0.0;
            char   _rate[JM_BENCH_RATE_LEN];
            for (int r = 0; r < ITERATIONS; r++) _s += _times_steps[r];
            printf("  steps()  %8.1f M%s\\n",
                   (double)BENCH_N / (_s / ITERATIONS) / 1e6,
                   jm_bench_rate_label(JM_BENCH_SAMPLE, _rate,
                                       sizeof(_rate)));
        }
    """
    i = indent
    t = f"_times_{name}"
    return "\n".join(
        [
            f'{i}jm_bench_add_unit(&_bench, "{name}", {t}, ITERATIONS,',
            f"{i}                  {iters}, {unit});",
            f"{i}{{",
            f"{i}    double _s = 0.0;",
            f"{i}    char   _rate[JM_BENCH_RATE_LEN];",
            f"{i}    for (int r = 0; r < ITERATIONS; r++) _s += {t}[r];",
            f'{i}    printf("  {label} %8.1f M%s\\n",',
            f"{i}           (double){iters} / (_s / ITERATIONS) / 1e6,",
            f"{i}           jm_bench_rate_label({unit}, _rate,",
            f"{i}                               sizeof(_rate)));",
            f"{i}}}",
        ]
    )


#: The worked `jm_bench_add` example every unfinished benchmark carries.
#: One copy: gh-1034 gave it a second caller (a function-only module) and
#: a second copy of a paste-and-run block is a copy that stops running.
_BENCH_TODO_TAIL = [
    "",
    "The pattern — uncomment and adapt. `jm_bench_add` is what puts a",
    "measurement into the JSON; without one this target writes an empty",
    '"benchmarks": [] array.',
    "",
    "The clock and the elapsed helper come from `jm_bench.h`, which this",
    "file already includes — do not redefine them (gh-1341).",
    "",
    "  uint64_t t0, t1;",
    "  double times[ITERATIONS];",
    "  for (int r = 0; r < ITERATIONS; r++) {",
    "      t0 = jm_bench_now_ns();",
    "      ... call the method BENCH_N times ...",
    "      t1 = jm_bench_now_ns();",
    "      times[r] = jm_bench_elapsed_sec(t0, t1);",
    "  }",
    '  jm_bench_add_unit(&_bench, "<name>", times, ITERATIONS, BENCH_N,',
    "                    JM_BENCH_CALL);",
    "",
    "The last argument says what one of BENCH_N is: JM_BENCH_CALL for",
    "BENCH_N calls, JM_BENCH_SAMPLE for a call over BENCH_N samples,",
    'JM_BENCH_FRAME, or a word of your own such as "bin" (gh-2282).',
]


def _comment_block(body: list[str]) -> str:
    """Wrap *body* as the C comment a scaffolded benchmark carries.

    The prefix is applied once, here, rather than written into each line:
    hand-prefixing produced a block whose continuation lines sat at column
    0 while the rest were indented, and a generated comment is not
    something clang-format will straighten out.
    """
    out = ["    /* TODO: benchmark this component."]
    out += [("     *" + (f" {ln}" if ln else "")) for ln in body]
    out.append("     */")
    return "\n".join(out) + "\n"


def _bench_todo(
    component: str,
    methods: list[dict],
    functions: "list[str] | None" = None,
    *,
    csym: str,
) -> str:
    """The `TODO:` block for a benchmark jm could not populate (gh-840).

    jm's contract is *scaffold, green from day 0, then fill in the TODOs* —
    and for a benchmark the file did not say it. Measured across doppler's 31
    such files: two carried a marker, and both were hand-added years earlier.
    No template jm ships emitted one, so "fill in the TODOs" named nothing an
    author could act on.

    Three things go in, all from data jm already has at render time:

    * that the file is unfinished, in the one word a reader greps for;
    * **which** methods are candidates — the same list `SILENT` counts when
      it reports `(N method(s), none benchable)`, so having it here costs
      nothing;
    * a worked `jm_bench_add` call. That symbol appeared in **zero**
      generated stubs, so the API the scaffold exists to standardise was
      never shown by the scaffold.

    The example carries its own `t0` / `t1` declarations because those are
    no longer emitted unconditionally (they were dead code the compiler
    flagged), so pasting the block in gives a compiling measurement rather
    than a fragment needing three other edits first. The timer and the
    elapsed helper come from `jm_bench.h`, which the benchmark already
    includes (gh-1341).

    The comment prefix is applied **once, here**, rather than written into
    each line: hand-prefixing produced a block whose continuation lines sat
    at column 0 while the rest were indented, and a generated comment is not
    something clang-format will straighten out.
    """
    # gh-1034: a function-only module has no object and no methods, so the
    # two branches below would call it "nothing to time" when in fact every
    # free function it declares is a candidate. Same block, same worked
    # `jm_bench_add` tail — only the lead and the candidate spelling differ,
    # because a module function is called without an `obj`.
    if functions:
        body = [
            "jm did not generate a timing loop: only a human knows what",
            f"question a benchmark asks of these {len(functions)}",
            "function(s).",
            "",
            "Candidates:",
        ] + [f"  {n}(...)" for n in functions]
        return _comment_block(body + _BENCH_TODO_TAIL)
    names = [m["name"] for m in methods]
    if names:
        body = [
            "jm did not generate a timing loop: this component has no",
            f"step(), and none of its {len(names)} method(s) has a shape jm",
            "can size at bench time (variable_output / out_type / varargs /",
            "codec).",
            "",
            "Candidates:",
        ] + [f"  {csym}_{n}(obj, ...)" for n in names]
    else:
        body = [
            "jm did not generate a timing loop: this component has no",
            "step() and no methods, so there is nothing it could time for",
            "you.",
        ]
    body += _BENCH_TODO_TAIL
    return _comment_block(body)


def _prototype_params(proto: str) -> "list[tuple[str, bool]]":
    """``(name, is_pointer)`` for each parameter of a one-line prototype.

    The kernel's own declaration, as the sacred header carries it, read
    back into the parameters a call has to supply.

    >>> _prototype_params("rec_t f(s_t *state, const float *in, size_t n_in);")
    [('state', True), ('in', True), ('n_in', False)]
    """
    inner = proto[proto.index("(") + 1 : proto.rindex(")")]
    out = []
    for decl in inner.split(","):
        decl = decl.strip()
        if decl and decl != "void":
            pname = re.findall(r"\w+", decl)[-1]
            out.append((pname, "*" in decl))
    return out


def _bench_call(c_fn: str, proto: str, args: "dict[str, str]") -> str:
    """The benchmark's call to *c_fn*, argument for argument as *proto*
    declares it.

    The bench used to spell each shape's call by hand, beside the
    prototype the header declares, and the two drifted: a ``single``
    method with a block input was called ``f(obj, 0.0f)`` against
    ``f(state, const float *in, size_t n_in)``. *args* names the value each
    parameter role takes in the timing loop; the prototype decides which
    roles the call has and their order. A parameter with no value is a jm
    bug, so it fails the render naming it rather than emit a call that
    does not compile.
    """
    vals = []
    for pname, _ in _prototype_params(proto):
        if pname not in args:
            raise RuntimeError(
                f"benchmark: no argument for `{pname}`, a parameter of"
                f" {c_fn} in {proto}"
            )
        vals.append(args[pname])
    return f"{c_fn}({', '.join(vals)})"


def _bench_method_block(
    component: str,
    m: dict,
    records: "list[dict] | None" = None,
    *,
    csym: str,
    doc_blocks: "dict | None" = None,
) -> str:
    """Return a self-contained C bench timing block for method *m*.

    Returns an empty string when the method should not be benchmarked
    (``bench == False`` in the method dict, or ``variable_output`` / ``out_type``
    methods whose output size is indeterminate at bench time).

    gh-529: an ``out_type`` method allocates a fresh output buffer per call and
    takes a trailing ``*out`` pointer the C signature (and the ext wrapper)
    supply, but the timing loop here has no such buffer to pass — only the
    ``batch`` shape allocates one. Every other branch would emit a call missing
    the ``out`` argument and the generated benchmark would not compile. Its
    output is sized from a runtime parameter, exactly the ``variable_output``
    situation, so it is skipped the same way rather than benched with a
    fabricated buffer.

    The generated block is wrapped in ``{}`` for scope isolation so that
    per-method locals (buffers, sink variables) do not conflict with each
    other or with the surrounding ``step()``/``steps()`` bench variables.

    Parameters
    ----------
    component : str
        Snake-case component name (e.g. ``"fir"``).
    m : dict
        Method dict with keys: name, arg_type, return_type, variable_output,
        batch, params.  Same shape as the dicts in ``[[comp.methods]]``.
    doc_blocks : dict, optional
        The sacred header as `_object._load_doc_blocks` read it: whether it
        declares a list-of-records method's ``_max_out`` capacity (gh-2205).

    Returns
    -------
    str
        C source fragment (indented 4 spaces, blank line before/after) ready
        to paste into ``main()`` of the bench executable, or ``""`` to skip.
    """
    if (
        m.get("bench") is False
        or m.get("variable_output")
        or m.get("varargs")
        or m.get("out_type")  # gh-529: no out buffer in the timing loop
        or m.get("codec")  # gh-554: a codec-pack method has no C core to time
    ):
        return ""

    name: str = m["name"]
    # gh-805 §A2: benchmark the C symbol the method actually binds. The local
    # variable names below stay keyed on `name` (they are C identifiers in the
    # generated bench, and `fn` may repeat across methods).
    c_fn: str = C.method_c_symbol(csym, m)
    # gh-1404: the bench is a PEER of the binding's own type read below --
    # it emits C against the same prototype, so it resolves a declared
    # element the same way. Missed here first, and the generated benchmark
    # would not compile: `unknown type name 'sample'`.
    arg_type: str = _record.resolve_element(m.get("arg_type", "void"), records)
    return_type: str = _record.resolve_element(
        m.get("return_type", "float _Complex"), records
    )
    batch: bool = m.get("batch", False)
    params: list[dict] = m.get("params", [])
    result_fields: list[dict] = m.get("result_fields", [])
    single_record: bool = m.get("single", False)
    max_results: int = int(m.get("max_results", 64))
    # gh-1310: the bench face never heard about `borrow` (gh-1312). A borrow
    # returns a POINTER into the state's memory, so the generic sink below
    # declared `volatile int16_t sink` for an `int16_t *` -- a constraint
    # violation that is a warning by default and an error under -Werror, in
    # the one generated file no test compiles. `record_dtype` makes it worse
    # rather than introducing it: the element is then a struct, and
    # `result_fields` would also send it down the list-of-records branch and
    # call a kernel signature that does not exist.
    borrow_m: bool = bool(m.get("borrow"))
    borrow_elem: str = (
        _borrow.element_type(m.get("record_dtype", ""), return_type)
        if borrow_m
        else ""
    )

    has_arg = arg_type != "void"
    has_ret = return_type != "void"
    is_array_arg = arg_type.endswith("[]")

    if has_arg:
        arg_elem = arg_type[:-2] if is_array_arg else arg_type
        # gh-1405: a record element has no `_CTYPE_META` row -- it is the
        # author's struct. Its zero is the compound literal, which is what a
        # generated benchmark needs to call the kernel with.
        arg_meta = _CTYPE_META.get(arg_elem)
        arg_elem_disp = arg_elem
        arg_zero = arg_meta["zero"] if arg_meta else f"({arg_elem}){{0}}"

    if has_ret:
        ret_meta = _CTYPE_META.get(return_type)
        ret_disp = return_type if ret_meta else return_type

    # The kernel's declaration, from the one builder the sacred header's
    # comes from; every call below is spelled from it (`_bench_call`).
    from .. import _method

    proto = _method._build_method_prototype(
        component,
        name,
        arg_type,
        return_type,
        False,
        list(m.get("multi_output", [])),
        params,
        batch=batch,
        result_fields=result_fields,
        single=single_record,
        record_dtype=m.get("record_dtype", ""),
        borrow=borrow_m,
        c_fn=m.get("fn", ""),
        csym=csym,
    )
    kernel = _prototype_params(proto)
    declared = {p["name"]: p["type"] for p in params}
    # A kernel that takes its input as a block is handed a buffer, whatever
    # shape declared it (gh-244: a record method with an input does).
    block_in = any(
        p in ("in", "x") and ptr and p not in declared for p, ptr in kernel
    )
    # What each parameter role is passed in the timing loop: the loop's
    # own locals, and for a declared param its zero -- no data for any
    # parameter it expands to a pointer (an array, `bytes`, `path`), and a
    # zero length beside it.
    args = {
        "state": "obj",
        "in": f"{name}_in",
        "x": f"{name}_in" if block_in else (arg_zero if has_arg else ""),
        "n": "BENCH_N",
        "n_in": "BENCH_N",
        "x_len": "BENCH_N",
        "out": f"{name}_out",
        "result": f"{name}_results",
    }
    for pname, ptr in kernel:
        if pname in declared:
            zero = _CTYPE_META.get(declared[pname], {}).get("zero", "0")
            args[pname] = "NULL" if ptr else zero
        elif pname.endswith("_len") and pname[: -len("_len")] in declared:
            args[pname] = "0"

    # gh-1523: gh-600 made each extra `multi_output` value a trailing
    # `<T> *outN` parameter, after the params, and the binding passes a local
    # for each (`&out1`, ...). The bench still called the old two-argument
    # form, so a fresh multi-output method's tree did not build. It passes a
    # local the same way, on the two shapes whose prototype carries them; the
    # batch prototype carries none, and the result_fields and skipped shapes
    # never reach a call with them.
    multi_output: list[str] = list(m.get("multi_output", []))
    mo_decls = [
        f"        {rt} {name}_out{i + 1} = {_CTYPE_META[rt]['zero']};"
        for i, rt in enumerate(multi_output)
    ]
    for i in range(len(multi_output)):
        args[f"out{i + 1}"] = f"&{name}_out{i + 1}"

    lines: list[str] = [f"    /* bench: {name}() */", "    {"]
    has_sink = False
    # gh-2282: what one round's work is, per branch below. A round over a
    # BENCH_N-element input (or output) is BENCH_N samples; a round of
    # BENCH_N calls with no input is BENCH_N calls.
    unit, iters = "JM_BENCH_SAMPLE", "BENCH_N"
    lines.append(f"        double _times_{name}[ITERATIONS];")

    if result_fields and not single_record and not borrow_m:
        # gh-244: a results[]/max_results method returns a count (size_t),
        # not `return_type` directly — the call signature and sink differ
        # from every other shape below, so this is handled first and skips
        # the generic branches entirely.
        rf_disp = return_type
        # gh-2205: the capacity the binding asks (gh-2184), when the header
        # declared it before this file was rendered -- the bench is
        # create-only, so a later one reaches it only when it is rendered
        # again. Asked once, for the input the timing loop passes, so a
        # kernel that stops at a full buffer is timed over its whole call
        # rather than the fixed buffer's 64 records.
        rf_heap = max_out_is_declared(doc_blocks, f"{c_fn}_max_out")
        if rf_heap:
            # The count the kernel is handed: the block input's, else the
            # first array param's, which the loop passes empty; with
            # neither, the capacity takes only the state.
            rf_count = "BENCH_N" if has_arg else None
            if not has_arg and any(
                is_array_param_type(p["type"]) for p in params
            ):
                rf_count = "0"
            rf_cap = f"{name}_cap"
            lines += [
                f"        size_t {rf_cap} ="
                f" {_max_out_call_c(c_fn, doc_blocks, rf_count, 'obj')};",
                f"        {rf_disp} *{name}_results = ({rf_disp} *)malloc("
                f"({rf_cap} ? {rf_cap} : 1) * sizeof({rf_disp}));",
                f"        if (!{name}_results) {{"
                ' fprintf(stderr, "OOM\\n"); return 1; }',
            ]
        else:
            rf_cap = str(max_results)
            lines.append(f"        {rf_disp} {name}_results[{max_results}];")
        # gh-1961: the params sit between the input and the results, as in
        # the prototype; this call omitted them, like the binding did.
        args["max_results"] = rf_cap
        call = _bench_call(c_fn, proto, args)
        if has_arg:
            lines += [
                f"        {arg_elem_disp} *{name}_in ="
                f" ({arg_elem_disp} *)calloc(BENCH_N,"
                f" sizeof({arg_elem_disp}));",
                f'        if (!{name}_in) {{ fprintf(stderr, "OOM\\n"); return 1; }}',
            ]
        else:
            # gh-2282: ONE call per round, with no input to count -- the
            # row said BENCH_N, so its rate was BENCH_N times too high.
            unit, iters = "JM_BENCH_CALL", "1"
        lines.append(f"        volatile size_t {name}_sink;")
        has_sink = True
        lines += [
            f"        for (int i = 0; i < 4; i++) {name}_sink = {call};",
            "        for (int r = 0; r < ITERATIONS; r++) {",
            "            t0 = jm_bench_now_ns();",
            f"            {name}_sink = {call};",
            "            t1 = jm_bench_now_ns();",
            f"            _times_{name}[r] = jm_bench_elapsed_sec(t0, t1);",
            "        }",
        ]
        if has_arg:
            lines.append(f"        free({name}_in);")
        if rf_heap:
            lines.append(f"        free({name}_results);")
    elif batch:
        if has_arg:
            # gh-139: the input buffer holds elements; an array arg_type
            # (`T[]`) must use the element display, not `T[] *…`.
            lines += [
                f"        {arg_elem_disp} *{name}_in ="
                f" ({arg_elem_disp} *)calloc(BENCH_N,"
                f" sizeof({arg_elem_disp}));",
            ]
        ret_disp_b = return_type
        lines += [
            f"        {ret_disp_b} *{name}_out ="
            f" ({ret_disp_b} *)malloc("
            f"BENCH_N * sizeof({ret_disp_b}));",
        ]
        chk_vars = f"{name}_in && {name}_out" if has_arg else f"{name}_out"
        lines += [
            f'        if (!({chk_vars})) {{ fprintf(stderr, "OOM\\n"); return 1; }}',
        ]
        call = _bench_call(c_fn, proto, args)
        lines += [
            "        for (int i = 0; i < 4; i++)",
            f"            {call};",
            "        for (int r = 0; r < ITERATIONS; r++) {",
            "            t0 = jm_bench_now_ns();",
            f"            {call};",
            "            t1 = jm_bench_now_ns();",
            f"            _times_{name}[r] = jm_bench_elapsed_sec(t0, t1);",
            "        }",
        ]
        if has_arg:
            lines.append(f"        free({name}_in);")
        lines.append(f"        free({name}_out);")

    elif block_in:
        lines += [
            f"        {arg_elem_disp} *{name}_in ="
            f" ({arg_elem_disp} *)calloc("
            f"BENCH_N, sizeof({arg_elem_disp}));",
            f'        if (!{name}_in) {{ fprintf(stderr, "OOM\\n"); return 1; }}',
        ]
        if has_ret:
            # `T *volatile` -- the POINTER is the volatile object, so the
            # compiler cannot elide the call. `volatile T *` would make the
            # pointee volatile and leave the assignment removable.
            lines.append(
                f"        {borrow_elem} *volatile {name}_sink;"
                if borrow_m
                else f"        volatile {ret_disp} {name}_sink;"
            )
            has_sink = True
        sink = f"{name}_sink = " if has_ret else ""
        lines += mo_decls
        call = _bench_call(c_fn, proto, args)
        lines += [
            "        for (int i = 0; i < 4; i++)",
            f"            {sink}{call};",
            "        for (int r = 0; r < ITERATIONS; r++) {",
            "            t0 = jm_bench_now_ns();",
            f"            {sink}{call};",
            "            t1 = jm_bench_now_ns();",
            f"            _times_{name}[r] = jm_bench_elapsed_sec(t0, t1);",
            "        }",
            f"        free({name}_in);",
        ]

    else:
        if has_ret:
            # `T *volatile` -- the POINTER is the volatile object, so the
            # compiler cannot elide the call. `volatile T *` would make the
            # pointee volatile and leave the assignment removable.
            lines.append(
                f"        {borrow_elem} *volatile {name}_sink;"
                if borrow_m
                else f"        volatile {ret_disp} {name}_sink;"
            )
            has_sink = True
        sink = f"{name}_sink = " if has_ret else ""
        lines += mo_decls
        # BENCH_N calls a round: each takes one sample when the method has
        # an input, and is just a call when it has none.
        if not has_arg:
            unit = "JM_BENCH_CALL"
        call = _bench_call(c_fn, proto, args)
        lines += [
            f"        for (int i = 0; i < 16; i++) {sink}{call};",
            "        for (int r = 0; r < ITERATIONS; r++) {",
            "            t0 = jm_bench_now_ns();",
            "            for (int i = 0; i < BENCH_N; i++)",
            f"                {sink}{call};",
            "            t1 = jm_bench_now_ns();",
            f"            _times_{name}[r] = jm_bench_elapsed_sec(t0, t1);",
            "        }",
        ]

    # gh-1745: every branch above that declares `<name>_sink` only stores to
    # it, which is -Wunused-but-set-variable (gcc and clang) and an error
    # under -Werror. One read after the timing loop makes it used; each
    # declaration sets `has_sink`, so no branch can declare one and miss it.
    if has_sink:
        lines.append(f"        (void){name}_sink;")
    lines.append(
        bench_record_c(name, f"{name}() ", unit, iters=iters, indent=" " * 8)
    )

    lines.append("    }")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# make_methods_ctx
# ---------------------------------------------------------------------------


def serializable_triplet_parts(
    component: str, Component: str, wrapper_prefix: str, *, csym: str
) -> tuple[list[str], str, str]:
    """The gh-400 state-blob binding for one object, as reusable text.

    Returns ``(c_funcs, pymethoddef_rows, pyi_stubs)``: the three
    ``state_bytes``/``get_state``/``set_state`` CPython wrapper functions, the
    three ``PyMethodDef`` rows, and the ``.pyi`` stub block. Shared by the
    regenerate path (:func:`make_methods_ctx`) and the sacred-fragment
    transplant (:mod:`_docsync`, gh-404) so both emit byte-identical glue.

    ``wrapper_prefix`` is the Python function-name prefix (e.g. ``FooObj``);
    the C calls use ``component`` (``foo_state_bytes`` over ``self->handle``).
    """
    _W = wrapper_prefix
    guard = (
        "    if (!self->handle) {\n"
        '        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
        "        return NULL;\n"
        "    }\n"
    )
    c_funcs = [
        (
            f"static PyObject *\n"
            f"{_W}_state_bytes"
            f"({Component}Object *self, PyObject *Py_UNUSED(ignored))\n"
            f"{{\n{guard}"
            f"    return PyLong_FromSize_t("
            f"{csym}_state_bytes(self->handle));\n"
            f"}}"
        ),
        (
            f"static PyObject *\n"
            f"{_W}_get_state"
            f"({Component}Object *self, PyObject *Py_UNUSED(ignored))\n"
            f"{{\n{guard}"
            f"    size_t _n = {csym}_state_bytes(self->handle);\n"
            f"    PyObject *_b = PyBytes_FromStringAndSize"
            f"(NULL, (Py_ssize_t)_n);\n"
            f"    if (!_b)\n"
            f"        return NULL;\n"
            f"    {csym}_get_state(self->handle, PyBytes_AS_STRING(_b));\n"
            f"    return _b;\n"
            f"}}"
        ),
        (
            f"static PyObject *\n"
            f"{_W}_set_state({Component}Object *self, PyObject *arg)\n"
            f"{{\n{guard}"
            f"    if (!PyBytes_Check(arg)) {{\n"
            f"        PyErr_SetString(PyExc_TypeError,"
            f' "set_state expects bytes");\n'
            f"        return NULL;\n"
            f"    }}\n"
            f"    if ((size_t)PyBytes_GET_SIZE(arg)"
            f" != {csym}_state_bytes(self->handle)) {{\n"
            f"        PyErr_SetString(PyExc_ValueError,"
            f' "state blob size mismatch");\n'
            f"        return NULL;\n"
            f"    }}\n"
            f"    if ({csym}_set_state(self->handle,"
            f" PyBytes_AS_STRING(arg)) != 0) {{\n"
            f"        PyErr_SetString(PyExc_ValueError,"
            f' "set_state rejected the blob");\n'
            f"        return NULL;\n"
            f"    }}\n"
            f"    Py_RETURN_NONE;\n"
            f"}}"
        ),
    ]
    # gh-647: one definition of this prose, rendered to both faces. The
    # literals these replaced had drifted -- get_state named "the engine",
    # a component from a long-gone example rather than the object it
    # documents.
    # gh-915: the PROSE names the Python class, so it takes `Component` (the
    # `class_name` override when one is declared) and not `_W`, which is a C
    # symbol prefix and gains an `Obj` suffix on a no_state object. Passing
    # `_W` documented `--class-name DDC` as "Lets a DdcObj be used…" — a name
    # that appears nowhere in the API the reader is holding.
    _glue = glue_methods(Component)
    _meth_c = {
        "state_bytes": "METH_NOARGS",
        "get_state": "METH_NOARGS",
        "set_state": "METH_O",
    }
    pmd = "".join(
        f'    {{"{n}", (PyCFunction){_W}_{n}, {flags},\n'
        f"     {_build_ml_doc(_glue[n].c_doc_lines())}}},\n"
        for n, flags in _meth_c.items()
    )
    _sigs = {
        "state_bytes": "self) -> int",
        "get_state": "self) -> bytes",
        "set_state": "self, blob: bytes) -> None",
    }
    pyi = "\n".join(
        f"    def {n}({sig}:\n" + "\n".join(_glue[n].pyi_doc())
        for n, sig in _sigs.items()
    )
    return c_funcs, pmd, pyi


def state_blob_fields(
    state_vars, opaque_fields=(), array_args=()
) -> "list[str] | None":
    """The state fields a scaffolded blob can copy byte for byte, or None.

    gh-1509. A field qualifies when its bytes ARE its value: a scalar jm
    knows (every ``_CTYPE_META`` kind but ``str``, which is a pointer) or a
    fixed-length ``T[N]`` array of one. Anything else -- a ``const char *``,
    an ``array_args`` buffer the struct points at, an ``opaque_fields``
    member whose type jm never sees -- owns memory elsewhere, and copying its
    bytes would copy an address. One such field makes the whole answer None,
    because serializing the others and dropping it would lose state silently.

    Examples
    --------
    >>> state_blob_fields([("ph", "double", "0.0"), ("taps", "float[4]", "")])
    ['ph', 'taps']
    >>> state_blob_fields([("name", "const char *", "NULL")]) is None
    True
    >>> state_blob_fields([("g", "float", "0")], [("h", "void *")]) is None
    True
    >>> state_blob_fields([])
    []
    """
    if opaque_fields or array_args:
        return None
    names = []
    for name, ctype, *_ in state_vars:
        parsed = T.parse_array_type(ctype)
        elem = parsed[0] if parsed else ctype
        meta = T._CTYPE_META.get(elem)
        if meta is None or meta["kind"] == "str":
            return None
        names.append(name)
    return names


def make_serializable_core_ctx(
    component: str,
    serializable: bool,
    fields: "list[str] | None",
    *,
    csym: str,
) -> dict:
    """The C triplet a ``serializable`` object's binding calls (gh-1509).

    gh-400 generates the Python ``state_bytes``/``get_state``/``set_state``
    over three C functions and, until gh-1509, left all three to the author
    -- undeclared, so the scaffold jm had just written failed to compile on
    implicit declaration. They are now scaffolded like every other function
    the binding calls: prototypes in the sacred ``_core.h``
    (``serializable_decls``) and bodies in ``_core.c``
    (``serializable_impls``), which a header-only core carries inline.

    What the bodies do depends on *fields* (:func:`state_blob_fields`):

    - a list: a working implementation. The blob is the listed fields packed
      in declaration order, so ``set_state(get_state())`` round-trips. It
      covers the state declared when jm wrote it; a field added later has to
      be added by hand, which the comment above the functions says.
    - None: a stub that refuses. ``state_bytes`` is 0 and ``set_state``
      returns -1, which the binding raises as ``ValueError`` -- loud, where a
      silent empty blob would restore nothing and report success.

    The slots are empty for an object that is not serializable, so every
    other scaffold renders byte-identically.

    Examples
    --------
    >>> ctx = make_serializable_core_ctx("q", False, [], csym="q")
    >>> ctx["serializable_decls"], ctx["serializable_impls"]
    ('', '')
    >>> print(make_serializable_core_ctx("q", True, ["x"], csym="q")[
    ...     "serializable_decls"].strip())
    /** @brief Bytes in the blob q_get_state() writes. */
    size_t q_state_bytes(const q_state_t *state);
    <BLANKLINE>
    /** @brief Write the state into @p blob. */
    void q_get_state(const q_state_t *state, void *blob);
    <BLANKLINE>
    /** @brief Restore the state; nonzero rejects @p blob. */
    int q_set_state(q_state_t *state, const void *blob);
    """
    if not serializable:
        return {"serializable_decls": "", "serializable_impls": ""}
    c, st = csym, f"{csym}_state_t"
    decls = (
        f"\n\n/** @brief Bytes in the blob {c}_get_state() writes. */"
        f"\nsize_t {c}_state_bytes(const {st} *state);"
        f"\n\n/** @brief Write the state into @p blob. */"
        f"\nvoid {c}_get_state(const {st} *state, void *blob);"
        f"\n\n/** @brief Restore the state; nonzero rejects @p blob. */"
        f"\nint {c}_set_state({st} *state, const void *blob);"
    )
    if fields is None:
        head = (
            "/* <<IMPLEMENT: serialize the state (gh-400). A field of this\n"
            " * struct owns memory outside it, so jm cannot copy its bytes.\n"
            " * Until this is written, set_state() raises ValueError. >> */\n"
        )
        size = "    (void)state;\n    return 0;\n"
        get = "    (void)state;\n    (void)blob;\n"
        put = "    (void)state;\n    (void)blob;\n    return -1;\n"
    else:
        head = (
            "/* The state blob: the fields below, packed in declaration\n"
            " * order (gh-400). jm wrote this from the state declared when\n"
            " * the object was scaffolded -- a field added since must be\n"
            " * added here too. */\n"
        )
        if fields:
            size = "    return {};\n".format(
                " + ".join(f"sizeof(state->{f})" for f in fields)
            )
            get = "    unsigned char *p = blob;\n" + "".join(
                f"    memcpy(p, &state->{f}, sizeof(state->{f}));\n"
                f"    p += sizeof(state->{f});\n"
                for f in fields
            )
            put = (
                "    const unsigned char *p = blob;\n"
                + "".join(
                    f"    memcpy(&state->{f}, p, sizeof(state->{f}));\n"
                    f"    p += sizeof(state->{f});\n"
                    for f in fields
                )
                + "    return 0;\n"
            )
        else:
            size = "    (void)state;\n    return 0;\n"
            get = "    (void)state;\n    (void)blob;\n"
            put = "    (void)state;\n    (void)blob;\n    return 0;\n"
    impls = (
        f"\n\n{head}size_t\n{c}_state_bytes(const {st} *state)\n{{\n{size}}}"
        f"\n\nvoid\n{c}_get_state(const {st} *state, void *blob)\n{{\n{get}}}"
        f"\n\nint\n{c}_set_state({st} *state, const void *blob)\n{{\n{put}}}"
    )
    return {"serializable_decls": decls, "serializable_impls": impls}


def _max_out_doc(
    component,
    name,
    count_param,
    max_out_const,
    block_of,
    c_fn="",
    *,
    csym: str,
    elements_per_sample: int = 1,
):
    """The doc for ``<name>_max_out``: header block if authored, else jm's.

    gh-684. Unlike the glue in :mod:`_gluedoc`, ``max_out``'s *value* is
    object-specific and its C body is an ``IMPLEMENT`` stub the author writes
    unless the manifest declared the constant -- so the header always wins and
    jm's prose is only the fallback.

    gh-805 §A2: *c_fn* is the method's resolved C symbol, which the header
    documents. *name* stays the PYTHON face — ``max_out_method`` below renders
    ``<name>_max_out`` for the reader — so the two are deliberately separate
    arguments rather than one. Defaults to the derived symbol.
    """
    blk = block_of(
        f"{C.method_c_symbol(csym, {'name': name, 'fn': c_fn})}_max_out"
    )
    gm = max_out_method(
        name,
        count_param or "",
        int(max_out_const or 0),
        elements_per_sample,
    )
    if blk is not None:
        # gh-1052: through the constructor, which also records that this
        # body is the header's LINES rather than jm's paragraphs.
        gm = gm.with_header_block(blk)
    return gm


def _max_out_return_c(call: str, elements_per_sample: int, who: str) -> str:
    """The ``<m>_max_out`` binding's return, in numpy elements (gh-1996).

    The C ``max_out`` answers in the kernel's samples. The Python face sizes
    an ``out=`` array, which holds elements, so under an interleave the
    answer is multiplied back -- bounded first through the one size emitter,
    so a sample count past ``SIZE_MAX / E`` is an ``OverflowError`` rather
    than a product that wraps to a small, wrong bound. With no interleave
    this is the line the binding always returned.

    Examples
    --------
    >>> print(_max_out_return_c("f_max_out(s)", 1, "F.m_max_out"), end="")
        return PyLong_FromSize_t(
            f_max_out(s));
    >>> print(_max_out_return_c("f_max_out(s)", 2, "F.m_max_out"), end="")
        size_t _mo_need = (size_t)(f_max_out(s));
        if (_mo_need > (size_t)(SIZE_MAX / 2)) {
            PyErr_Format(PyExc_OverflowError,
                "F.m_max_out: output of %zu samples of 2 elements is too large", _mo_need);
            return NULL;
        }
        size_t _mo = (size_t)_mo_need;
        return PyLong_FromSize_t(_mo * 2);
    """
    if elements_per_sample == 1:
        return f"    return PyLong_FromSize_t(\n        {call});\n"
    return _coerce.output_size_c(
        "_mo",
        call,
        who,
        ctype="size_t",
        limit=f"(SIZE_MAX / {elements_per_sample})",
        unit=f"samples of {elements_per_sample} elements",
    ) + (f"    return PyLong_FromSize_t(_mo * {elements_per_sample});\n")


def _count_default_parts(
    expr: str, component: str, *, csym: str, state: str = "self->handle"
) -> tuple[str, str]:
    r"""Return ``(initialiser, state_alias_line)`` for a ``count_default``.

    gh-657. A void-input ``variable_output`` method binds its capacity as the
    ``count`` keyword, and jm has always initialised it to ``1``. That default
    is the method's entire zero-arg behaviour, and ``1`` is almost never the
    right snapshot size — but the right size (the object's ring length, its
    buffered depth) lives in the user's C, not in the manifest, so it has to
    be declared rather than derived.

    *expr* is C, evaluated once before ``PyArg_ParseTupleAndKeywords`` and
    overridden by any count the caller actually passes. An expression that
    mentions ``state`` gets a local alias for ``self->handle`` so the natural
    ``state->num_taps`` reads correctly; one that does not (a plain integer,
    say) gets no alias, which keeps ``-Wunused-variable`` quiet.

    Parameters
    ----------
    expr : str
        The manifest's ``count_default``. Empty restores the historical ``1``.
    component : str
        Component name, used to spell the state type for the alias.
    state : str, optional
        The state pointer the alias reads: ``self->handle`` on the object,
        a :class:`MethodHost`'s ``state`` elsewhere (gh-2259).

    Returns
    -------
    tuple of (str, str)
        The initialiser expression, and either an empty string or a complete
        declaration line (newline included) to emit before it.

    Examples
    --------
    >>> _count_default_parts("", "delay", csym="delay")
    ('1', '')
    >>> _count_default_parts("64", "delay", csym="delay")
    ('(Py_ssize_t)(64)', '')
    >>> _count_default_parts("state->num_taps", "delay", csym="delay")
    ('(Py_ssize_t)(state->num_taps)', '    delay_state_t *state = self->handle;\n')
    """
    if not expr:
        return "1", ""
    alias = ""
    if re.search(r"\bstate\b", expr):
        alias = f"    {csym}_state_t *state = {state};\n"
    return f"(Py_ssize_t)({expr})", alias


def _max_out_count_param_ctx(
    has_arg: bool,
    has_params: bool,
    params: "list[dict]",
    no_count: bool = False,
) -> "tuple[str, str | None]":
    """gh-607: peer of ``_method._max_out_count_param`` — the same per-shape
    count parameter ``*_max_out()`` takes, adapted for this module's
    dict-shaped ``params`` (vs. ``_method.py``'s ``(name, type)`` tuples).
    Duplicated rather than imported: importing ``_method.py`` here would
    cycle (``_method`` -> ``_object`` -> ``_context`` -> ``_context._methods``).
    Keep the two in sync. gh-2233: ``no_count`` has no count to mirror.
    """
    if no_count:
        return "", None
    if has_arg:
        return ", size_t n_in", "n_in"
    if has_params:
        for p in params:
            if is_array_param_type(p["type"]):
                return f", size_t {p['name']}_len", f"{p['name']}_len"
        return "", None
    return ", size_t n", "n"


def _max_out_call_c(
    c_fn: str,
    doc_blocks: "dict | None",
    count: "str | None",
    state: str = "self->handle",
) -> str:
    """The ``<c_fn>_max_out(...)`` call a wrapper sizes its output with.

    One spelling for every wrapper that asks a method's capacity before
    calling its kernel: the ``variable_output`` shapes (gh-607) and a
    list-of-records method whose header declares one (gh-2184) -- and the
    generated benchmark of that method (gh-2205), whose *state* is its
    ``obj``. *count* is the C expression the kernel is about to be handed as
    its input length, or ``None`` for a shape with no count to mirror.
    *state* is the state pointer the kernel is handed: ``self->handle``, a
    :class:`MethodHost`'s (gh-2259), or the benchmark's ``obj``.

    gh-761: the header is the source of truth for the arity. A declaration
    that takes only the state is called with only the state, whatever count
    the shape has -- a wrapper calling ``max_out(state, n)`` against a
    ``max_out(state)`` prototype does not compile.

    Examples
    --------
    >>> _max_out_call_c("h_push", None, "n_in")
    'h_push_max_out(self->handle, n_in)'
    >>> _max_out_call_c("h_push", None, None)
    'h_push_max_out(self->handle)'
    >>> from .._docstring import max_out_arity_key
    >>> _max_out_call_c(
    ...     "h_push", {max_out_arity_key(): {"h_push_max_out"}}, "n_in"
    ... )
    'h_push_max_out(self->handle)'
    >>> _max_out_call_c("h_push", None, "BENCH_N", "obj")
    'h_push_max_out(obj, BENCH_N)'
    """
    state_only = max_out_is_state_only(doc_blocks, f"{c_fn}_max_out")
    arg = f", {count}" if (count and not state_only) else ""
    return f"{c_fn}_max_out({state}{arg})"


def _records_consumed_c(
    buffer: "Callable[[str, str], tuple[str, str, str]]",
    call: "Callable[[str, str], str]",
    *,
    ptr: str,
    count: str,
    held: str,
    consumed_fn: str,
    who: str,
    build: str,
    state: str = "self->handle",
) -> str:
    """A list-of-records body that re-offers its input until none is left.

    gh-2204. The kernel's contract, ``(..., const T *in, size_t n_in, rec_t
    *result, size_t max_results) -> count``, lets it stop when ``result`` is
    full -- a detector's hit count depends on the data, so no capacity can
    bound it up front. Called once, everything past where it stopped was
    never seen by the C. With ``consumed_fn`` declared the binding asks it
    after every call how much of the input that call took, advances *ptr* /
    *count* past it, and calls again on the rest; the records of every call
    are appended to one ``list``, in call order -- the shape the method
    always returned.

    Each call's buffer is *buffer*'s answer for the input still left (the
    method's ``_records_buffer_c``): the fixed ``max_results`` stack array,
    or, when the header declares ``<c>_<m>_max_out``, that many records
    allocated for the remainder. The params ride along unchanged in *call*,
    which emits the kernel call over a capacity. A call offered nothing
    still runs once, as it always has. ``_coerce.consumed_guard_c`` raises
    rather than spinning on a call that took none of what is left, or
    stepping past the input's end on one claiming more.

    Parameters
    ----------
    buffer : callable
        ``(count, release) -> (declare, capacity, free)`` for one call.
    call : callable
        ``(capacity, release) -> C``: the kernel call's block for a capacity
        expression, with *release* the statements that undo everything held
        at that point (the buffer, the list, the parsed arrays), built by the
        caller inside :func:`_kernel_call_block` -- the one ``nogil``
        emitter, which ``tests/test_gh2259_batch_method_faces.py`` requires
        every kernel call in ``make_methods_ctx`` to be spelled inside.
    ptr, count : str
        The input block's pointer and element count: mutable locals, which
        the loop advances.
    held : str
        Statements releasing what the wrapper holds (its parsed arrays), on
        every way out.
    consumed_fn : str
        The author's ``size_t <fn>(const <state> *)``.
    who : str
        ``Class.method``, for the messages.
    build : str
        The ``Py_BuildValue`` arguments for ``results[i]``.
    state : str, optional
        The state pointer *consumed_fn* is asked about: ``self->handle`` on
        the object, a :class:`MethodHost`'s ``state`` elsewhere (gh-2259).

    Returns
    -------
    str
        The C from the list's creation to ``return lst;``.
    """
    rel_lst = f"{held} Py_DECREF(lst);".strip()
    decl, cap, free = buffer(count, rel_lst)
    rel_all = f"{free} {rel_lst}".strip()
    body = (
        decl
        + call(cap, rel_all)
        # gh-1716: `n_out` records are read from `results[]`.
        + _coerce.returned_count_c("n_out", cap, who, rel_all)
        + "    for (size_t i = 0; i < n_out; i++) {\n"
        f"        PyObject *tup = Py_BuildValue({build});\n"
        "        if (!tup || PyList_Append(lst, tup) < 0) {\n"
        "            Py_XDECREF(tup);\n"
        f"            {rel_all}\n"
        "            return NULL;\n"
        "        }\n"
        "        Py_DECREF(tup);\n"
        "    }\n"
        + (f"    {free}\n" if free else "")
        + f"    size_t _consumed = {consumed_fn}({state});\n"
        + _coerce.consumed_guard_c(
            "_consumed", count, consumed_fn, who, rel_lst
        )
        + f"    {ptr} += _consumed;\n"
        f"    {count} -= _consumed;\n"
    )
    pad = "    "
    return (
        "    PyObject *lst = PyList_New(0);\n"
        f"    if (!lst) {{{f' {held}' if held else ''} return NULL; }}\n"
        "    do {\n"
        + "".join(
            pad + ln if ln.strip() else ln for ln in body.splitlines(True)
        )
        + f"    }} while ({count} > 0);\n"
        + (f"    {held}\n" if held else "")
        + "    return lst;\n"
    )


def _records_buffer_c(
    ret_disp: str,
    c_fn: str,
    doc_blocks: "dict | None",
    count: "str | None",
    max_results: int,
    who: str,
    release: str,
    state: str = "self->handle",
) -> "tuple[str, str, str]":
    """``(declare, capacity, free)`` for a list-of-records result buffer.

    gh-2184. The kernel's contract is ``(..., T *result, size_t
    max_results) -> count``: it fills at most *max_results* records and has
    no way to say there were more. So the buffer's size IS the most a call
    can ever return, and a fixed one silently dropped whatever the C still
    had to report -- doppler's detectors lost every result past the 64th.

    Without a declared capacity the buffer is what it always was: a stack
    array of the manifest's ``max_results`` (default 64), byte-identical to
    every wrapper rendered before this. When the header declares
    ``<c_fn>_max_out`` -- the ``variable_output`` capacity function, same
    name, same arity rules, called through :func:`_max_out_call_c` -- the
    wrapper asks it, heap-allocates that many records, and hands the kernel
    exactly that capacity. A function that can bound its output then never
    saturates. The bound is trusted as given, as ``pass_capacity`` trusts a
    ``variable_output`` one: the kernel is told it, so a capacity of 0 is
    an empty result, not an overrun. ``PyMem_Malloc(0)`` returns a distinct
    non-NULL pointer, so that case needs no branch of its own.

    *release* drops what the wrapper already holds (parsed input arrays)
    on the two failure paths before the kernel runs. The product
    ``capacity * sizeof(T)`` is bounded first (gh-1710's emitter), so a huge
    capacity raises ``OverflowError`` instead of allocating a short buffer.

    Returns
    -------
    tuple of str
        The C that declares ``results`` (and, with a capacity, ``_cap``);
        the capacity expression to pass as ``max_results`` and to bound the
        returned count with; and the statement that frees ``results``,
        empty for the stack array.
    """
    if not max_out_is_declared(doc_blocks, f"{c_fn}_max_out"):
        return (
            f"    {ret_disp} results[{max_results}];\n",
            str(max_results),
            "",
        )
    rel = release.replace("\n    ", " ").strip()
    rel_line = f"        {rel}\n" if rel else ""
    return (
        _coerce.output_size_c(
            "_cap",
            _max_out_call_c(c_fn, doc_blocks, count, state),
            who,
            rel,
            ctype="size_t",
            limit=f"(PY_SSIZE_T_MAX / sizeof({ret_disp}))",
        )
        + f"    {ret_disp} *results ="
        f" ({ret_disp} *)PyMem_Malloc(_cap * sizeof({ret_disp}));\n"
        "    if (!results) {\n"
        f"{rel_line}"
        "        return PyErr_NoMemory();\n"
        "    }\n",
        "_cap",
        "PyMem_Free(results);",
    )


def _capacity_exprs(
    pass_capacity: bool,
    exact_max_out: bool,
    fallback: str,
    max_out_state_only: bool = False,
    short_out: bool = False,
) -> "tuple[str, str]":
    """``(alloc_clamp, min_cap_expr)`` for a variable-output method (gh-805 §D).

    The two capacity decisions a variable-output wrapper makes — how much the
    internal path allocates, and how much an ``out=`` buffer must hold — are
    one question asked twice, and they must give the same answer or ``out=``
    under-validates relative to what the binding would have allocated itself.
    Derived together here rather than written out at the two call sites.

    ``pass_capacity``
        The kernel is handed the capacity and enforces the bound itself, so
        the exact value is trusted and there is no clamp — **provided the
        header's ``max_out`` can see the count** (gh-920, below).

    ``exact_max_out``
        gh-805 §D. The same trust, without changing the kernel's signature.
        The clamp goes; the **zero-guard stays**, because `_method` scaffolds
        ``return 0; /* placeholder */`` and a project that has not implemented
        the function yet must not allocate nothing and hand the kernel a
        buffer to overrun.

    neither
        ``max(max_out, n)``. The historical default and still the right one.

    **gh-920: a state-only ``max_out`` earns neither trust.** gh-607 shipped
    two halves — the exact allocation, and the count parameter that makes an
    exact answer *possible* — and a project can be in the seam: opted into
    ``pass_capacity`` while its header still declares the pre-gh-607
    ``max_out(state)``. A value that cannot see ``n`` cannot be a per-call
    bound, so trusting it exactly turns the caller's request into a silent
    truncation: doppler's ``NCO.steps_u32(393_216)`` returned 65536 samples
    and raised nothing, because a fixed internal cap was read as the answer to
    a question about *this* call. jm already knows the arity here (gh-761's
    ``max_out_is_state_only``), so it falls back to the clamped form rather
    than extending a trust the signature cannot carry. The kernel is still
    handed the true allocation, so the ``pass_capacity`` contract is intact —
    this decides how large the buffer is, never what the kernel is told.

    ``exact_max_out`` is deliberately *not* gated the same way: it is the
    author asserting the bound holds for any call, which is exactly the claim
    a state-only prototype cannot make on its own. Setting it is how a project
    that really does have a call-independent bound keeps the exact allocation.

    **gh-2230: ``short_out`` asks the second question differently, and only
    the second.** Everything above makes the ``out=`` minimum the allocation
    the binding would have made, so a caller's buffer is held to exactly what
    the kernel would otherwise have been given. That is right for a kernel
    that merely clamps: a short buffer silently drops output it owed. A
    *backpressure* kernel's contract is the reverse -- a short buffer is how
    the caller asks for less, the kernel writes what fits and takes only the
    input that produced it, and the object reports how much (doppler's
    Spectrogram, ``rows = s.push(x, out=buf); x = x[s.consumed:]``). Under
    ``short_out`` there is no minimum at all, so the expression is ``""``
    and the ``out=`` site checks nothing but the buffer's shape. The key
    requires ``pass_capacity`` (``_config._short_out_errors``), so the
    kernel is handed the caller's true ``PyArray_SIZE`` -- the bound it
    stops at -- and the allocating path, which has no caller buffer, is
    unchanged.

    **Why this is an assertion and not a derivation.** The obvious rule —
    "trust it when the prototype takes ``n_in``, since it was given the input
    size" — is wrong, and jm's own fixtures prove it: ``Nco().steps_ovf(n)``
    declares the length-bearing form, returns a fixed internal cap of 65536,
    and writes exactly the ``n`` the caller asked for. Dropping the clamp
    there reintroduces the gh-600 heap corruption. Arity says what the C
    function was *told*, never what it does with it, so only the author can
    say whether the answer is a true bound.

    Parameters
    ----------
    pass_capacity : bool
        The method hands the kernel its capacity (gh-138/gh-607).
    exact_max_out : bool
        The author asserts ``max_out`` bounds any call (gh-805 §D). Ignored
        when *pass_capacity* is set, which already implies it and is stronger.
    fallback : str
        C expression for the call's own length — ``_need`` at the allocation
        site, the lazy-fallback expression at the ``out=`` site.
    max_out_state_only : bool
        The header declares ``max_out(state)`` (gh-761), so the bound is
        provably call-independent and *cannot* be trusted as an exact one.
    short_out : bool
        The method declares ``short_out`` (gh-2230): an ``out=`` buffer has
        no minimum, so the second element is ``""``. The first is untouched.

    Examples
    --------
    >>> _capacity_exprs(False, False, "_need")[0]
    '    if (!_cap || _cap < _need) _cap = _need;\\n'
    >>> _capacity_exprs(False, True, "_need")
    ('    if (!_cap) _cap = _need;\\n', '        size_t _min_cap = _omax ? _omax : (_need);\\n')
    >>> _capacity_exprs(True, False, "_need")
    ('    (void)_need;\\n', '        size_t _min_cap = _omax;\\n')

    gh-920: the same method, whose header kept the pre-gh-607 prototype.

    >>> _capacity_exprs(True, False, "_need", max_out_state_only=True)[0]
    '    if (!_cap || _cap < _need) _cap = _need;\\n'
    >>> _capacity_exprs(True, True, "_need", max_out_state_only=True)[0]
    '    if (!_cap) _cap = _need;\\n'

    gh-2230: a backpressure kernel's ``out=`` has no minimum; its
    allocation is the one ``pass_capacity`` gives it.

    >>> _capacity_exprs(True, False, "_need", short_out=True)
    ('    (void)_need;\\n', '')
    """
    if pass_capacity and not max_out_state_only:
        alloc = "    (void)_need;\n"
        min_cap = "        size_t _min_cap = _omax;\n"
    elif exact_max_out:
        alloc = f"    if (!_cap) _cap = {fallback};\n"
        min_cap = f"        size_t _min_cap = _omax ? _omax : ({fallback});\n"
    else:
        alloc = f"    if (!_cap || _cap < {fallback}) _cap = {fallback};\n"
        min_cap = (
            f"        size_t _min_cap = _omax >"
            f" {fallback} ? _omax : ({fallback});\n"
        )
    return alloc, ("" if short_out else min_cap)


def _zero_bound_guard(
    c_fn: str,
    name: str,
    fallback: str,
    cleanup: str = "",
    bound: str = "_cap",
    pass_capacity: bool = False,
) -> str:
    """Refuse a zero allocation bound, for the one shape that has no floor.

    gh-1085. Every ``variable_output`` binding allocates behind a floor::

        size_t _need = <the call's own length>;
        size_t _cap  = <m>_max_out(...);
        if (!_cap || _cap < _need) _cap = _need;

    and that floor is what makes ``max_out()`` returning **0** safe — jm's own
    docs call a zero legal and say the binding then sizes the allocation from
    the call itself.

    For every shape but one, ``_need`` is an independent quantity: an input
    array's length, or the synthesized count. **An all-scalar-params method has
    no call length**, so gh-607 made ``_need`` fall back to ``max_out()``
    itself — and the two sides of the floor become the same expression, leaving
    a guard that cannot fire.

    Measured, compiled and run: a kernel writing four floats behind a
    placeholder ``return 0;`` got a **zero-length** array, wrote past it, and
    the caller received ``[0. 0. 0. 0.]`` — right shape, lost values, no error,
    because ``PyArray_Resize`` had reallocated underneath. At 4096 samples
    glibc aborts with ``realloc(): invalid next size``.

    So this shape's only bound is ``max_out()``, and when it is unknown jm
    genuinely cannot size the buffer. Saying so is the whole fix: it converts a
    heap overflow into a message naming the function to implement.
    ``exact_max_out`` does not rescue it — it keeps the same no-op zero-guard.

    Emitted **only** where the floor is inert, which is exactly where
    *fallback* is the ``max_out`` call. Anywhere else a zero is already
    rescued, and refusing there would break the documented "0 means unknown"
    contract.

    **gh-1091: ``pass_capacity`` is the one flag that does rescue it, and this
    said the opposite.** The original reading was that ``pass_capacity``
    "drops the floor entirely (a bounds-checking kernel then writes nothing
    and the caller silently gets an empty array)" — describing the empty array
    as the silent failure. It is the correct answer. The whole premise of the
    guard is that jm cannot bound a write the kernel performs blind; a
    ``pass_capacity`` kernel is **handed** its capacity and must clamp to it,
    so ``0`` means "write nothing", the kernel writes nothing, and the binding
    returns the empty array. There is no unbounded write to refuse.

    doppler's ``Telemetry.read()`` is the shape that proves it: a non-blocking
    drain of an SPSC ring whose ``max_out(state)`` answers "how many are
    available right now", and whose steady state — an empty ring polled in a
    loop — is exactly ``0``. Refusing it turned the *designed* case into a
    ``RuntimeError``, and the only project-side escape was to redefine
    ``max_out`` as a worst case, contradicting the C contract and allocating
    the whole ring on every poll.

    The kernel is handed the capacity on **both** paths, which is what makes
    this safe to drop rather than merely convenient: the allocating path
    passes ``_cap``, and the ``out=`` path passes the caller's own
    ``PyArray_SIZE``. gh-920's "inert" ``pass_capacity`` does not weaken it —
    that decides how large the buffer is, never what the kernel is told.

    Parameters
    ----------
    c_fn : str
        The C symbol, for the ``max_out`` name in the message.
    name : str
        The Python method name, which is what the caller typed.
    fallback : str
        The ``_need`` expression. The guard is emitted iff this is the
        ``max_out`` call — derived, so a future shape that grows a real length
        is not gated by accident.
    cleanup : str
        Statements releasing anything acquired earlier in the wrapper.
    pass_capacity : bool
        The kernel is handed its capacity and clamps to it (gh-138/gh-607).
        No guard is emitted: a zero is a *value* ("write nothing"), not the
        absent bound this refuses. See gh-1091 above.
    bound : str
        The C local holding the BOUND. ``_cap`` on the allocating path, where
        the two are the same thing — but ``_omax`` in the ``out=`` branch,
        where ``_cap`` is the *caller's buffer size* and testing it would ask
        "did you pass an empty array?" instead of "can jm size this at all?".
        Measured: with the default the `out=` path accepted a 4096-element
        buffer against a zero bound and ran the kernel.

    Returns
    -------
    str
        C source, or ``""`` when this shape already has a working floor.
    """
    if pass_capacity:
        return ""
    if "_max_out(" not in fallback:
        return ""
    return (
        f"    if (!{bound}) {{\n"
        "        PyErr_Format(PyExc_RuntimeError,\n"
        '            "%s() cannot size its output: %s() returned 0, and this '
        "method has no input length to fall back on. Implement it to return "
        'the worst-case count.",\n'
        f'            "{name}", "{c_fn}_max_out");\n'
        f"        {cleanup}return NULL;\n"
        "    }\n"
    )


def make_methods_ctx(
    component: str,
    Component: str,
    methods: list[dict],
    pkg: str = "",
    py_create_args: str = "",
    no_state: bool = False,
    doc_blocks: dict | None = None,
    serializable: bool = False,
    codecs: dict | None = None,
    builtin_members: "frozenset[str]" = frozenset(),
    enums: dict[str, list[str]] | None = None,
    module_dir: str = "",
    records: "list[dict] | None" = None,
    properties: "list[dict] | None" = None,
    *,
    csym: str,
    extra_methods: "list[dict] | None" = None,
    host: "MethodHost | None" = None,
) -> dict[str, str]:
    """Generate template context keys for extra named methods.

    Each method dict has: name, arg_type ("void" or a _CTYPE_META key),
    return_type (a _CTYPE_META key), variable_output (bool),
    batch (bool), and optionally multi_output (list of additional return
    ctypes).

    batch=True generates a 1:1-rate array method:
      C: void comp_name(state_t *, [const arg_t *in,] size_t n, ret_t *out)
      Python: allocates output array each call with PyArray_SimpleNew.

    pkg and py_create_args are used in the generated PyMethodDef docstrings
    to produce working doctests; omitting them produces functional but
    package-anonymous examples. *module_dir* is
    `_config.module_package_resolved`'s answer when the component lives in a
    module — without it the synthesized example says
    ``from <pkg> import <Component>``, which is right for a standalone object
    and does not import for a module one (gh-1208). It is not the module id,
    which names the wrong package once the module declares ``package``
    (gh-2054).

    enums (gh-1021) is the ``[[enum]]`` SSOT, read only to write a REAL choice
    into a generated doctest for an enum parameter. The C those parameters
    emit needs no registry — the table symbol is derived from the name — so
    ``None`` costs the examples their choice and nothing else, which is what
    the `jm bind` path (no manifest) wants.

    builtin_members (gh-994) names the members whose C symbol the object's own
    generated code kept — a method entry that *names* a built-in rather than
    replacing it. This loop emits no glue for those, so the built-in's wrapper,
    PyMethodDef row and `.pyi` entry remain the only ones. Derived from the
    tree by :func:`just_makeit._builtins.builtin_owned_members`; the default
    empty set is right for every caller with no methods to place, and for a
    project where no method names a built-in.

    extra_methods (gh-1997) are the object's ``[[<obj>.extra_methods]]``
    rows: methods whose CPython function the author writes in the object's
    ``_extra.c``. Each gets its ``PyMethodDef`` row after the declared
    methods' and its ``.pyi`` member, both from :mod:`._extramethods` (the
    emitter gh-1190's composer rows use), and the forward prototypes come
    back in ``extra_method_protos`` -- placed by the caller above the type,
    since a module declares them in its aggregator, before the fragment, so
    a SACRED fragment's table can name the function too. A ``manual_stub``
    method of the same name keeps its hand-owned ``.pyi`` text; the row's
    member replaces only its placeholder.

    host (gh-2259) renders the wrappers onto another type -- a composer's
    generating source delegating its generator's methods -- through
    :class:`MethodHost`. ``None`` is the object itself. The C a wrapper is
    built around moves, and its error messages name ``host.who``;
    *Component* still names the class in every doc and ``.pyi`` member,
    whose ``Raises`` gain ``host.raises``, so a delegated method reads as
    the generator's own plus what building the generator can raise.
    """

    _EMPTY: dict = {
        "method_decls": "",
        "extra_buf_fields": "",
        "extra_buf_free": "",
        "extra_buf_alloc": "",
        "extra_methods_c": "",
        "extra_methods_pymethoddef": "",
        "pyi_extra_methods": "",
        "pyi_records": "",
        "bench_methods_timing_block": "",
        # gh-840: nothing to time, so no helper, no timer locals, and a TODO
        # that says which is which. Set here as well as at the real return
        # below — this branch renders the same template.
        "bench_elapsed_helper": "",
        "bench_timer_decls": "",
        "bench_todo": _bench_todo(component, [], csym=csym),
        "varargs_binding_files": [],
        "extra_method_protos": "",
    }
    if not methods and not serializable and not extra_methods:
        return _EMPTY
    # gh-1997: the rows the author's `_extra.c` implements, by Python name.
    _extra_names = {str(r.get("name") or "") for r in extra_methods or ()}

    wrapper_prefix = f"{Component}Obj" if no_state else Component

    guard = (
        "    if (!self->handle) {\n"
        '        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
        "        return NULL;\n"
        "    }\n"
    )
    # gh-2259: `self` and its state, spelled once for every wrapper below.
    # `_self_C` is what `<x>Object *self` is built from; `_st` is the state
    # pointer each kernel call is handed; `_who` leads every error message;
    # `_host_raises` is what the guard adds to each documented `Raises`.
    _self_C, _st, _who = Component, "self->handle", Component
    _host_raises: "list[tuple[str, str]]" = []
    if host is not None:
        wrapper_prefix, _self_C, _st, guard = (
            host.prefix,
            host.self_class,
            host.state,
            host.guard,
        )
        _who, _host_raises = host.who, list(host.raises)

    decl_lines: list[str] = []
    buf_fields: list[str] = []
    # gh-1426 A: the record a release defaults its count from. One field
    # per OBJECT, not per borrow: two borrows share one release, so what
    # is outstanding is 'the last view handed out', whichever lent it.
    #
    # Keyed on having a BORROW, not on something releasing it. Gated on
    # the release, DECLARATION ORDER decided the answer: a borrow
    # rendered before anything released it never learned to record, and
    # a sacred fragment only gains MISSING members -- so declaring the
    # release afterwards left a field read twice, zeroed twice and
    # written never, and every `wait(n); consume()` raised at runtime
    # from a project whose every command printed `Done!`. One store per
    # lend removes the state rather than detecting it (gh-1426).
    if any(_borrow.is_borrow(_m) for _m in methods):
        buf_fields.append(
            f"    size_t {_borrow.RELEASE_FIELD};  /* last borrow's count */\n"
        )
    buf_free: list[str] = []
    buf_alloc: list[str] = []
    method_c_parts: list[str] = []
    pmd_lines: list[str] = []
    pyi_lines: list[str] = []
    varargs_binding_files: list[str] = []
    # gh-994. A method entry may name something the object's own generated
    # code already provides, and there are two very different reasons to write
    # one. doppler declares `reset` in 28 objects purely to *describe* the
    # Python surface the built-in already binds; a `reset(start)` or a `steps`
    # promoted to a variable-output method *replaces* it. `builtin_members`
    # names the first kind — the members whose C symbol the built-in kept —
    # and for those this loop emits nothing at all, because the built-in's own
    # wrapper, PyMethodDef row and `.pyi` entry are already there and a second
    # set of each is a redefinition rather than a member.
    #
    # It also settles gh-131, which has been reading this question one method
    # name too coarsely: suppressing the built-in `reset` glue is right for the
    # entry that replaces it and wrong for the entry that merely names it,
    # which would otherwise be left with no binding at all.
    user_has_reset: bool = any(
        m["name"] == "reset" and m["name"] not in builtin_members
        for m in methods
    )

    for m in methods:
        name: str = m["name"]
        if name in builtin_members:
            continue
        # gh-805 §A2: the C symbol this method binds. Derived from the
        # component by default — `fn` overrides it so existing C with its own
        # established prefix can be adopted without renaming a public API, and
        # so a hot-path function and its validating variant can coexist
        # (`name = "emit"`, `fn = "dp_tlm_emit_checked"`).
        #
        # Resolved ONCE, here, and used for every C symbol this method forms
        # below — including the `_max_out` companion, so the pair cannot
        # disagree. `name` remains the PYTHON face throughout: the PyMethodDef
        # row, the .pyi and the docstrings are unaffected by `fn`.
        #
        # `fn` is already the spelling on properties, getters, setters,
        # composer fields and handle methods; this is that key reaching one
        # more place, not a new concept.
        c_fn: str = C.method_c_symbol(csym, m)
        # gh-2327: one owner slot per retained capsule param, released by
        # dealloc after the C state it was lent to is destroyed. `_keep` is
        # placed where the call is known to have succeeded; `_retain.errors`
        # refuses the shapes that have no such single place.
        buf_fields.append(_retain.fields_c(m))
        buf_free.append(_retain.free_c(m))
        _keep = _retain.swap_c(m)

        # Summary precedence: TOML `doc` override > header @brief > name
        # fallback. This one is still resolved here because the *runtime*
        # PyMethodDef doc is brief-only (gh-642 is the parity ask); the .pyi's
        # param/return prose is resolved inside render_numpy_doc.
        #
        # Keyed on the C symbol, not the derived name: the header documents
        # the function actually declared, so an `fn`-overridden method finds
        # its Doxygen block under `fn`. Same fallback shape `_handle.py`
        # already uses for its own `fn`-carrying methods.
        # gh-1396: the one chain, shared with the `.pyi` writer and with
        # `jm status --docs`. It also drops a scaffold block, which this face
        # did not: `_load_doc_blocks` filters those on the way in, so the two
        # faces agreed in practice and disagreed only for a block arriving
        # another way -- where the stub's answer (gh-666: jm's own template
        # is not documentation) is the right one.
        _block = (doc_blocks or {}).get(c_fn) or (doc_blocks or {}).get(
            f"{csym}_{name}"
        )
        _brief_text, _brief_is_stub = method_doc(
            component, m, doc_blocks, csym=csym
        )
        # The faces below distinguish "documented" from "fell back to the
        # name", so keep the empty-string spelling they were written against.
        _brief = "" if _brief_is_stub else _brief_text

        # ── varargs method (*args, **kwargs) ─────────────────────────────
        if m.get("varargs"):
            # gh-1881: the file `_method` WRITES, named by the one helper.
            # It was `f"{c_fn}_core.c"` -- the C symbol, which a `c_prefix`
            # moves and a file name never follows -- so a prefixed module's
            # `Python3_add_library` listed a file that does not exist.
            binding_file = C.varargs_core_c(component, name)
            varargs_binding_files.append(binding_file)
            extern_decl = (
                f"/* varargs binding — body in {binding_file} */\n"
                f"extern PyObject *\n"
                f"{c_fn}"
                f"(PyObject *, PyObject *, PyObject *);\n"
            )
            method_c_parts.append(extern_decl)
            # gh-1040: `_brief` is resolved four lines up — manifest `doc`,
            # else the header `@brief` — and both faces used to throw it away
            # and can the summary from the method's own name. A method whose
            # binding jm does not write is exactly the one whose purpose jm
            # cannot infer, so this is the shape that needs the author's
            # sentence most.
            #
            # Rendered through the same two functions every other shape uses,
            # not spelled locally: `render_runtime_doc` and `render_numpy_doc`
            # share their section builder, which is what makes the runtime
            # `__doc__` and the `.pyi` unable to drift (gh-642, gh-651).
            #
            # The params list is empty and the return is `Any` on purpose. A
            # varargs method's arguments are unknown to jm by definition —
            # that is what the flag means — so there is nothing to put in a
            # `Parameters` section, and inventing entries for `*args` /
            # `**kwargs` would document the mechanism rather than the method.
            # Everything the header DOES say — extended description, Returns,
            # Examples — comes through.
            _va_summary = _brief or name_summary(name)
            _va_runtime = render_runtime_doc(
                _block, name, [], "Any", _va_summary
            )
            pmd_lines.append(
                f'    {{"{name}",'
                f" (PyCFunction)(void *){c_fn},"
                f" METH_VARARGS | METH_KEYWORDS,\n"
                f"     {_build_ml_doc([f'{name}(*args, **kwargs)', '', *_va_runtime])}}},\n"
            )
            pyi_lines.append(
                f"    def {name}(self, *args: Any, **kwargs: Any)"
                f" -> Any:\n"
                + "\n".join(
                    render_numpy_doc(_block, name, [], "Any", _va_summary)
                )
                + "\n"
            )
            continue

        # ── manual_stub method (hand-written C binding, jm emits nothing
        # C-side; only a placeholder .pyi entry the splice engine preserves
        # verbatim across regen — gh-428) ─────────────────────────────────
        if m.get("manual_stub"):
            # gh-1997: an `extra_methods` row of the same name registers the
            # binding and declares its signature, so its member stands in
            # for the placeholder -- one member, not two. The text stays
            # hand-owned: the splice still carries a written stub over it.
            if name in _extra_names:
                continue
            pyi_lines.append(
                f"    def {name}(self, *args: Any, **kwargs: Any)"
                f" -> Any:\n"
                f'        """<<MANUAL_STUB>> hand-write this signature/'
                f"docstring in the .pyi — jm preserves it verbatim on"
                f' future regens."""\n'
            )
            continue

        # ── codec-pack method (variant-typed input; gh-554) ──────────────
        # jm generates the whole binding — parse, per-code pack of a
        # scalar-or-sequence, the sink call, and rc->error — from the
        # declared `[codec.X]` table. No hand marshaler.
        if _codec.is_codec_method(m):
            cdc = (codecs or {}).get(m["codec"])
            if cdc is None:
                raise _codec.CodecError(
                    f"{component}.{name}: method references codec "
                    f"'{m['codec']}', which is not declared in [codec.*]."
                )
            c_body, pmd, pyi = _codec.render_pack(
                component, _self_C, wrapper_prefix, m, cdc, guard, _st
            )
            method_c_parts.append(c_body)
            pmd_lines.append(pmd)
            pyi_lines.append(pyi)
            continue

        # gh-1404: a declared SCALAR element's name stands for its width.
        # Substituted HERE, in the render context, rather than in
        # `C.methods` -- `_apply` feeds that accessor straight back into
        # `_method.run`, so resolving there would write the width into the
        # manifest on every apply and restate per member exactly what the
        # declaration removed (the gh-805 shape).
        arg_type: str = _record.resolve_element(
            m.get("arg_type", "void"), records
        )
        return_type: str = _record.resolve_element(
            m.get("return_type", "float _Complex"), records
        )
        variable_output: bool = m.get("variable_output", False)
        batch: bool = m.get("batch", False)
        multi_output: list[str] = m.get("multi_output", [])
        params: list[dict] = m.get("params", [])
        result_fields: list[dict] = m.get("result_fields", [])
        max_results: int = int(m.get("max_results", 64))
        # gh-244: return ONE named record (PyStructSequence) rather than a
        # list[tuple]. The C kernel returns the record struct by value.
        single_record: bool = m.get("single", False)
        # gh-257 (`record_name`, the chosen public name) and gh-261
        # (`record_module`, the qualifier for the structseq's __module__) are
        # both read straight off `m` by `_record`, which the .pyi writers share
        # — see the descriptor emit below.
        # gh-2183: read through the one predicate the annotations and both
        # doc faces ask, not off the key -- so every `Py_RETURN_NONE` this
        # loop emits for an empty result is one an annotation says `| None`
        # for. Inside the variable-output and borrow branches, its only
        # readers, it is the key itself.
        none_on_empty: bool = empty_is_none(m)
        # gh-1426 B: refuse a wrong-dtype / non-1-D / strided INPUT rather
        # than casting, copying and flattening it. The scalar path was the
        # only array surface still converting silently -- `out=` (gh-581)
        # and a record input already refused.
        strict_in: bool = bool(m.get("strict"))
        # gh-1159: the other reading of an empty result. `none_on_empty`
        # says "nothing to report"; this says "the kernel REFUSED". A
        # variable_output method has no status to carry -- its one return
        # value is the length -- so without this a kernel that validates
        # its input and returns 0 produced a well-formed empty array and
        # the caller carried on. For a framing object that is the worst
        # available answer: the frame comes back short, nothing raises,
        # and it surfaces far away as a bad decode.
        error_on_empty: bool = m.get("error_on_empty", False)
        # Opt-in GIL release around the pure-C kernel (thread-per-shard
        # scaling). v1 covers the variable_output execute shapes.
        nogil: bool = m.get("nogil", False)
        # gh-432: the C `int` return is a status code (0 = OK, non-zero =
        # failure) — bound as `-> None`, raising ValueError on failure (the
        # same contract the serializable set_state glue emits). Fixed-output
        # methods only.
        status_return: bool = m.get("status_return", False)
        # gh-805 §B: the int return is a VALUE unless it is negative, in
        # which case it is an error code — `open`/`read`/`snprintf` and every
        # registry-style lookup work this way. Distinct from `status_return`,
        # which claims the whole int: here a successful call still has a
        # number to give back, so the `.pyi` return annotation is unchanged
        # and only the failure path differs. The CLI rejects the two together.
        error_negative: bool = m.get("error_negative", False)
        # The exception category and text for that failure. `error` reuses
        # gh-482's ERROR_CATEGORIES (already validated at declaration time),
        # so this renders a name jm has vetted rather than arbitrary C.
        #
        # gh-869: resolved ONCE, by `declared_raise`, and read by the emitter
        # below *and* by both doc faces. They used to be two readings of the
        # same four keys — one that emitted `PyErr_Format(PyExc_ValueError,
        # ...)` and one that documented nothing at all, so the two doc faces
        # agreed there was no exception over a binding that raises one.
        _raise_pair = declared_raise(m)
        _raises_doc = raises_doc(m) + _host_raises
        # gh-138: opt into the 5-arg `(..., out, size_t max_out)` form for a
        # variable_output method whose C API forwards an explicit output
        # capacity (the buffer cap jm already tracks for grow-on-demand).
        pass_capacity: bool = m.get("pass_capacity", False)
        # gh-805 §D: the author asserts `max_out` is a true upper bound for
        # any call, so the binding allocates exactly it instead of clamping up
        # to the call's own length. See `_capacity_exprs` for why this is an
        # assertion rather than something jm can derive.
        exact_max_out: bool = m.get("exact_max_out", False)
        # gh-2233: a void-input variable_output method that takes NO count.
        # The kernel is `fn(state, out[, max_out])`, its `_max_out(state)`
        # takes the state alone, and the Python call is `f()`. Validated at
        # load (`_config._no_count_errors`), so here it only selects shapes.
        no_count: bool = bool(m.get("no_count", False))
        # gh-788 gap 1: the method writes rows of a POD C struct, and the
        # result is a numpy STRUCTURED array whose dtype IS that struct's
        # layout. The key names the struct (`dp_tlm_rec_t`); `result_fields`
        # names its members. Everything downstream then treats the struct as
        # the output element type, so the C prototype (`dp_tlm_rec_t *out`)
        # and the data-pointer cast fall out of the existing variable_output
        # machinery -- only the numpy allocation differs, because a struct
        # has no `_CTYPE_META` entry and so no single NPY_ enum.
        record_dtype: str = str(m.get("record_dtype", "") or "").strip()
        # gh-1312: a borrow returns a pointer into the state's own memory.
        # The element type is the method's `return_type` -- there is no
        # out-parameter to name one -- and the view's length is a declared
        # param, resolved by `_borrow.count_param` so the CLI, the binding
        # and both .pyi writers ask one place. The count is cast at the C
        # level because the param may be any integer type.
        # gh-1426 A: a release's count is optional -- omitted, it takes
        # the outstanding borrow's. Injected HERE and not persisted: the
        # manifest must not carry a default jm derives, and `default`
        # is what puts the param after the `|` in the PyArg format.
        if _borrow.is_release(m) and _borrow.release_count_param(m):
            _rc_name = _borrow.release_count_param(m)
            m = dict(m)
            m["params"] = [
                (
                    {**_p, "default": "0", "stub_default": "..."}
                    if _p.get("name") == _rc_name
                    else _p
                )
                for _p in (m.get("params") or [])
            ]
            params = m["params"]
        borrow: bool = _borrow.is_borrow(m)
        borrow_writeable: bool = _borrow.is_writeable(m)
        # gh-1310: with `record_dtype` the element is the author's POD
        # struct, so it names the pointer type AND the view's dtype -- which
        # is a constructed descr, not an `NPY_*` enum.
        _borrow_elem = (
            record_dtype
            if (borrow and record_dtype)
            else (
                return_type[:-2] if return_type.endswith("[]") else return_type
            )
        )
        _borrow_count_c = f"({_borrow.count_param(m)})" if borrow else ""
        if borrow and not record_dtype:
            # Was `.get(..., "NPY_CFLOAT")`. Unreachable then -- both front
            # doors refuse an unregistered element type first -- but a
            # fallback on a lookup miss is the `_PYBUILD_FMT` shape that cost
            # a silent `ptrdiff_t` truncation (see `_types.py`), and gh-1310
            # adds the first caller that can reach this resolver with a type
            # that is deliberately NOT in `_CTYPE_META`. A complex64 view
            # over int16 data is 4x the itemsize and reads past the mapping
            # with no error, so this fails loudly instead.
            _borrow_py_type = _CTYPE_META.get(_borrow_elem, {}).get(
                "py_type", ""
            )
            if _borrow_py_type not in _NP_ENUM:
                raise KeyError(
                    f"method '{name}': borrow element type "
                    f"{_borrow_elem!r} has no numpy enum. A record element "
                    f"type needs `record_dtype`; a builtin one must be "
                    f"registered in `_CTYPE_META`."
                )
            _borrow_np_enum = _NP_ENUM[_borrow_py_type]
        else:
            _borrow_np_enum = ""
        # gh-657: a void-input variable_output method's `count` is the whole
        # user-facing knob — its default IS the method's zero-arg behaviour.
        # jm's own `1` was inert until gh-607 started feeding that count to
        # max_out() and dropped the clamp that had been rescuing it, at which
        # point `obj.ptr()` silently went from "snapshot everything" to "give
        # me one sample". jm cannot derive the natural capacity (it lives in
        # the user's C), so the manifest declares it.
        _count_default: str = str(m.get("count_default", "") or "").strip()
        _count_init, _count_alias = _count_default_parts(
            _count_default,
            component,
            csym=csym,
            state=_st,
        )
        # gh-1074: what that synthesized argument is CALLED. Read once and
        # used at every site below — the name was spelled seven times, which
        # is the shape a drifted copy is found in.
        _count_kw = _gluedoc.count_kwarg_name(m.get("count_name", ""))
        _cap_param = ", size_t max_out" if pass_capacity else ""
        # Placeholder that the three `call_data` builders below drop into the
        # output-argument slot; each emit site then substitutes whatever it
        # actually writes into (a per-call NumPy allocation, or the caller's
        # `out=` array). Since gh-604 there is no instance buffer of this name
        # — the token is purely a splice point, so it is defined once here
        # rather than spelled out at five call sites.
        _VO_BUF_TOKEN = f"self->_{name}_buf"
        # The translation-unit-unique prefix for this method's file-scope
        # statics. Shared by the structseq descriptor (gh-244) and the
        # structured dtype (gh-788) so both name their statics the same way
        # and `_docsync`/`_apply` have one shape to look for.
        _sid = f"{wrapper_prefix}_{name}"
        # gh-1310: derived from `_sid`, so it is defined HERE rather than
        # beside the other borrow slots above -- `_sid` is not set until this
        # line, and an f-string reading it earlier is an UnboundLocalError the
        # moment a borrow declares a record.
        _borrow_descr_fn = (
            f"{_sid}_get_dtype" if (borrow and record_dtype) else ""
        )

        ret_disp = return_type
        _ret_elem = (
            return_type[:-2] if return_type.endswith("[]") else return_type
        )
        ret_meta = _CTYPE_META.get(_ret_elem)
        ret_np = _NP_ENUM.get(ret_meta["py_type"]) if ret_meta else "NPY_FLOAT"

        out_type: str | None = m.get("out_type")
        out_divisor: int = int(m.get("out_divisor", 1))
        # The variable-output buffer holds *elements*, so a `T[]` return type
        # (or out_type) must be reduced to its element type `T`; otherwise the
        # buffer field, the `*out` param, sizeof(), and the NumPy enum all
        # render the invalid `T[] *out` / `sizeof(T[])` (gh-201 follow-up).
        # gh-788: a record_dtype names the output element outright -- it is
        # the struct the kernel writes rows of. It wins over `out_type` and
        # `return_type` so the `*out` parameter, the `sizeof`, the data
        # pointer cast and the `.pyi` all describe the same struct without
        # the manifest having to spell it three times.
        _vo_out_elem = _outbuf.element(
            variable_output=variable_output,
            record_dtype=record_dtype,
            borrow=borrow,
            out_type=out_type or "",
            return_type=return_type,
        )
        # gh-1885: `void` has no dtype, so the variable-output NPY-enum list
        # and the batch dtype string below would die on
        # `_CTYPE_META["void"]`. Both front doors refuse such a row
        # before writing it; this is where one ALREADY in a manifest (an
        # older jm wrote it) or built without one (`jm bind`, whose parser
        # spells every variable-output return `void`: gh-1894) meets the
        # render -- as the same refusal, not a traceback.
        _element_why = _outbuf.element_why_not(
            f"method '{component}.{name}'",
            variable_output=variable_output,
            batch=batch,
            record_dtype=record_dtype,
            borrow=borrow,
            out_type=out_type or "",
            return_type=return_type,
        )
        if _element_why:
            raise Refusal(_element_why)
        # gh-2233: `no_count` calls `<fn>_max_out(state)` with the state
        # alone, so a header still declaring it with a count does not compile
        # against the binding -- and the count it asks for does not exist.
        # Only the render reads the header, so this half of the key's rules
        # is refused here; the manifest half is `_config._no_count_errors`.
        if (
            no_count
            and max_out_is_declared(doc_blocks, f"{c_fn}_max_out")
            and not max_out_is_state_only(doc_blocks, f"{c_fn}_max_out")
        ):
            raise Refusal(
                f"method '{component}.{name}' declares no_count, but its "
                f"header declares {c_fn}_max_out() with a count. A no_count "
                f"method has no count to pass it: declare it as "
                f"`size_t {c_fn}_max_out({csym}_state_t *state)`."
            )
        _vo_out_disp = _vo_out_elem
        _vo_out_meta = _CTYPE_META.get(_vo_out_elem)
        _vo_out_np = (
            _NP_ENUM.get(_vo_out_meta["py_type"])
            if _vo_out_meta
            else "NPY_FLOAT"
        )
        has_params = bool(params)
        has_arg = arg_type != "void"
        # gh-1996: the unit every variable-output count is in -- the sizing
        # array's `elements_per_sample`. Read once, here, for the binding,
        # its `<m>_max_out` and both doc faces; refused before anything
        # renders when the output cannot be counted in it.
        _vo_e = 1
        if variable_output:
            _what = f"method '{component}.{name}'"
            _interleave_why = _outbuf.interleave_why_not(
                _what,
                has_arg=has_arg,
                params=params,
                out_elems=[_vo_out_elem]
                + [
                    rt[:-2] if rt.endswith("[]") else rt for rt in multi_output
                ],
            )
            if _interleave_why:
                raise Refusal(_interleave_why)
            _vo_e = _outbuf.interleave(has_arg, params, _what)
        # gh-642: the Python-facing argument list, resolved once here so the
        # runtime PyMethodDef literal each shape branch emits below and the
        # .pyi stub built at the end of this loop document the same arguments
        # with the same annotations. It used to be built only at the stub, so
        # the runtime face had nothing to share and carried the @brief alone.
        # gh-2259: a `batch` binding reads a BLOCK too, and parses `x` (or a
        # count) and `out=` and nothing else, so both faces describe exactly
        # that. A declared param never reaches it: whether it should, or be
        # refused, is gh-2021's.
        _sig_parts, _doc_params = _stub_params(
            arg_type, [] if batch else params, variable_output or batch, enums
        )
        # ...and the return annotation with it, for the same reason: it is the
        # type line of the `Returns` section on both faces.
        if batch:
            # gh-2259: an ndarray of the return element, one per input. First
            # because the binding renders a batch method ahead of every other
            # shape (`_outbuf.element_why_not`); it read `complex` here, and
            # the `.pyi` had no member at all.
            _ret_ann = _pyi_ndarray(return_type)
        elif borrow:
            # gh-1312: a borrow returns an ndarray VIEW of the element type,
            # not one element of it. Ahead of every other branch because the
            # shape owns its whole return; falling through annotated the
            # scalar `return_type` and told the reader `-> complex` for an
            # array. Peer of the same branch in `_stubs.py`, and the two are
            # gated to agree.
            # gh-1310: the element type, which `record_dtype` names when
            # the borrow carries one -- `_pyi_ndarray` then yields
            # `NDArray[Any]`, the same annotation the variable-output record
            # path uses, because a record type is not in `_CTYPE_META`.
            # gh-1418: `none_on_empty` makes NULL a normal answer, so the
            # view is optional and the stub must say so -- a caller that
            # cannot see `| None` writes `peek(n).mean()` and the checker
            # agrees with them right up until it returns None.
            # gh-2183: through `optional_ann`, which the variable-output
            # branch below and the module-aggregated peer ask too.
            _ret_ann = optional_ann(
                m,
                _pyi_ndarray(_borrow.element_type(record_dtype, return_type)),
            )
        elif result_fields and single_record:
            # gh-244/gh-646: one named record — a PyStructSequence (a tuple
            # subclass). This used to annotate as a bare tuple of the field
            # types, which types unpacking but leaves `r.enob` unknown to the
            # checker and undocumented to the reader. The stub now declares the
            # record class itself (see `pyi_records` below) and names it here.
            _ret_ann = _record.public_name(m)
        elif result_fields and not _record.is_record_array(
            variable_output, record_dtype, borrow
        ):
            # gh-788: a record_dtype method also carries `result_fields`, but
            # they describe the dtype's columns, not a list of per-row tuples
            # -- it returns ONE structured ndarray. Without this guard the
            # richer shape is shadowed by the older one that merely mentions
            # the same key.
            #
            # gh-1312: this used to read `not record_dtype`, dropping the
            # `variable_output` term its peer in `_stubs.py` carries. The two
            # agreed only because a `record_dtype` without `variable_output`
            # is refused up front (measured: both the CLI and `apply` reject
            # it), so the divergence was unreachable rather than harmless.
            # Both now ask `_record.is_record_array`, so they agree by
            # construction instead of by a validator in another file.
            _ret_ann = "list[tuple]"
        elif variable_output:
            # Only the record case reads the resolved element type here; the
            # `out_type` case is left reporting `return_type` exactly as
            # before, since changing it is a separate behaviour question and
            # not this issue's.
            _all_rts = [record_dtype or return_type] + list(multi_output)
            _ndarrays = [_pyi_ndarray(rt) for rt in _all_rts]
            # gh-2183: `none_on_empty` returns None on both of this shape's
            # routes, and the stub said the array was always there -- the
            # borrow branch above was the only one asking.
            _ret_ann = optional_ann(
                m,
                (
                    f"tuple[{', '.join(_ndarrays)}]"
                    if len(_ndarrays) > 1
                    else _ndarrays[0]
                ),
            )
        elif status_return or multi_output or out_type:
            # gh-529: `out_type` on a method allocates a fresh output array
            # per call and returns it -- the C wrapper (the `elif out_type`
            # branch below) does exactly what a function's out_type does, and
            # the PyMethodDef docstring already says `-> ndarray`. Only this
            # annotation lagged, reporting the scalar `return_type` and so
            # contradicting both. `_stubs._obj_stub` carries the peer of this
            # branch for the module-aggregated stub; the two must move
            # together (see tests/test_gh529_method_out_type_pyi.py).
            # gh-432: a status alone binds as None (raise on failure).
            # gh-2250: all three from the list the binding builds -- a
            # `multi_output` tuple read as the bare scalar here, and a
            # status beside an output had no binding to describe.
            _ret_ann = fixed_result_ann(m, _pyi_scalar, _pyi_ndarray)
        else:
            _ret_ann = _pyi_scalar(return_type)

        # The Python-facing argument names, for the signature line each shape
        # puts at the top of its runtime doc. Derived from the same list as
        # the Parameters section so the two cannot contradict each other —
        # which they did: the variable_output shape hard-coded `x` and
        # dropped every declared param, so a documented `run(x, gain)`
        # advertised `run(x)` directly above a Parameters block listing
        # `gain`. Same shape as the gh-657 report, where help() advertised
        # `ptr(n=1)` against a kwlist that bound `count`.
        _doc_names = [n for n, _ in _doc_params]
        _has_header_examples = bool(_block and _block.examples)

        def _demo(lines: list[str]) -> list[str]:
            """The synthesized doctest, dropped when the header wrote one.

            gh-642 renders the header's ``@code`` as a real ``Examples``
            section at runtime, so emitting jm's placeholder demo underneath
            it would put two example blocks in one docstring — the second one
            constructing the object a different way than the author just
            showed. The author's wins; jm's is the fallback it always was.

            It carries its own ``Examples`` heading (gh-869). It never did:
            the demo was appended as bare indented ``>>>`` lines, which read
            as a continuation of whatever section happened to end above it.
            While that was always the summary the ambiguity was invisible —
            but a declared exception now puts a ``Raises`` section there, and
            numpydoc reads four-space-indented lines under an entry as that
            entry's description. So the doctest rendered as part of the
            exception's prose. The heading is what the header-supplied
            examples already get one line up, in `render_runtime_doc`.
            """
            if _has_header_examples:
                return []
            # The demo's own leading blank separates it from the section
            # above; the heading takes that slot instead.
            body = lines[1:] if lines and not lines[0].strip() else lines
            return ["", "Examples", "--------", *body]

        def _runtime_doc(default_summary: str) -> list[str]:
            """This method's runtime numpy block, summary resolved.

            Closes over the loop's per-method state so each shape branch below
            passes only what differs: the sentence to use when neither the
            manifest nor the header supplies one. Precedence is unchanged —
            TOML ``doc`` > header ``@brief`` > *default_summary* — because
            ``_brief`` already resolved the first two.
            """
            return render_runtime_doc(
                _block,
                name,
                _doc_params,
                _ret_ann,
                _brief or default_summary,
                raises=_raises_doc,
                none_when=empty_is_none_doc(m),
                param_defaults=_gluedoc.binding_param_docs(
                    _count_kw,
                    batch=batch,
                    count=_stub_count_arg,
                    out=_stub_enable_out,
                    out_cols=bool(m.get("out_cols")),
                    short_out=bool(m.get("short_out")),
                ),
                authored_doc=m.get("doc") or "",
                param_docs=authored_param_docs(m),
            )

        # gh-1042: the binding's own arguments, decided ONCE and read by both
        # faces. They were decided several hundred lines below, where only the
        # `.pyi` could see them, so the signature listed `count`/`out=` while
        # the `Parameters` section documented neither -- and a `variable_output`
        # method with `arg_type = "void"` has no other parameter, so it
        # rendered a two-argument signature above no section at all.
        #
        # Appended to `_doc_params` rather than kept beside it, because that
        # list is what filters the header's `@param` entries: a name absent
        # from it is never looked up, so an authored `@param count` was
        # silently discarded and no authoring move could fix the above.
        _stub_count_arg = _gluedoc.synthesizes_count(
            variable_output=variable_output,
            arg_type=arg_type,
            params=params,
            no_count=no_count,
        )
        _stub_enable_out = _outbuf.enabled(
            variable_output=variable_output,
            multi_output=bool(multi_output),
            has_arg=has_arg,
            params=params,
        )
        if batch:
            # gh-2259: a batch binding's own arguments (gh-222): `out=`
            # always, and the leading count when there is no input -- the
            # generator's `|nO` over {count, out}.
            _stub_count_arg, _stub_enable_out = not has_arg, True
        if _stub_count_arg:
            _doc_params = _doc_params + [(_count_kw, "int")]
        # gh-1724: the caller's buffer, spelled by the one helper as a
        # writable array of the element the binding writes.
        _out_ann = T.array_param_annotation(_vo_out_elem, writable=True)
        if _stub_enable_out:
            _doc_params = _doc_params + [("out", f"{_out_ann} | None")]
        _doc_names = [n for n, _ in _doc_params]
        _in_acq = ""
        _in_dtype_helper = ""
        _in_rec: dict = {}
        if has_arg:
            _arg_elem = arg_type[:-2] if arg_type.endswith("[]") else arg_type
            # gh-139: a block method's input is `const <elem> *in`. Use the
            # element display so an array arg_type (`T[]`) does not render the
            # invalid `const T[] *in` / `(const T[] *)` cast. Scalar arg types
            # are their own element type, so this is a no-op for them. (The
            # scalar `arg_disp x` decls below are only reached when arg_type is
            # not an array.)
            arg_disp = _arg_elem
            # gh-1405: rows of the author's struct, not a scalar element.
            # The struct name IS the C display, and there is no typenum --
            # the acquisition takes the record's cached descr instead.
            _in_rec = _record.input_record(arg_type, records)
            if _in_rec:
                arg_meta = None
                arg_np = ""
            else:
                arg_meta = _CTYPE_META[_arg_elem]
                arg_np = _NP_ENUM[arg_meta["py_type"]]
            # ONE acquisition emitter for every method shape below (gh-1405).
            # The record form needs its cached descr builder prepended to the
            # wrapper, the way the reading side already prepends its own --
            # the two travel together or the fragment does not compile.
            _in_acq = _coerce.input_array_acq(
                npy_enum=arg_np,
                dtype_fn=f"{_sid}_in" if _in_rec else "",
                strict=strict_in,
                # `arg_meta` is None for a RECORD element -- the author's
                # struct is not in `_CTYPE_META`, and `expect` names a
                # scalar dtype the record path never prints.
                expect=str((arg_meta or {}).get("py_type", "")),
                label="x",
            )
            # The builder is named for the PARAM it serves, because that is
            # what `_build_params_parse` emits the call under -- the primary
            # array arrives as the synthetic `x`. One per record param, so a
            # method taking two different records gets two.
            _in_dtype_helper = "".join(
                _record.dtype_c(
                    f"{_sid}_{_pn}",
                    str(_pr.get("name") or ""),
                    _record.declared_fields(_pr),
                )
                for _pn, _pr in ([("x", _in_rec)] if _in_rec else [])
                + [
                    (
                        str(_p.get("name") or ""),
                        _record.declared(
                            records,
                            array_elem_ctype(str(_p.get("type") or "")),
                        ),
                    )
                    for _p in params
                    if is_array_param_type(str(_p.get("type") or ""))
                ]
                if _pr
            )

        _ndecl = len(decl_lines)

        if not has_arg:
            _in_example = ""
            _in_dtype_str = ""
        elif arg_type.endswith("[]"):
            _elem = arg_type[:-2]
            _in_dtype_str = (
                _CTYPE_META[_elem]["py_type"]
                if _elem in _CTYPE_META
                else "np.float32"
            )
            _in_example = f"np.zeros(4, dtype={_in_dtype_str})"
        elif arg_type in _CTYPE_META:
            _in_dtype_str = _CTYPE_META[arg_type]["py_type"]
            _kind = _CTYPE_META[arg_type]["kind"]
            _in_example = _KIND_PY_TEST_VAL.get(_kind, "1")
        else:
            _in_dtype_str = "np.float32"
            _in_example = "x"
        _from_line = (
            ["    >>> " + class_import_line(pkg, Component, module_dir)]
            if pkg
            else []
        )
        _obj_line = f"    >>> obj = {Component}({py_create_args})"

        def _demo_call_args(block_input: bool = False) -> str:
            """The argument list for this method's synthesized doctest.

            One builder for every shape, because the example is executable
            prose: a wrong literal here reads as a working call. gh-1021 fixed
            the enum case (a bare ``0`` is the very TypeError that issue
            reports) in the fixed-output branch, which was then the only
            caller — a second copy would have shipped that bug again the day
            a record method declared an enum param.

            gh-2001: the variable-output and list-of-records examples did not
            call it, and each spelled its input alone -- ``delay(x, mu)``
            was shown as ``obj.delay(np.zeros(4))``, a TypeError for the
            missing ``mu``. *block_input* keeps the list-of-records input
            spelled as the block it is (``np.zeros(4, dtype=...)``) where
            the other shapes show one element.
            """
            parts: list[str] = []
            if has_arg:
                parts.append(
                    f"np.zeros(4, dtype={_in_dtype_str})"
                    if block_input
                    else (_in_example if _in_example else "x")
                )
            for _p in params:
                _pt = _p["type"]
                # gh-1426 A: a release's count resolves 0 to "the outstanding
                # borrow", and a synthesized demo has none -- so the type's
                # zero would generate `obj.consume(0)`, which RAISES. The
                # example is executable prose, and this project ships no
                # scaffold whose own doctest fails.
                if _borrow.is_release_count(m, str(_p.get("name", ""))):
                    parts.append("1")
                    continue
                if _p.get("enum"):
                    # Show a real choice: the declared default when there is
                    # one, else the enum's first (its C zero).
                    _choices = (enums or {}).get(_p["enum"]) or []
                    parts.append(
                        repr(
                            _p.get("default")
                            or (_choices[0] if _choices else "")
                        )
                    )
                elif _pt.endswith("[]"):
                    _pe = _pt[:-2]
                    _pe_str = (
                        _CTYPE_META[_pe]["py_type"]
                        if _pe in _CTYPE_META
                        else "np.float32"
                    )
                    parts.append(f"np.zeros(4, dtype={_pe_str})")
                elif _pt in _CTYPE_META:
                    parts.append(_CTYPE_META[_pt].get("py_zero", "0"))
                else:
                    parts.append("0")
            return ", ".join(parts)

        def _block_params() -> list[dict]:
            """``x`` as the block it is, then every declared param.

            A record or variable-output method's ``arg_type`` is a BLOCK
            input -- ``const T *in, size_t n_in`` in its prototype -- so it
            joins the param list in its array form whether or not the
            manifest spelled the ``[]``, under the Python name ``x``. The one
            list the single-record (gh-594), list-of-records (gh-1961) and
            variable-output (gh-1960) parses hand `_build_params_parse`, so
            the three read a declared param the way every other shape does.
            """
            _x_type = arg_type if arg_type.endswith("[]") else f"{arg_type}[]"
            return ([{"name": "x", "type": _x_type}] if has_arg else []) + [
                dict(_p) for _p in params
            ]

        # gh-581: every `out=` branch below requires the exact output dtype
        # before marshaling, so FROM_OTF cannot cast the caller's buffer into a
        # temp that the kernel fills and then discards. Two flavors: with an
        # input array already owned (release it on the reject path) and without.
        _out_guard_in = _coerce.out_buffer_guard(
            "out_obj",
            ret_np,
            decrefs="Py_DECREF(in_arr);",
            indent=" " * 8,
        )
        _out_guard = _coerce.out_buffer_guard(
            "out_obj", ret_np, indent=" " * 8
        )

        # ── declarations for _core.h ─────────────────────────────────────
        # gh-788: `record_dtype` reuses `result_fields` for the dtype's
        # columns, so it must not be captured by the list-of-records
        # prototype below -- its kernel has the ordinary variable_output
        # signature, writing struct rows into `dp_tlm_rec_t *out`. The
        # wrapper chain already prefers `variable_output`; before this the
        # two chains disagreed and the declaration described a kernel the
        # binding never called.
        if batch:
            # gh-179: a 1:1-rate transform -- n elements in, n out, nothing
            # returned (`_method._build_method_prototype`'s batch branch).
            decl_lines.append(
                f"void {c_fn}({csym}_state_t *state,"
                + (f" const {arg_disp} *in," if has_arg else "")
                + f" size_t n, {ret_disp} *out);"
            )
        elif result_fields and not _record.is_record_array(
            variable_output, record_dtype, borrow
        ):
            # gh-594: this is the peer of _method._build_method_prototype's
            # record branch and must render the identical signature -- params
            # expanded (array -> ptr + `_len`), and `single` returning the
            # record by value instead of the results[]/max_results out-params.
            # It previously did neither, so a record method declared here got
            # a prototype the binding could not call.
            # gh-1591: the symbol stem. `.format` hid this from the f-string
            # ratchet, so a record method's prototype kept the bare type.
            _rf_parts = [f"{csym}_state_t *state"]
            if has_arg:
                _rf_parts += [f"const {arg_disp} *in", "size_t n_in"]
            _rf_parts += c_param_parts(params)
            if single_record:
                decl_lines.append(
                    f"{ret_disp} {c_fn}({', '.join(_rf_parts)});"
                )
            else:
                _rf_parts += [f"{ret_disp} *result", "size_t max_results"]
                decl_lines.append(f"size_t {c_fn}({', '.join(_rf_parts)});")
        elif variable_output:
            extra_params = "".join(
                f", {rt} *out{i + 1}" for i, rt in enumerate(multi_output)
            )
            _moc_decl, _ = _max_out_count_param_ctx(
                has_arg, has_params, params, no_count
            )
            # gh-761: never re-declare a count over a header that says
            # otherwise. jm splices these back into the sacred `_core.h`, so
            # emitting the count form against a state-only implementation
            # rewrites the author's prototype out from under their code.
            if max_out_is_state_only(doc_blocks, f"{c_fn}_max_out"):
                _moc_decl = ""
            # gh-2012: the kernel returns its count in `count_type`, which
            # `_method._build_method_prototype` and the `_core.c` stub read
            # through the same accessor. `_max_out` stays `size_t`: it is a
            # capacity, never a refusal.
            _count_t = count_type_of(m)
            if has_arg:
                # gh-1960: the params follow the block input, as they do in
                # a record method's prototype. They were dropped here, in
                # `_method._build_method_prototype` and in the `_core.c` stub
                # alike, so the three agreed with each other and with no stub.
                _vp_in = "".join(f", {_p}" for _p in c_param_parts(params))
                decl_lines.append(
                    f"size_t {c_fn}_max_out"
                    f"({csym}_state_t *state{_moc_decl});\n"
                    f"{_count_t} {c_fn}"
                    f"({csym}_state_t *state,"
                    f" const {arg_disp} *in, size_t n_in{_vp_in},"
                    f" {_vo_out_disp} *out{extra_params}{_cap_param});"
                )
            elif has_params:
                # gh-1491: the one expansion, which knows a writable (`out`)
                # array param is not `const`.
                _vp_parts = c_param_parts(params)
                decl_lines.append(
                    f"size_t {c_fn}_max_out"
                    f"({csym}_state_t *state{_moc_decl});\n"
                    f"{_count_t} {c_fn}"
                    f"({csym}_state_t *state,"
                    f" {', '.join(_vp_parts)},"
                    f" {_vo_out_disp} *out{extra_params}{_cap_param});"
                )
            else:
                # gh-2233: `no_count` drops the `size_t n` the shape takes.
                _n_param = "" if no_count else " size_t n,"
                decl_lines.append(
                    f"size_t {c_fn}_max_out"
                    f"({csym}_state_t *state{_moc_decl});\n"
                    f"{_count_t} {c_fn}"
                    f"({csym}_state_t *state,{_n_param}"
                    f" {_vo_out_disp} *out{extra_params}{_cap_param});"
                )
        else:
            extra_params = "".join(
                f", {rt} *out{i + 1}" for i, rt in enumerate(multi_output)
            )
            out_type_param = f", {out_type} *out" if out_type else ""
            if has_params:
                p_parts: list[str] = []
                if has_arg:
                    if is_array_param_type(arg_type):
                        _e_disp = array_elem_ctype(arg_type)
                        p_parts.append(f"const {_e_disp} *x")
                        p_parts.append("size_t x_len")
                    else:
                        p_parts.append(f"{arg_disp} x")
                p_parts += c_param_parts(params)
                c_param_str = ", ".join(p_parts)
                decl_lines.append(
                    f"{ret_disp} {c_fn}"
                    f"({csym}_state_t *state,"
                    f" {c_param_str}{extra_params}{out_type_param});"
                )
            elif has_arg:
                if is_array_param_type(arg_type):
                    _e_disp = array_elem_ctype(arg_type)
                    decl_lines.append(
                        f"{ret_disp} {c_fn}"
                        f"({csym}_state_t *state,"
                        f" const {_e_disp} *x, size_t x_len"
                        f"{extra_params}{out_type_param});"
                    )
                else:
                    decl_lines.append(
                        f"{ret_disp} {c_fn}"
                        f"({csym}_state_t *state,"
                        f" {arg_disp} x{extra_params}{out_type_param});"
                    )
            else:
                decl_lines.append(
                    f"{ret_disp} {c_fn}"
                    f"({csym}_state_t *state"
                    f"{extra_params}{out_type_param});"
                )

        for _j in range(_ndecl, len(decl_lines)):
            _doc = scaffold_doc_block(decl_lines[_j], name)
            if _doc:
                decl_lines[_j] = _doc + "\n" + decl_lines[_j]

        # ── output storage ───────────────────────────────────────────────
        # gh-604: nothing to declare. Every variable_output shape now lets
        # NumPy own each call's arrays (see the wrapper below), so there is no
        # instance buffer to size at __init__, grow, retire, weakref-track or
        # free. That removed `_<name>_buf`, `_<name>_buf_cap`,
        # `_<name>_retired{,_n,_cap}` and `_<name>_view_ref` — the gh-219
        # deferred-free freelist and the gh-437 live-view probe existed solely
        # to make sharing one buffer across calls safe, and nothing is shared
        # any more. gh-600 had already done this for multi-output; the
        # benchmarks in gh-604 retired it for single-output too.
        # ── Python wrapper in ext.c ──────────────────────────────────────
        # gh-219: single-output variable_output methods accept an optional
        # `out=` buffer (zero-alloc, caller-owned, safe to retain) — parity
        # with blockwise steps(x, out=). Multi-output keeps its signature
        # without one; which shapes get it is `_outbuf.why_not`'s answer.
        # gh-805 §E: a structured result gets `out=` too. Three places in
        # this branch spoke in scalar NPY_ enums -- the guard, the
        # acquisition, and the trimmed view -- and each has a record form
        # below. A scalar enum cannot name a record layout (its type num is
        # NPY_VOID), and coercing to one would silently reinterpret the
        # caller's buffer, which is why this was carved out until the
        # descr-based path existed rather than shipped half-working.
        #
        # gh-1079: the SAME call as the two `.pyi` builders make. It used to
        # read `has_params` where they read `params`; the two agree today
        # (`has_params` is `bool(params)`), and agreeing today is exactly the
        # guarantee a shared accessor replaces with a structural one.
        _enable_out = _outbuf.enabled(
            variable_output=variable_output,
            multi_output=bool(multi_output),
            has_arg=has_arg,
            params=params,
        )
        # gh-412: keyword parsing is independent of the `out=` buffer feature.
        # A variable_output method with named params (e.g. Farrow.delay(x, mu))
        # is positional-OR-keyword — matching its `.pyi` and the fixed-output
        # path — even though it gets no `out=` buffer. Previously such methods
        # fell through to a positional-only PyArg_ParseTuple, so `delay(x,
        # mu=0.3)` raised TypeError despite the stub advertising the keyword.
        _enable_kw = _enable_out or (variable_output and has_params)
        # ── batch method ─────────────────────────────────────────────────
        # gh-179: a 1:1-rate transform. gh-222: it always takes an optional
        # `out=` -- written in place and returned, else allocated.
        #
        # gh-2259: first in this chain, as it was first in the loop, and no
        # longer a `continue` past the rest of it -- which skipped the `.pyi`
        # member below, so the method was missing from the stub. Its two
        # kernel calls go through `_nogil_call`, so `nogil` is honoured on
        # both paths: the input is acquired, `out=` validated and the result
        # allocated above the released block, under the GIL.
        if batch:
            _b_in = (
                f"(const {arg_disp} *)PyArray_DATA(in_arr),\n        "
                if has_arg
                else ""
            )

            def _b_call(out_arr: str, held: str) -> str:
                """The kernel call writing into *out_arr*, at body depth.

                *held* is the result array this path owns; the input is
                held too when the method takes one (gh-2273).
                """
                return _nogil_call(
                    "",
                    "",
                    f"{c_fn}({_st},\n        {_b_in}(size_t)n,\n"
                    f"        ({ret_disp} *)PyArray_DATA({out_arr}))",
                    nogil,
                    release=f"Py_DECREF({held});"
                    + (" Py_DECREF(in_arr);" if has_arg else ""),
                )

            _b_out_kernel = _reindent(_b_call("out_arr", "out_arr"))
            _b_new_kernel = _b_call("(PyArrayObject *)out", "out")
            _b_array_arg = _coerce.array_arg(
                "out_obj",
                ret_np,
                "NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_WRITEABLE",
                "out",
            )
            if has_arg:
                wrapper = _in_dtype_helper + (
                    f"static PyObject *\n"
                    f"{wrapper_prefix}_{name}"
                    f"({_self_C}Object *self,"
                    f" PyObject *args, PyObject *kwds)\n"
                    f"{{\n"
                    f"{guard}"
                    f'    static char *_kwlist[] = {{"x", "out", NULL}};\n'
                    f"    PyObject *in_obj = NULL;\n"
                    f"    PyObject *out_obj = NULL;\n"
                    f"    if (!PyArg_ParseTupleAndKeywords("
                    f'args, kwds, "O|O",\n'
                    f"            _kwlist, &in_obj, &out_obj))\n"
                    f"        return NULL;\n"
                    f"{_in_acq}"
                    f"    Py_ssize_t n = PyArray_SIZE(in_arr);\n"
                    f"    if (out_obj && out_obj != Py_None) {{\n"
                    f"{_out_guard_in}"
                    f"        PyArrayObject *out_arr ="
                    f"\n            {_b_array_arg};\n"
                    f"        if (!out_arr)"
                    f" {{ Py_DECREF(in_arr); return NULL; }}\n"
                    f"        if (PyArray_SIZE(out_arr) != n) {{\n"
                    f"            PyErr_Format(PyExc_ValueError,\n"
                    f'                "out length %zd != input length %zd",\n'
                    f"                (Py_ssize_t)PyArray_SIZE(out_arr),"
                    f" (Py_ssize_t)n);\n"
                    f"            Py_DECREF(out_arr);"
                    f" Py_DECREF(in_arr); return NULL;\n"
                    f"        }}\n"
                    f"{_b_out_kernel}"
                    f"        Py_DECREF(in_arr);\n"
                    f"        return (PyObject *)out_arr;\n"
                    f"    }}\n"
                    f"    npy_intp dims[] = {{n}};\n"
                    f"    PyObject *out ="
                    f" PyArray_SimpleNew(1, dims, {ret_np});\n"
                    f"    if (!out) {{ Py_DECREF(in_arr); return NULL; }}\n"
                    f"{_b_new_kernel}"
                    f"    Py_DECREF(in_arr);\n"
                    f"    return out;\n"
                    f"}}"
                )
            else:
                # gh-222: count-driven batch generator with optional `out=`.
                wrapper = _in_dtype_helper + (
                    f"static PyObject *\n"
                    f"{wrapper_prefix}_{name}"
                    f"({_self_C}Object *self,"
                    f" PyObject *args, PyObject *kwds)\n"
                    f"{{\n"
                    f"{guard}"
                    f"    static char *_kwlist[] ="
                    f' {{"{_count_kw}", "out", NULL}};\n'
                    f"{_count_alias}"
                    f"    Py_ssize_t n = {_count_init};\n"
                    f"    PyObject *out_obj = NULL;\n"
                    f"    if (!PyArg_ParseTupleAndKeywords("
                    f'args, kwds, "|nO",\n'
                    f"            _kwlist, &n, &out_obj))\n"
                    f"        return NULL;\n"
                    f"    if (out_obj && out_obj != Py_None) {{\n"
                    f"{_out_guard}"
                    f"        PyArrayObject *out_arr ="
                    f"\n            {_b_array_arg};\n"
                    f"        if (!out_arr) return NULL;\n"
                    f"        if (PyArray_SIZE(out_arr) != n) {{\n"
                    f"            PyErr_Format(PyExc_ValueError,\n"
                    f'                "out length %zd != count %zd",\n'
                    f"                (Py_ssize_t)PyArray_SIZE(out_arr),"
                    f" (Py_ssize_t)n);\n"
                    f"            Py_DECREF(out_arr); return NULL;\n"
                    f"        }}\n"
                    f"{_b_out_kernel}"
                    f"        return (PyObject *)out_arr;\n"
                    f"    }}\n"
                    f"    npy_intp dims[] = {{n}};\n"
                    f"    PyObject *out ="
                    f" PyArray_SimpleNew(1, dims, {ret_np});\n"
                    f"    if (!out) return NULL;\n"
                    f"{_b_new_kernel}"
                    f"    return out;\n"
                    f"}}"
                )
            _ret_np_str = _CTYPE_META[_ret_elem]["py_type"].replace("np.", "")
            # gh-2259: the synopsis names what the Parameters section
            # documents -- `x`, or the count under its kwlist name (gh-657),
            # which this line spelled `n` -- each binding argument with its
            # default.
            _batch_sig = (
                f"{name}("
                + ", ".join(
                    "out=None"
                    if _n == "out"
                    else (
                        f"{_n}={_gluedoc.count_stub_default(_count_default)}"
                        if _stub_count_arg and _n == _count_kw
                        else _n
                    )
                    for _n in _doc_names
                )
                + ") -> ndarray"
            )
            _batch_demo = [
                "",
                "    >>> import numpy as np",
                *_from_line,
                _obj_line,
            ]
            if has_arg:
                _batch_demo += [
                    f"    >>> x = np.zeros(4, dtype={_in_dtype_str})",
                    f"    >>> y = obj.{name}(x)",
                ]
            else:
                _batch_demo.append(f"    >>> y = obj.{name}(4)")
            _batch_demo += [
                "    >>> y.shape",
                "    (4,)",
                "    >>> y.dtype",
                f"    dtype('{_ret_np_str}')",
            ]
            _batch_doc_lines = [
                _batch_sig,
                "",
                *_runtime_doc(
                    f"1:1-rate batch transform. Returns an ndarray of dtype"
                    f" {_ret_np_str}."
                ),
                *_demo(_batch_demo),
            ]
            pmd_lines.append(
                f'    {{"{name}", (PyCFunction)(void (*)(void))'
                f"{wrapper_prefix}_{name},"
                f" METH_VARARGS | METH_KEYWORDS,\n"
                f"     {_build_ml_doc(_batch_doc_lines)}}},\n"
            )
        elif variable_output:
            if has_arg and not has_params:
                if _enable_out:
                    _kwlist_decl = (
                        '    static char *_kwlist[] = {"x", "out", NULL};\n'
                    )
                    _out_decl = "    PyObject *out_obj = NULL;\n"
                    _parse_call = (
                        "    if (!PyArg_ParseTupleAndKeywords("
                        'args, kwds, "O|O",\n'
                        "            _kwlist, &in_obj, &out_obj))\n"
                        "        return NULL;\n"
                    )
                else:
                    _kwlist_decl = ""
                    _out_decl = ""
                    _parse_call = (
                        '    if (!PyArg_ParseTuple(args, "O", &in_obj))\n'
                        "        return NULL;\n"
                    )
                parse_block = (
                    f"{_kwlist_decl}"
                    f"    PyObject *in_obj = NULL;\n"
                    f"{_out_decl}"
                    f"{_parse_call}"
                    f"{_in_acq}"
                    f"    Py_ssize_t n = PyArray_SIZE(in_arr);\n"
                )
                call_data = (
                    f"{_st},"
                    f" (const {arg_disp} *)PyArray_DATA(in_arr),"
                    f" (size_t)n, {_VO_BUF_TOKEN}"
                )
                decref_in = "    Py_DECREF(in_arr);\n"
                _lazy_fallback = "(size_t)n"
                # gh-607: the count `*_max_out()` is called with — the same
                # value about to be passed to the kernel as n_in.
                _moc_arg: str | None = _lazy_fallback
            elif has_arg:
                # gh-1960: an input AND params. This shape parsed the input
                # alone ("O") and called the kernel without the params, while
                # both `.pyi` writers and the runtime doc advertised them and
                # a keyword one was accepted and dropped. The input and the
                # params now go through the parse every other params shape
                # uses -- the single-record shape's route since gh-594 -- with
                # the input first, as the block (`x`, `x_len`) the prototype
                # takes as `in, n_in`. gh-2028: and the optional `out=` last,
                # as that parse's trailing argument; the out branch below
                # validates and acquires it as it does for every other shape.
                parse_block, _p_call, decref_in = _build_params_parse(
                    _block_params(),
                    Component,
                    enums,
                    records,
                    _sid,
                    strict_in,
                    out_obj="out_obj" if _enable_out else "",
                )
                call_data = f"{_st}, {_p_call}, {_VO_BUF_TOKEN}"
                # The input sizes the output, as without params: `x_len` is
                # the `n_in` the kernel and `*_max_out()` are both handed.
                _lazy_fallback = "x_len"
                _moc_arg = _lazy_fallback
            elif has_params:
                # gh-2023 / gh-2220: the params go through the parse every
                # other params shape uses -- the input-and-params shape's
                # route since gh-1960 -- with the optional `out=` last, as
                # that parse's trailing argument (gh-2028). This was a
                # second copy of `_build_params_parse` that knew scalars and
                # plain arrays only: a `path` param did not compile, an
                # `enum` one parsed as an int while both stubs said `str`,
                # and a `size_t` kept the masking `K` after the one
                # tuple-parse slot dropped it.
                parse_block, _p_call, decref_in = _build_params_parse(
                    params,
                    Component,
                    enums,
                    records,
                    _sid,
                    strict_in,
                    out_obj="out_obj" if _enable_out else "",
                )
                call_data = f"{_st}, {_p_call}, {_VO_BUF_TOKEN}"
                _sizing = _outbuf.sizing_param(has_arg, params)
                # gh-421: with no array param to size from, a scalar param
                # (e.g. Delay.push_ptr(x), where x is the value being pushed,
                # not a count) has no "count" semantics jm can derive from its
                # raw value -- casting it as one silently mis-sizes the
                # buffer. Fall back to the method's own <name>_max_out(),
                # always available per the standard variable_output triplet.
                # gh-1996: in samples, as the kernel is handed it above.
                _lazy_fallback = (
                    _coerce.array_count_c(f"{_sizing['name']}_arr", _vo_e)
                    if _sizing is not None
                    else f"{c_fn}_max_out({_st})"
                )
                # gh-607: an all-scalar params shape (no array to size from)
                # has no count for the kernel to take either — max_out()
                # stays zero-arg for this one shape, matching
                # `_max_out_count_param`'s design (there is nothing to pass).
                _moc_arg = _lazy_fallback if _sizing is not None else None
            elif no_count:
                # gh-2233: no input, no params and no count. The one
                # argument is the optional `out=`; without it (a
                # multi_output result) the method takes none at all. The
                # result is sized by `<fn>_max_out(state)` alone, exactly
                # as the all-scalar-params shape above sizes it -- so the
                # same `_zero_bound_guard` refuses a bound of 0 that no
                # `pass_capacity` kernel is told about.
                parse_block = (
                    (
                        '    static char *_kwlist[] = {"out", NULL};\n'
                        "    PyObject *out_obj = NULL;\n"
                        "    if (!PyArg_ParseTupleAndKeywords("
                        'args, kwds, "|O",\n'
                        "            _kwlist, &out_obj))\n"
                        "        return NULL;\n"
                    )
                    if _enable_out
                    else ""
                )
                call_data = f"{_st}, {_VO_BUF_TOKEN}"
                decref_in = ""
                _lazy_fallback = f"{c_fn}_max_out({_st})"
                _moc_arg = None
            else:
                if _enable_out:
                    parse_block = (
                        "    static char *_kwlist[] ="
                        f' {{"{_count_kw}", "out", NULL}};\n'
                        f"{_count_alias}"
                        f"    Py_ssize_t n = {_count_init};\n"
                        "    PyObject *out_obj = NULL;\n"
                        "    if (!PyArg_ParseTupleAndKeywords("
                        'args, kwds, "|nO",\n'
                        "            _kwlist, &n, &out_obj))\n"
                        "        return NULL;\n"
                    )
                else:
                    parse_block = (
                        f"{_count_alias}"
                        f"    Py_ssize_t n = {_count_init};\n"
                        '    if (!PyArg_ParseTuple(args, "|n", &n))\n'
                        "        return NULL;\n"
                    )
                call_data = f"{_st}, (size_t)n, {_VO_BUF_TOKEN}"
                decref_in = ""
                _lazy_fallback = "(size_t)n"
                # gh-607: same value about to be passed to the kernel as n.
                _moc_arg = _lazy_fallback

            # gh-607: `*_max_out()`'s call-site argument list — empty for the
            # all-scalar-params shape, whose kernel has no count to mirror
            # (see `_max_out_count_param_ctx`).
            #
            # ...and empty for a state-only prototype, which gh-761 missed
            # here. It suppressed the count in the *declaration* (above) and
            # in the standalone `*_max_out` Python binding, but not at the two
            # call sites inside the variable-output wrapper — so a project
            # whose header says `max_out(state)` got a wrapper calling
            # `max_out(state, n)`, which does not compile. It stayed latent
            # because per-object fragments are sacred: an existing one keeps
            # the text it was created with, and `refresh_glue_bindings`
            # repairs the `*_max_out` row specifically, not this wrapper. Only
            # a newly added method or a `jm regenerate` renders it fresh.
            # gh-2184: one spelling, `_max_out_call_c`, shared with the
            # list-of-records wrapper that now asks the same function.
            # gh-2233: except under `no_count`, whose `max_out(state)` is
            # call-independent because the CALL is: there is no count for
            # the bound to miss, so gh-920's distrust does not apply and
            # `pass_capacity` keeps its exact allocation. Decided by the key,
            # not the header, so a first render and a re-render agree.
            _moc_state_only = not no_count and max_out_is_state_only(
                doc_blocks, f"{c_fn}_max_out"
            )
            _moc_call = _max_out_call_c(c_fn, doc_blocks, _moc_arg, _st)

            # gh-604: NumPy owns every variable-output result, for one
            # output or many. Each call allocates its arrays at
            # max(max_out(), n), the kernel writes straight into them, and a
            # trimmed view of the filled prefix is returned pinned to the full
            # array.
            #
            # This replaced a per-instance reuse buffer that was grown on
            # demand, retired to a freelist when a previously returned view
            # was still alive (gh-219), and tracked by a weakref to detect
            # that liveness (gh-437). The measurements that retired it:
            # binding the result to a name — i.e. any loop that actually uses
            # the block — took the retire path on *every* call, and retired
            # buffers were freed only in tp_dealloc, so a 3000-iteration loop
            # over 65536 samples grew RSS by ~1.5 GB (~514 KiB/call). It was
            # also 6-8x SLOWER on that path (a fresh malloc plus a page fault
            # per call against a monotonically growing heap), while saving
            # nothing measurable in the case it was designed for: -0.2% at
            # 64k, -0.6% at 1M, and 12 ns at n=1024.
            #
            # `out=` remains the explicit zero-allocation contract, and is the
            # one that can actually promise it — a caller-owned buffer cannot
            # silently alias a previous result the way the reuse buffer could.
            _all_out_rts = [_vo_out_elem] + list(multi_output)
            _n_out_arrays = len(_all_out_rts)
            # gh-788: a record element has no `_CTYPE_META` entry and so no
            # single NPY_ enum -- its whole point is that the row is a struct.
            # It is allocated from a PyArray_Descr instead (below), so it
            # contributes no enum here; this stays a plain subscript for
            # every other type, because a `.get(..., "NPY_FLOAT")` fallback
            # is how an unregistered ctype silently becomes an array of
            # floats.
            _out_np_enums = [
                ""
                if (record_dtype and i == 0)
                else _NP_ENUM[
                    _CTYPE_META[rt[:-2] if rt.endswith("[]") else rt][
                        "py_type"
                    ]
                ]
                for i, rt in enumerate(_all_out_rts)
            ]
            _none_on_empty_line = (
                "    if (!n_out) Py_RETURN_NONE;\n" if none_on_empty else ""
            )
            # Any pre-call failure must still release the input arrays.
            _decref_early_vo = (
                " ".join(
                    line.strip()
                    for line in decref_in.splitlines()
                    if line.strip()
                )
                + " "
                if decref_in.strip()
                else ""
            )
            # gh-1996: the kernel counts samples of `_vo_e` elements, numpy
            # counts elements. `_cap` and `n_out` stay in the kernel's unit
            # throughout -- so `max_out`, the zero-bound guard and the
            # returned-count guard compare like with like -- and these are
            # the places a count becomes a numpy length again. `_cap_unit`
            # is what every message counting `_cap` / `n_out` calls them.
            _odim_c = "n_out" if _vo_e == 1 else f"(n_out * {_vo_e})"
            _cap_unit = (
                "elements" if _vo_e == 1 else f"samples of {_vo_e} elements"
            )

            # gh-2115: `out_cols` makes the single flat result a matrix. The
            # kernel's contract is unchanged -- it fills a flat buffer and
            # returns an element count -- so everything above this line, and
            # `_max_out`, still speak elements; the binding only changes the
            # SHAPE it hands back, and the shape it accepts for `out=`.
            #
            # `_out_cols` is a C expression over the object, evaluated once
            # per call after the arguments parse (so it sees the object as it
            # is NOW, not as it was constructed). `state` is aliased to
            # `self->handle` when mentioned, exactly as `count_default` does;
            # when that method already declared the alias, it is reused, as
            # two declarations of `state` do not compile.
            _out_cols = str(m.get("out_cols", "") or "").strip()
            _cols_block = ""
            if _out_cols:
                _cols_alias = (
                    ""
                    if _count_alias
                    else (
                        f"    {csym}_state_t *state = {_st};\n"
                        if re.search(r"\bstate\b", _out_cols)
                        else ""
                    )
                )
                _cols_block = (
                    f"{_cols_alias}"
                    f"    size_t _cols = (size_t)({_out_cols});\n"
                    f"    if (_cols == 0) {{\n"
                    f"        PyErr_SetString(PyExc_RuntimeError,\n"
                    f'            "{_who}.{name}: out_cols evaluated'
                    f' to 0");\n'
                    f"        {_decref_early_vo}return NULL;\n"
                    f"    }}\n"
                )

            # ── optional out= buffer (single output only) ────────────────
            if _enable_out:
                _out_call_data = call_data.replace(
                    _VO_BUF_TOKEN,
                    f"({_vo_out_disp} *)PyArray_DATA(out_arr)",
                )
                _out_cap_arg = ", _cap" if pass_capacity else ""
                _out_kernel = _reindent(
                    _kernel_call_block(
                        f"{c_fn}({_out_call_data}{_out_cap_arg})",
                        nogil,
                        m,
                        release=f"Py_DECREF(out_arr); {_decref_early_vo}",
                    )
                )
                _out_decref = _reindent(decref_in) if decref_in else ""
                if error_on_empty:
                    # The second call path, and the reason this is generated
                    # at all: hand-written, the refusal has to be repeated per
                    # path per method, and the two paths do not even name the
                    # output array the same way.
                    _out_none = (
                        "        if (!n_out) {\n    "
                        + empty_raise_c(
                            *raise_pair_of(m, name),
                            decrefs="Py_DECREF(out_arr); ",
                        ).replace("\n", "\n    ")[:-4]
                        + "        }\n"
                    )
                else:
                    _out_none = (
                        "        if (!n_out)"
                        " { Py_DECREF(out_arr); Py_RETURN_NONE; }\n"
                        if none_on_empty
                        else ""
                    )
                _vo_out_guard = (
                    _coerce.out_buffer_guard_record(
                        "out_obj",
                        f"{_sid}_get_dtype",
                        decrefs=_decref_early_vo.strip(),
                        indent=" " * 8,
                    )
                    if record_dtype
                    else _coerce.out_buffer_guard(
                        "out_obj",
                        _vo_out_np,
                        decrefs=_decref_early_vo.strip(),
                        indent=" " * 8,
                    )
                )
                # The guard has already proved exact dtype, C-contiguity and
                # writeability, so the record path has nothing to convert:
                # borrow the caller's array and take one reference, which is
                # the same ownership FROM_OTF hands back on the scalar path.
                _vo_acquire = (
                    (
                        "        PyArrayObject *out_arr ="
                        " (PyArrayObject *)out_obj;\n"
                        "        Py_INCREF(out_arr);\n"
                    )
                    if record_dtype
                    else (
                        f"        PyArrayObject *out_arr ="
                        f"\n            {_coerce.array_arg('out_obj', _vo_out_np, 'NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_WRITEABLE', 'out')};\n"
                        f"        if (!out_arr) {{"
                        f" {_decref_early_vo}return NULL; }}\n"
                    )
                )
                # PyArray_NewFromDescr STEALS the descr on both paths, so the
                # new reference from _get_dtype() is balanced with no decref.
                _vo_view = (
                    (
                        f"        PyArray_Descr *_vdescr ="
                        f" {_sid}_get_dtype();\n"
                        f"        if (!_vdescr)"
                        f" {{ Py_DECREF(out_arr); return NULL; }}\n"
                        f"        PyObject *_oview = PyArray_NewFromDescr(\n"
                        f"            &PyArray_Type, _vdescr, 1, &_odim,\n"
                        f"            NULL, PyArray_DATA(out_arr),"
                        f" 0, NULL);\n"
                    )
                    if record_dtype
                    else (
                        "        PyObject *_oview ="
                        " PyArray_SimpleNewFromData(\n"
                        + (
                            f"            2, _odims, {_vo_out_np},"
                            if _out_cols
                            else f"            1, &_odim, {_vo_out_np},"
                        )
                        + " PyArray_DATA(out_arr));\n"
                    )
                )
                # gh-2115: a matrix result takes a matrix `out=`. The view
                # returned over it is (rows, cols), so a 1-D buffer or one of
                # another width would hand back a shape the caller did not
                # allocate -- refused with the shape that was expected.
                _vo_out_shape = (
                    (
                        f"        if (PyArray_NDIM(out_arr) != 2 ||\n"
                        f"            (size_t)PyArray_DIM(out_arr, 1)"
                        f" != _cols) {{\n"
                        f"            PyErr_Format(PyExc_ValueError,\n"
                        f'                "out must be a 2-D array with'
                        f' %zu columns, shape (rows, %zu)",\n'
                        f"                _cols, _cols);\n"
                        f"            Py_DECREF(out_arr);"
                        f" {_decref_early_vo}return NULL;\n"
                        f"        }}\n"
                    )
                    if _out_cols
                    else ""
                )
                # Without pass_capacity, max_out() alone is not always a
                # true call-independent upper bound — a generator's
                # steps(count) writes exactly the caller's requested
                # size, which can exceed it. Require capacity for
                # whichever is larger, matching the internal path's own
                # `_cap < _need` fallback so the two agree instead of
                # out= silently under-validating.
                #
                # With pass_capacity the kernel is handed `_cap` below
                # and trusted to respect it exactly -- that is the same
                # trust already extended on the internal-allocation path
                # (`_vo_alloc`'s clamp is dropped there under the same
                # condition). Requiring anything more than _omax here
                # would reject a caller-owned buffer sized to the exact
                # bound the binding itself would have allocated.
                #
                # gh-805 §D: `exact_max_out` earns the same trust without
                # changing the kernel signature. `_capacity_exprs` holds
                # all three cases and also produces `_vo_alloc`'s clamp
                # below, so the two cannot answer differently.
                #
                # gh-920: except when the header's `max_out` takes only the
                # state, which is a bound that cannot see this call.
                _out_min_cap = _capacity_exprs(
                    bool(pass_capacity),
                    exact_max_out,
                    _lazy_fallback,
                    _moc_state_only,
                    short_out=bool(m.get("short_out")),
                )[1]
                _out_zero = _reindent(
                    _zero_bound_guard(
                        c_fn,
                        name,
                        _lazy_fallback,
                        f"Py_DECREF(out_arr); {_decref_early_vo}",
                        bound="_omax",
                        pass_capacity=bool(pass_capacity),
                    )
                )
                # gh-2230: `short_out` has no minimum, so `_capacity_exprs`
                # answers "" and there is no bound to read or to check: the
                # buffer's shape, above, is all an `out=` is held to, and
                # the kernel is handed its true size below. Every other
                # method renders the three lines it always did.
                _out_bound = (
                    (
                        # gh-2273: `max_out` reads the state too, and runs
                        # straight after the arrays were converted.
                        _inflight.recheck(
                            "self",
                            _moc_call,
                            f"Py_DECREF(out_arr); {_decref_early_vo}",
                            indent="        ",
                        )
                        + f"        size_t _omax = {_moc_call};\n"
                        + _out_min_cap
                        + _out_zero
                        + (
                            f"        if (_cap < _min_cap) {{\n"
                            f"            PyErr_Format(PyExc_ValueError,\n"
                            f'                "out has %zu {_cap_unit},'
                            f' need >= %zu",\n'
                            f"                _cap, _min_cap);\n"
                            f"            Py_DECREF(out_arr);"
                            f" {_decref_early_vo}return NULL;\n"
                            f"        }}\n"
                            if _out_min_cap
                            else ""
                        )
                    )
                    if (_out_min_cap or _out_zero)
                    else ""
                )
                _out_branch = (
                    f"    if (out_obj && out_obj != Py_None) {{\n"
                    f"{_vo_out_guard}"
                    f"{_vo_acquire}"
                    f"{_vo_out_shape}"
                    f"        size_t _cap ="
                    f" {_coerce.array_count_c('out_arr', _vo_e)};\n"
                    f"{_out_bound}"
                    f"{_out_kernel}"
                    f"{_out_decref}"
                    # gh-2012: a declared refusal value, read before the
                    # guard below -- which would otherwise report it as an
                    # overrun. This path holds `out_arr`.
                    + _count_refusal_c(m, name, "Py_DECREF(out_arr);", " " * 8)
                    # gh-2327: kept once the kernel has not refused -- here,
                    # or after the empty refusal below when it declares one.
                    + ("" if error_on_empty else _reindent(_keep))
                    # gh-1716: `n_out` becomes the length of a view over the
                    # caller's `_cap`-element buffer; past it, the view reads
                    # beyond the caller's allocation.
                    + _coerce.returned_count_c(
                        "n_out",
                        "_cap",
                        f"{_who}.{name}",
                        "Py_DECREF(out_arr);",
                        indent=" " * 8,
                        unit=_cap_unit,
                    )
                    + f"{_out_none}"
                    + (_reindent(_keep) if error_on_empty else "")
                    + f"        npy_intp _odim = (npy_intp){_odim_c};\n"
                    + (
                        # gh-2115: whole rows only. A kernel that returns a
                        # count that is not a multiple of the width has broken
                        # the contract the declaration made, and a ragged
                        # last row would be a silently wrong shape.
                        f"        if ((size_t)_odim % _cols != 0) {{\n"
                        f"            PyErr_Format(PyExc_RuntimeError,\n"
                        f'                "{_who}.{name}: kernel returned'
                        f" %zu elements, not a whole number of %zu-wide"
                        f' rows",\n'
                        f"                (size_t)_odim, _cols);\n"
                        f"            Py_DECREF(out_arr); return NULL;\n"
                        f"        }}\n"
                        f"        npy_intp _odims[2] = {{\n"
                        f"            _odim / (npy_intp)_cols,"
                        f" (npy_intp)_cols}};\n"
                        if _out_cols
                        else ""
                    )
                    + f"{_vo_view}"
                    f"        if (!_oview)"
                    f" {{ Py_DECREF(out_arr); return NULL; }}\n"
                    # gh-1312: checked, like every other SetBaseObject jm
                    # emits. This one pins the CALLER's array rather than
                    # `self`, so it hands over the reference this function
                    # already owns instead of taking a new one -- which is
                    # why there is no Py_INCREF above. On failure the steal
                    # did not happen, so that reference is still ours to
                    # drop.
                    f"        if (PyArray_SetBaseObject(\n"
                    f"                (PyArrayObject *)_oview,\n"
                    f"                (PyObject *)out_arr) < 0) {{\n"
                    f"            Py_DECREF(out_arr);\n"
                    f"            Py_DECREF(_oview);\n"
                    f"            return NULL;\n"
                    f"        }}\n"
                    f"        return _oview;\n"
                    f"    }}\n"
                )
            else:
                _out_branch = ""
            # Every parse above reads `args` (an output count is optional,
            # never absent). gh-412: params methods take kwds (keyword
            # parsing) even without the out= buffer branch -- and with an
            # input as well since gh-1960, whose parse dropped the params and
            # left `kwds` unread.
            # gh-2233: a `no_count` method without `out=` (multi_output)
            # reads neither, and is METH_NOARGS like every other no-argument
            # wrapper.
            _vo_cc = call_convention(
                _self_C,
                args=not (no_count and not _enable_out),
                kwds=_enable_kw,
            )

            # ── allocate the outputs, call, trim, return ─────────────────
            _idx = range(_n_out_arrays)
            _xdecref_arrs = " ".join(f"Py_XDECREF(arr{i});" for i in _idx)
            _decref_arrs = " ".join(f"Py_DECREF(arr{i});" for i in _idx)
            _vo_alloc = (
                # gh-2273: as for the `out=` path's `max_out`.
                _inflight.recheck("self", _moc_call, _decref_early_vo)
                + f"    size_t _need = {_lazy_fallback};\n"
                f"    size_t _cap ="
                f" {_moc_call};\n"
                # gh-607: without pass_capacity, the kernel is never told its
                # capacity, so max_out() is only a sizing HINT and the alloc
                # is clamped to at least what the call needs — a mechanically
                # migrated `return 0;` still allocates `_need` and is safe.
                # With pass_capacity the kernel enforces the bound itself
                # (it's handed `_cap` below), so the exact bound is trusted
                # and the clamp is dropped — that's the entire point of
                # opting in: exact allocation instead of a defensive one.
                #
                # gh-805 §D: `exact_max_out` drops the clamp too, keeping only
                # the zero-guard — a mechanically migrated `return 0;` must
                # still allocate `_need` rather than nothing.
                #
                # gh-920: a state-only `max_out(state)` keeps the clamp under
                # `pass_capacity`, because a call-independent value read as an
                # exact per-call bound truncates the request instead.
                + _capacity_exprs(
                    bool(pass_capacity),
                    exact_max_out,
                    "_need",
                    _moc_state_only,
                )[0]
                + _zero_bound_guard(
                    c_fn,
                    name,
                    _lazy_fallback,
                    _decref_early_vo,
                    pass_capacity=bool(pass_capacity),
                )
                # gh-1710: `_cap` is `max_out()`'s size_t; past NPY_MAX_INTP
                # it wrapped to a negative dimension.
                + _coerce.output_size_c(
                    "_adim",
                    "_cap",
                    f"{_who}.{name}",
                    _decref_early_vo,
                    # gh-1996: `_cap` is in samples and the arrays hold
                    # elements, so the bound is the quotient and the product
                    # is taken after it -- it cannot wrap.
                    limit=(
                        "NPY_MAX_INTP"
                        if _vo_e == 1
                        else f"(NPY_MAX_INTP / {_vo_e})"
                    ),
                    unit=_cap_unit,
                )
                + ("" if _vo_e == 1 else f"    _adim *= {_vo_e};\n")
                + "".join(
                    (
                        # gh-788: the structured output. `_get_dtype()` hands
                        # back a NEW reference and PyArray_NewFromDescr STEALS
                        # one, on the failure path as well as the success
                        # path — so the two balance exactly and there is no
                        # decref of `_descr` anywhere below. Getting this
                        # wrong is a refcount bug that only shows up under
                        # sustained load, so it is spelled out rather than
                        # left to the reader.
                        f"    PyArray_Descr *_descr = {_sid}_get_dtype();\n"
                        f"    if (!_descr)"
                        f" {{ {_decref_early_vo}return NULL; }}\n"
                        f"    PyObject *arr{i} = PyArray_NewFromDescr(\n"
                        f"        &PyArray_Type, _descr, 1, &_adim,\n"
                        f"        NULL, NULL, 0, NULL);\n"
                    )
                    if (record_dtype and i == 0)
                    else (
                        f"    PyObject *arr{i} ="
                        f" PyArray_SimpleNew(1, &_adim,"
                        f" {_out_np_enums[i]});\n"
                    )
                    for i in _idx
                )
                + (
                    f"    if (!arr0) {{ {_decref_early_vo}return NULL; }}\n"
                    if _n_out_arrays == 1
                    else f"    if ({' || '.join(f'!arr{i}' for i in _idx)}) {{\n"
                    f"        {_xdecref_arrs} {_decref_early_vo}return NULL;\n"
                    f"    }}\n"
                )
                # Hoist each data pointer into a typed local. Two reasons:
                # the kernel call then contains no Python C-API at all, which
                # is what makes `nogil` sound (PyArray_DATA inlined into the
                # call would sit inside Py_BEGIN_ALLOW_THREADS); and the
                # generated call reads as plain C.
                + "".join(
                    f"    {_all_out_rts[i]} *_d{i} ="
                    f" ({_all_out_rts[i]} *)"
                    f"PyArray_DATA((PyArrayObject *)arr{i});\n"
                    for i in _idx
                )
            )
            _vo_call_data = call_data.replace(_VO_BUF_TOKEN, "_d0")
            _vo_call_extra = "".join(
                f", _d{i}" for i in range(1, _n_out_arrays)
            )
            _vo_cap_arg = ", _cap" if pass_capacity else ""
            _kernel_vo = _kernel_call_block(
                f"{c_fn}({_vo_call_data}{_vo_call_extra}{_vo_cap_arg})",
                nogil,
                m,
                release=f"{_decref_arrs} {_decref_early_vo}",
            )
            if error_on_empty:
                # The same test, the opposite verdict. Rendered from
                # `declared_raise`, so the `.pyi` and the runtime docstring
                # document exactly the exception this raises.
                _vo_empty = (
                    "    if (!n_out) {\n"
                    + empty_raise_c(
                        *raise_pair_of(m, name),
                        decrefs=_decref_arrs.strip() + " "
                        if _decref_arrs.strip()
                        else "",
                    )
                    + "    }\n"
                )
            else:
                _vo_empty = (
                    f"    if (!n_out) {{ {_decref_arrs} Py_RETURN_NONE; }}\n"
                    if none_on_empty
                    else ""
                )
            # n_out <= _cap. Shrink each array in place rather than
            # returning a view pinned to the full allocation: the array is
            # fresh, unshared and refcount-1, so PyArray_Resize reallocs the
            # buffer down and RELEASES the tail.
            #
            # gh-604 follow-up: the view form retained the whole allocation
            # for as long as the caller held the result, which is governed by
            # how tight max_out() is, not by the data. A kernel whose
            # max_out() is a fixed internal cap (a resampler at 65536 emitting
            # 512) retained 128x the samples it returned. Shrinking makes
            # retention proportional to the data instead of the cap. See
            # gh-607 for making max_out() a per-call bound, which removes the
            # over-allocation itself rather than just its retention.
            _vo_views = (
                f"    npy_intp _odim = (npy_intp){_odim_c};\n"
                + (
                    f"    if ((size_t)_odim % _cols != 0) {{\n"
                    f"        PyErr_Format(PyExc_RuntimeError,\n"
                    f'            "{_who}.{name}: kernel returned'
                    f" %zu elements, not a whole number of %zu-wide"
                    f' rows",\n'
                    f"            (size_t)_odim, _cols);\n"
                    f"        {_decref_arrs} return NULL;\n"
                    f"    }}\n"
                    f"    npy_intp _odims[2] = {{\n"
                    f"        _odim / (npy_intp)_cols, (npy_intp)_cols}};\n"
                    if _out_cols
                    else ""
                )
                + "".join(
                    f"    PyArray_Dims _rs{i} = "
                    + ("{_odims, 2}" if _out_cols else "{&_odim, 1}")
                    + ";\n"
                    f"    PyObject *v{i} = PyArray_Resize(\n"
                    f"        (PyArrayObject *)arr{i}, &_rs{i}, 0,"
                    f" NPY_CORDER);\n"
                    f"    if (!v{i}) {{ {_decref_arrs} return NULL; }}\n"
                    f"    Py_DECREF(v{i});\n"
                    for i in _idx
                )
            )
            if _n_out_arrays == 1:
                _vo_exact_return = "        return arr0;\n"
                _vo_return = "    return arr0;\n"
            else:
                _vo_exact_return = (
                    f"        PyObject *_exact = PyTuple_Pack("
                    f"{_n_out_arrays},"
                    f" {', '.join(f'arr{i}' for i in _idx)});\n"
                    + "".join(f"        Py_DECREF(arr{i});\n" for i in _idx)
                    + "        return _exact;\n"
                )
                # The arrays were shrunk in place, so THEY are the results
                # -- PyArray_Resize returns None, not a new array.
                _vo_return = (
                    f"    PyObject *result = PyTuple_Pack("
                    f"{_n_out_arrays},"
                    f" {', '.join(f'arr{i}' for i in _idx)});\n"
                    + "".join(f"    Py_DECREF(arr{i});\n" for i in _idx)
                    + "    return result;\n"
                )
            # Exact-fill fast path: when the kernel filled the whole
            # allocation there is nothing to trim, so hand the array back
            # directly and skip a per-call PyObject. This is the common case
            # for a generator shape (steps(n) returns exactly n), and without
            # it the trim view is pure overhead on the hot path.
            _vo_exact = (
                ""
                if _out_cols
                else (
                    f"    if ((size_t)n_out == _cap) {{\n"
                    f"{_vo_exact_return}"
                    f"    }}\n"
                )
            )
            # gh-788: the cached descr builder is file-scope, so it is
            # prepended to the wrapper the way the structseq descriptor is —
            # the two travel together or the fragment does not compile.
            _vo_dtype_helper = (
                _record.dtype_c(
                    _sid, record_dtype, _record.fields(m, doc_blocks, records)
                )
                if record_dtype
                else ""
            )
            wrapper = (
                _in_dtype_helper
                + _vo_dtype_helper
                + (
                    f"static PyObject *\n"
                    f"{wrapper_prefix}_{name}"
                    f"({_vo_cc.params})\n"
                    f"{{\n"
                    f"{guard}"
                    f"{parse_block}"
                    f"{_cols_block}"
                    f"{_out_branch}"
                    f"{_vo_alloc}"
                    f"{_kernel_vo}"
                    f"{decref_in}"
                    # gh-2012: the same refusal, before the same guard. This
                    # path holds the arrays it allocated, not `out_arr`.
                    + _count_refusal_c(m, name, _decref_arrs, " " * 4)
                    + ("" if error_on_empty else _keep)
                    # gh-1716: past `_cap`, PyArray_Resize below GREW the
                    # result into memory the kernel never wrote.
                    + _coerce.returned_count_c(
                        "n_out",
                        "_cap",
                        f"{_who}.{name}",
                        _decref_arrs,
                        unit=_cap_unit,
                    )
                    + f"{_vo_empty}"
                    + (_keep if error_on_empty else "")
                    + f"{_vo_exact}"
                    f"{_none_on_empty_line if not none_on_empty else ''}"
                    f"{_vo_views}"
                    f"{_vo_return}"
                    f"}}"
                )
            )
            _all_rts_vo = [_vo_out_elem] + list(multi_output)
            _dtype_strs_vo = [
                # A record row has no scalar dtype string to print; the demo
                # below shows its field NAMES instead, which is both stable
                # output and the thing a reader wants from a structured
                # result.
                ""
                if (record_dtype and i == 0)
                else _CTYPE_META[rt[:-2] if rt.endswith("[]") else rt][
                    "py_type"
                ].replace("np.", "")
                for i, rt in enumerate(_all_rts_vo)
            ]
            # gh-2183: the synopsis line is the runtime face's annotation.
            _ret_hint_vo = optional_ann(
                m,
                (
                    f"tuple[{', '.join('ndarray' for _ in _all_rts_vo)}]"
                    if len(_all_rts_vo) > 1
                    else "ndarray"
                ),
            )
            if has_arg or has_params or no_count:
                # Every declared param, not just the first array one: the
                # signature line has to match what the binding accepts.
                # gh-2233: for `no_count` that is `out` alone, or nothing.
                _vo_sig_arg = ", ".join(_doc_names)
                # gh-2001: and so does the example's call. It passed ONE
                # argument whatever the params -- `obj.delay(np.zeros(4))`
                # for `delay(x, mu)`, a TypeError the moment it runs -- and
                # with an input it passed the input alone (gh-1960).
                _vo_call_example = f"obj.{name}({_demo_call_args()})"
            else:
                # gh-657: the kwlist binds this as `count`; the doc said
                # `n`, which is what sent the reporter looking for a rename
                # that never happened.
                # A declared default is C, not a Python literal, so the
                # Python-facing signature shows `...` rather than leaking it.
                _vo_sig_arg = f"{_count_kw}={'...' if _count_default else 1}"
                # gh-2222: and the buffer it takes, as every other shape's
                # signature line names it (`_doc_names`).
                if _stub_enable_out:
                    _vo_sig_arg += ", out=None"
                _vo_call_example = f"obj.{name}(4)"
            # gh-788: a numpydoc-shaped block naming each column of the
            # structured result and its Python type. Only the documented
            # fields get prose; an undocumented one still gets its name and
            # type, because that much is derived rather than invented.
            _vo_field_doc_lines: list[str] = []
            if record_dtype:
                _vo_flds = _record.fields(m, doc_blocks, records)
                _vo_field_doc_lines = ["", "Fields", "------"]
                for _vf in _vo_flds:
                    _vo_field_doc_lines.append(
                        f"{_vf.name} : {_pyi_scalar(_vf.ctype)}"
                    )
                    if _vf.doc:
                        _vo_field_doc_lines.append(f"    {_vf.doc}")
            _vo_doc_lines = [
                f"{name}({_vo_sig_arg}) -> {_ret_hint_vo}",
                "",
                # gh-604: this default ships into the user's compiled .so as
                # the method's __doc__, so it has to describe what the binding
                # actually does. It previously advertised the reuse buffer
                # ("zero-copy view into an internally managed buffer … a
                # still-referenced buffer is retired, never reused in place")
                # and was wrong on both clauses once that buffer was deleted.
                *_runtime_doc(
                    (
                        # gh-788: a structured result gets its own sentence,
                        # naming the property that makes the shape worth
                        # having.
                        "Returns a new NumPy-owned structured array each"
                        f" call, one row per {record_dtype} — the dtype is"
                        " that struct's own layout, so a row and the C"
                        " record are the same bytes. Independent of every"
                        " other result, and safe to keep."
                        if record_dtype
                        else "Returns a new NumPy-owned array each call —"
                        " independent of every other result, and safe to"
                        " keep."
                    )
                    # gh-2222: offered where the binding takes `out=` -- the
                    # predicate its `_kwlist` and both `.pyi` writers read
                    # -- and only there: a `multi_output` method has none,
                    # and a structured result has one (gh-805 section E).
                    + (
                        " Pass out= to write into your own buffer instead."
                        if _stub_enable_out
                        else ""
                    )
                ),
                # gh-788: the columns, documented on the SAME face as the
                # signature. This is the point of the migration — a
                # hand-written module carries its field docs in a header
                # comment, a PyMethodDef literal and a .pyi with nothing
                # linking the three, and they drift. Here `_record.fields`
                # resolves each doc from the manifest or, failing that, the
                # sacred header's own `///<` member doc (gh-671), so one
                # source feeds every face.
                *_vo_field_doc_lines,
                *_demo(
                    [
                        "",
                        "    >>> import numpy as np",
                        *_from_line,
                        _obj_line,
                        f"    >>> y = {_vo_call_example}",
                        # gh-2232: a declared refusal (`error_on_empty`,
                        # `error_sentinel`, a signed count's
                        # `error_negative`) claims no result, as on the
                        # fixed-output demo: the kernel may refuse this input
                        # -- the scaffold's own stub does, under
                        # `error_on_empty`.
                        # A structured row has no scalar dtype to print, and
                        # the full repr is long and numpy-version-sensitive;
                        # the field names are the stable, useful answer.
                        *(
                            []
                            if _raise_pair is not None
                            else [
                                "    >>> y.dtype.names",
                                # Python's own repr, so the one-field case
                                # keeps its trailing comma and the doctest is
                                # copy-pasteable rather than nearly right.
                                "    "
                                + repr(
                                    tuple(_f["name"] for _f in result_fields)
                                ),
                            ]
                            if record_dtype
                            else [
                                f"    >>> y"
                                f"{'[0]' if len(_all_rts_vo) > 1 else ''}"
                                f".dtype",
                                f"    dtype('{_dtype_strs_vo[0]}')",
                            ]
                        ),
                    ]
                ),
            ]
            pmd_lines.append(
                f'    {{"{name}", {_vo_cc.cast}{wrapper_prefix}_{name},'
                f" {_vo_cc.flags},\n"
                f"     {_build_ml_doc(_vo_doc_lines)}}},\n"
            )
            if _enable_out:
                # gh-219: expose <verb>_max_out() so callers can size the
                # `out=` buffer they pass in.
                # gh-607: it takes the same count the binding is about to
                # pass to the kernel (see `_max_out_count_param_ctx`) — a
                # caller who wants to size a buffer for `execute(x, out=…)`
                # calls `execute_max_out(len(x))`, mirroring the call
                # they're about to make. The all-scalar-params shape has no
                # count to mirror and stays the original zero-arg form.
                _pymo_decl, _pymo_name = _max_out_count_param_ctx(
                    has_arg, has_params, params, no_count
                )
                # gh-761: the header's own prototype wins. jm assumed every
                # `_max_out` takes a count (gh-607); most kernels bound their
                # output by the state, not by the block, and declare
                # `size_t <c>_<m>_max_out(<c>_state_t *)`. Emitting a
                # count-taking binding against that is both a compile error
                # and — where an older jm already wrote the no-arg form — a
                # stub that disagrees with the binding beside it.
                if max_out_is_state_only(doc_blocks, f"{c_fn}_max_out"):
                    _pymo_decl, _pymo_name = "", None
                # gh-1996: the caller passes `len(x)` and allocates `out=`
                # from the answer, both in elements; the C function takes
                # and returns samples. Converted on both sides, so the
                # sentence above still holds under an interleave.
                _mo_per = f" / {_vo_e}" if _vo_e != 1 else ""
                if _pymo_name:
                    _mo_doc = _build_ml_doc(
                        [f"{name}_max_out({_pymo_name}) -> int", ""]
                        + _max_out_doc(
                            component,
                            name,
                            _pymo_name,
                            m.get("max_out", 0),
                            lambda k: (doc_blocks or {}).get(k),
                            c_fn=c_fn,
                            csym=csym,
                            elements_per_sample=_vo_e,
                        ).c_doc_lines(raises=_host_raises or None)
                    )
                    method_c_parts.append(
                        f"static PyObject *\n"
                        f"{wrapper_prefix}_{name}_max_out"
                        f"({_self_C}Object *self, PyObject *args)\n"
                        f"{{\n"
                        f"{guard}"
                        f"    Py_ssize_t {_pymo_name} = 0;\n"
                        f'    if (!PyArg_ParseTuple(args, "n",'
                        f" &{_pymo_name}))\n"
                        f"        return NULL;\n"
                        + _max_out_return_c(
                            f"{c_fn}_max_out({_st},"
                            f" (size_t){_pymo_name}{_mo_per})",
                            _vo_e,
                            f"{_who}.{name}_max_out",
                        )
                        + "}"
                    )
                    pmd_lines.append(
                        f'    {{"{name}_max_out",'
                        f" (PyCFunction){wrapper_prefix}_{name}_max_out,\n"
                        f"     METH_VARARGS, {_mo_doc}}},\n"
                    )
                else:
                    _mo_doc = _build_ml_doc(
                        [f"{name}_max_out() -> int", ""]
                        + _max_out_doc(
                            component,
                            name,
                            "",
                            m.get("max_out", 0),
                            lambda k: (doc_blocks or {}).get(k),
                            c_fn=c_fn,
                            csym=csym,
                            elements_per_sample=_vo_e,
                        ).c_doc_lines(raises=_host_raises or None)
                    )
                    method_c_parts.append(
                        f"static PyObject *\n"
                        f"{wrapper_prefix}_{name}_max_out"
                        f"({_self_C}Object *self,"
                        f" PyObject *Py_UNUSED(ignored))\n"
                        f"{{\n"
                        f"{guard}"
                        + _max_out_return_c(
                            f"{c_fn}_max_out({_st})",
                            _vo_e,
                            f"{_who}.{name}_max_out",
                        )
                        + "}"
                    )
                    pmd_lines.append(
                        f'    {{"{name}_max_out",'
                        f" (PyCFunction){wrapper_prefix}_{name}_max_out,\n"
                        f"     METH_NOARGS, {_mo_doc}}},\n"
                    )
        elif result_fields and single_record:
            # gh-244: return ONE named record as a PyStructSequence (named,
            # unpackable) instead of a list[tuple]. The C kernel returns the
            # record struct by value; the structseq type is created lazily and
            # cached in this translation unit, so module-init/aggregator wiring
            # is untouched. (`_sid` is hoisted to the top of the loop —
            # gh-788's dtype statics use the same prefix.)
            # gh-257/gh-261/gh-646: the record's public name, its qualified
            # name, and its documented fields all come from _record, which the
            # two .pyi writers read too -- the descriptor emitted here and the
            # class they emit describe one type, so they derive it once.
            _rec_name = _record.public_name(m)
            _rec_fields = _record.fields(m, doc_blocks, records)
            _descriptor = _record.descriptor_c(
                _sid,
                _record.qualified_name(m),
                _record.type_doc(m, _rec_fields),
                _rec_fields,
            )
            # gh-594: method params go through the SAME builder every other
            # method shape uses (`_build_params_parse`), rather than the
            # scalar-only loop that used to live here. That loop emitted
            # `type` as a declaration and `_CTYPE_META.get(
            # type, {}).get("fmt", "d")` as a format char, so an array param
            # rendered the invalid `float _Complex[] rx = 0;`, parsed as a
            # scalar double, and passed no length -- three compile errors from
            # one omission. The shared builder already handles arrays (ptr +
            # `_len`), capsules, `parse_type` scalars and gh-240 defaults, and
            # keeping one copy is what stops this shape drifting from the rest
            # again.
            _has_kw = bool(params)
            _sp_kwnames = [_p["name"] for _p in params]

            if _has_kw:
                # A record method's `arg_type` is always the block input
                # (`const T *in, size_t n_in` in the prototype -- see
                # _method._methods_c_stub_result_single), so the primary arg
                # joins the param list in its array form: `_block_params`.
                _s_parse, _p_call, _p_cleanup = _build_params_parse(
                    _block_params(), Component, enums, records, _sid, strict_in
                )
                # Any array acquired above must be released on the structseq
                # type-creation failure path too, not just after the call.
                _cleanup_inline = _p_cleanup.replace("\n    ", " ").strip()
                _fail = f" {{{' ' + _cleanup_inline if _cleanup_inline else ''} return NULL; }}"
                _s_ensure = (
                    f"    if (!{_sid}_type) {{\n"
                    f"        {_sid}_type ="
                    f" PyStructSequence_NewType(&{_sid}_desc);\n"
                    f"        if (!{_sid}_type){_fail}\n"
                    f"    }}\n"
                )
                _s_call = (
                    _single_kernel_block(
                        ret_disp,
                        f"{c_fn}({_st}, {_p_call})",
                        nogil,
                        release=_cleanup_inline,
                    )
                    + _p_cleanup
                    + _keep
                )
            elif has_arg:
                _s_parse = (
                    f"    PyObject *in_obj = NULL;\n"
                    f'    if (!PyArg_ParseTuple(args, "O", &in_obj))\n'
                    f"        return NULL;\n"
                    f"{_in_acq}"
                    f"    size_t n_in = (size_t)PyArray_SIZE(in_arr);\n"
                )
                _s_ensure = (
                    f"    if (!{_sid}_type) {{\n"
                    f"        {_sid}_type ="
                    f" PyStructSequence_NewType(&{_sid}_desc);\n"
                    f"        if (!{_sid}_type)"
                    f" {{ Py_DECREF(in_arr); return NULL; }}\n"
                    f"    }}\n"
                )
                _s_call = (
                    _single_kernel_block(
                        ret_disp,
                        f"{c_fn}({_st},\n"
                        f"        (const {arg_disp} *)PyArray_DATA(in_arr),"
                        f" n_in)",
                        nogil,
                        release="Py_DECREF(in_arr);",
                    )
                    + "    Py_DECREF(in_arr);\n"
                )
            else:
                _s_parse = ""
                _s_ensure = (
                    f"    if (!{_sid}_type) {{\n"
                    f"        {_sid}_type ="
                    f" PyStructSequence_NewType(&{_sid}_desc);\n"
                    f"        if (!{_sid}_type) return NULL;\n"
                    f"    }}\n"
                )
                _s_call = _single_kernel_block(
                    ret_disp, f"{c_fn}({_st})", nogil, release=""
                )
            _set_lines = []
            for _i, _f in enumerate(result_fields):
                _topy = _CTYPE_META[_f["type"]]["to_py"](f"_r.{_f['name']}")
                _set_lines.append(
                    f"    PyStructSequence_SET_ITEM(_o, {_i}, {_topy});\n"
                )
            # gh-1959: with no input and no param the body parses nothing,
            # so the wrapper is METH_NOARGS -- it took `(self, args)`, never
            # read `args`, and dropped whatever the caller passed.
            _s_cc = call_convention(
                _self_C, args=has_arg or _has_kw, kwds=_has_kw
            )
            wrapper = (
                _in_dtype_helper
                + _descriptor
                + (
                    f"static PyObject *\n"
                    f"{wrapper_prefix}_{name}"
                    f"({_s_cc.params})\n"
                    f"{{\n"
                    f"{guard}"
                    f"{_s_parse}"
                    f"{_s_ensure}"
                    f"{_s_call}"
                    f"    PyObject *_o = PyStructSequence_New({_sid}_type);\n"
                    f"    if (!_o) return NULL;\n"
                    f"{''.join(_set_lines)}"
                    f"    return _o;\n"
                    f"}}"
                )
            )
            _s_names = ", ".join(_f["name"] for _f in result_fields)
            _s_demo = [""]
            if has_arg or has_params:
                _s_demo.append("    >>> import numpy as np")
            _s_demo += [
                *_from_line,
                _obj_line,
                f"    >>> rec = obj.{name}({_demo_call_args()})",
                f"    >>> rec.{result_fields[0]['name']} is not None",
                "    True",
            ]
            _s_doc_lines = [
                # The signature line keeps the field list: a structseq prints
                # as a bare tuple at a REPL, so naming its members where the
                # reader's eye lands first is worth the width.
                f"{name}({', '.join(_doc_names)}) ->"
                f" {_rec_name} record ({_s_names})",
                "",
                # gh-1039: the FIFTH shape, and the last one whose runtime
                # face ignored the header outright. gh-642 gave the other four
                # `_runtime_doc`; this branch kept the canned line above as its
                # entire doc, so a fully documented record method had a full
                # `.pyi` and a one-line `help()` — with no authoring move that
                # could fix it, since a manifest `doc` never reached here
                # either.
                *_runtime_doc(f"Returns one {_rec_name} record."),
                *_demo(_s_demo),
            ]
            pmd_lines.append(
                f'    {{"{name}", {_s_cc.cast}{wrapper_prefix}_{name},'
                f" {_s_cc.flags},\n"
                f"     {_build_ml_doc(_s_doc_lines)}}},\n"
            )
        elif result_fields and not _record.is_record_array(
            variable_output, record_dtype, borrow
        ):
            # gh-1310: this chain never needed the guard while `record_dtype`
            # implied `variable_output` -- such a method always took the
            # branch above and could not reach here. A BORROWED record
            # carries `result_fields` for the dtype's columns and reaches
            # this branch, which would render the list-of-records kernel
            # (`results, max_results`) for a method that lends a pointer.
            # The fifth place that must suppress the list reading of
            # `result_fields`, and the first one a borrow can reach.
            #
            # gh-598: peer of _render's list-of-records builder — both go
            # through record_tuple_build so a field type converts via
            # _CTYPE_META's to_py rather than a cast-less "i" fallback.
            _bv = record_tuple_build(result_fields, "results[i]")

            # gh-2184: where the records go, and how many the kernel may
            # write -- a fixed stack array, or the capacity the header's
            # `<c_fn>_max_out` reports for this call. *count* is the length
            # the kernel is about to be handed, as a `variable_output`
            # wrapper passes its own (gh-607); *release* what the wrapper
            # holds before the kernel runs.
            def _rf_buffer(
                count: "str | None", release: str
            ) -> "tuple[str, str, str]":
                return _records_buffer_c(
                    ret_disp,
                    c_fn,
                    doc_blocks,
                    count,
                    max_results,
                    f"{_who}.{name}",
                    release,
                    _st,
                )

            if has_params:
                # gh-1961: the params, through the parse the single-record
                # branch above uses (gh-594), the input first as its block.
                # Parsed by neither path before, so the kernel was called
                # without them against a prototype that takes them, and the
                # wrapper did not compile.
                _rf_parse, _p_call, _p_cleanup = _build_params_parse(
                    _block_params(), Component, enums, records, _sid, strict_in
                )
                # The block input is `x` here; without one, the first array
                # param is the count, as `_max_out_count_param_ctx` declares.
                _rf_count = "x_len" if has_arg else None
                for _p in [] if has_arg else params:
                    if is_array_param_type(_p["type"]):
                        _rf_count = f"{_p['name']}_len"
                        break
                _rf_decl, _rf_cap, _rf_free = _rf_buffer(_rf_count, _p_cleanup)
                _rf_call = (
                    _rf_decl
                    + _kernel_call_block(
                        f"{c_fn}({_st}, {_p_call}, results, {_rf_cap})",
                        nogil,
                        release=f"{_rf_free} {_p_cleanup}".strip(),
                    )
                    + _p_cleanup
                    + _keep
                )
            elif has_arg:
                _rf_parse = (
                    f"    PyObject *in_obj = NULL;\n"
                    f'    if (!PyArg_ParseTuple(args, "O", &in_obj))\n'
                    f"        return NULL;\n"
                    f"{_in_acq}"
                    f"    size_t n_in ="
                    f" (size_t)PyArray_SIZE(in_arr);\n"
                )
                _rf_decl, _rf_cap, _rf_free = _rf_buffer(
                    "n_in", "Py_DECREF(in_arr);"
                )
                _rf_call = (
                    _rf_decl
                    + _kernel_call_block(
                        f"{c_fn}({_st}, "
                        f"(const {arg_disp} *)PyArray_DATA(in_arr), n_in, "
                        f"results, {_rf_cap})",
                        nogil,
                        release=f"{_rf_free} Py_DECREF(in_arr);".strip(),
                    )
                    + "    Py_DECREF(in_arr);\n"
                )
            else:
                _rf_parse = ""
                _rf_decl, _rf_cap, _rf_free = _rf_buffer(None, "")
                _rf_call = _rf_decl + _kernel_call_block(
                    f"{c_fn}({_st}, results, {_rf_cap})",
                    nogil,
                    release=_rf_free,
                )
            # The stack array needs no release; a heap buffer is freed on
            # every way out once the kernel has filled it.
            _rf_rel = f" {_rf_free}" if _rf_free else ""
            _rf_tail = (
                f"{_rf_call}"
                # gh-1716: `n_out` records are read from `results[]`.
                + _coerce.returned_count_c(
                    "n_out", _rf_cap, f"{_who}.{name}", _rf_free
                )
                + "    PyObject *lst ="
                " PyList_New((Py_ssize_t)n_out);\n"
                + (
                    f"    if (!lst) {{{_rf_rel} return NULL; }}\n"
                    if _rf_free
                    else "    if (!lst) return NULL;\n"
                )
                + f"    for (size_t i = 0; i < n_out; i++) {{\n"
                f"        PyObject *tup ="
                f" Py_BuildValue({_bv});\n"
                f"        if (!tup)"
                f" {{{_rf_rel} Py_DECREF(lst); return NULL; }}\n"
                f"        PyList_SET_ITEM(lst, (Py_ssize_t)i, tup);\n"
                f"    }}\n"
                + (f"    {_rf_free}\n" if _rf_free else "")
                + "    return lst;\n"
            )
            # gh-2204: a kernel that stops at a full buffer, re-offered the
            # rest of its input block through `consumed_fn`. The loader
            # refused the key on a method with no block, so the block is
            # `x` beside params, else `in_arr`.
            _rf_consumed = _record.consumed_fn(m)
            if _rf_consumed and has_params:
                _rf_tail = _records_consumed_c(
                    _rf_buffer,
                    # gh-2327: after every call; keeping the object already
                    # kept is a no-op.
                    lambda cap, rel: (
                        _kernel_call_block(
                            f"{c_fn}({_st}, {_p_call}, results, {cap})",
                            nogil,
                            release=rel,
                        )
                        + _keep
                    ),
                    ptr="x",
                    count="x_len",
                    held=" ".join(_p_cleanup.split()),
                    consumed_fn=_rf_consumed,
                    who=f"{_who}.{name}",
                    build=_bv,
                    state=_st,
                )
            elif _rf_consumed:
                _rf_parse += (
                    f"    const {arg_disp} *_in ="
                    f" (const {arg_disp} *)PyArray_DATA(in_arr);\n"
                )
                _rf_tail = _records_consumed_c(
                    _rf_buffer,
                    lambda cap, rel: _kernel_call_block(
                        f"{c_fn}({_st}, _in, n_in, results, {cap})",
                        nogil,
                        release=rel,
                    ),
                    ptr="_in",
                    count="n_in",
                    held="Py_DECREF(in_arr);",
                    consumed_fn=_rf_consumed,
                    who=f"{_who}.{name}",
                    build=_bv,
                    state=_st,
                )
            # gh-1959: the single-record shape's twin -- with no input the
            # body parses nothing, so the wrapper is METH_NOARGS. A declared
            # param is parsed with keywords (gh-1961), as in that shape.
            _rf_cc = call_convention(
                _self_C, args=has_arg or has_params, kwds=has_params
            )
            wrapper = _in_dtype_helper + (
                f"static PyObject *\n"
                f"{wrapper_prefix}_{name}"
                f"({_rf_cc.params})\n"
                f"{{\n"
                f"{guard}"
                f"{_rf_parse}"
                f"{_rf_tail}"
                "}"
            )
            _rf_field_names = ", ".join(f["name"] for f in result_fields)
            # gh-1961 / gh-2001: every argument the binding takes, from the
            # one builder -- the input alone was a TypeError once the params
            # were parsed.
            _rf_call_arg = _demo_call_args(block_input=True)
            _rf_doc_lines = [
                f"{name}({', '.join(_doc_names)}) -> list[tuple]",
                "",
                # gh-642: this shape used to hard-code its summary, so a
                # documented record method's @brief reached the .pyi and not
                # help() — the only one of the four that ignored the header
                # outright rather than merely stopping at the brief.
                *_runtime_doc(f"Returns list of ({_rf_field_names},) tuples."),
                *_demo(
                    [
                        "",
                        "    >>> import numpy as np",
                        *_from_line,
                        _obj_line,
                        f"    >>> results = obj.{name}({_rf_call_arg})",
                        "    >>> isinstance(results, list)",
                        "    True",
                    ]
                ),
            ]
            pmd_lines.append(
                f'    {{"{name}", {_rf_cc.cast}{wrapper_prefix}_{name},'
                f" {_rf_cc.flags},\n"
                f"     {_build_ml_doc(_rf_doc_lines)}}},\n"
            )
        else:
            # Fixed-output wrapper
            _p_cleanup = ""
            # gh-238/gh-240: branches that parse via _build_params_parse are
            # positional-OR-keyword (the parse uses PyArg_ParseTupleAndKeywords
            # + a kwlist), so their wrapper takes `kwds` and the PyMethodDef
            # entry is METH_VARARGS | METH_KEYWORDS. The bare scalar `step`-shape
            # arg (no params) and the no-arg case stay positional / NOARGS --
            # `call_convention` (gh-1959) spells each.
            # gh-2191: the names the parse's `_kwlist` holds, in its order --
            # a release asks whether its count was PASSED, and where to look
            # is this list, not a re-derivation of it.
            _parsed_names: list[str] = []
            if has_params and has_arg:
                _x_param = {"name": "x", "type": arg_type}
                _combined = [_x_param] + list(params)
                parse_block, _p_call, _p_cleanup = _build_params_parse(
                    _combined, Component, enums, records, _sid, strict_in
                )
                _parsed_names = [p["name"] for p in _combined]
                call_args_c = f"{_st}, {_p_call}"
            elif has_params:
                parse_block, _p_call, _p_cleanup = _build_params_parse(
                    params, Component, enums, records, _sid, strict_in
                )
                _parsed_names = [p["name"] for p in params]
                call_args_c = f"{_st}, {_p_call}"
            elif has_arg and arg_type.endswith("[]"):
                _x_param = {"name": "x", "type": arg_type}
                parse_block, _p_call, _p_cleanup = _build_params_parse(
                    [_x_param], Component, enums, records, _sid, strict_in
                )
                call_args_c = f"{_st}, {_p_call}"
            elif has_arg:
                parse_block = _step_parse_block(arg_type, arg_meta) + "\n"
                call_args_c = f"{_st}, x"
            else:
                parse_block = ""
                call_args_c = _st
            _fx_cc = call_convention(
                _self_C,
                args=has_arg or has_params,
                kwds=has_params or (has_arg and arg_type.endswith("[]")),
            )

            if borrow:
                # gh-1312: the kernel LENDS a pointer into memory the state
                # owns. NULL is the author's failure signal -- end of stream,
                # interrupted, a count the state can never satisfy -- and it
                # is the only one, because there is no count to report.
                #
                # The view is built by the shared emitter, so this shape pins
                # and checks exactly as the `buf_field` property and
                # `get_<name>_view()` do. Read-only unless the author opts
                # out: a consumer holding a view into a producer's region has
                # no business writing through it.
                ret_body = (
                    # gh-1418: through the shared emitter, so `nogil` is
                    # honoured here as everywhere else. A blocking borrow is
                    # the shape that NEEDS it -- `wait(n)` sleeps until a
                    # producer supplies n samples, and with the GIL held a
                    # Python producer thread can never run, so the program
                    # hangs rather than failing.
                    _nogil_call(
                        f"{_borrow_elem} *",
                        "_p",
                        f"{c_fn}({call_args_c})",
                        nogil,
                        release=" ".join(_p_cleanup.split()),
                    )
                    + f"{_p_cleanup}"
                    + "    if (!_p) {\n"
                    # gh-1418 part 2: a NULL borrow used to mean exactly one
                    # thing. A blocking kernel gives up for reasons the
                    # caller must tell apart -- end of stream is what a
                    # consumer loop CATCHES, and a Ctrl-C is not bad input --
                    # so signals are checked first and a declared
                    # `status_fn` maps the rest. Every row returns, so
                    # falling through IS the fallback below: a status with no
                    # row keeps `none_on_empty`, else the blanket raise.
                    + _borrow.status_dispatch_c(
                        m, csym=csym, properties=properties
                    )
                    # A NULL borrow ALWAYS raises, declared `error` or not --
                    # there is no count to report and no empty array to hand
                    # back, so `raise_pair_of` supplies the undeclared
                    # defaults rather than this branch restating them (the
                    # reason that helper exists; see its import above).
                    #
                    # `empty_raise_c`, not `_rc_raise_c`: the latter appends
                    # `(rc=%lld)` and reads an `_rc` this shape does not have
                    # -- a borrow fails by returning NULL, and there is no
                    # code to print. Its docstring draws exactly this line.
                    # gh-1418: `none_on_empty` says NULL is a NORMAL answer,
                    # not a failure -- the non-blocking twin of a blocking
                    # wait, where "not yet" is what the caller asked about.
                    # Recorded and dropped before, so a `peek()` that should
                    # return None raised instead.
                    + (
                        "        Py_RETURN_NONE;\n"
                        if none_on_empty
                        else empty_raise_c(*raise_pair_of(m, name), indent=8)
                    )
                    + "    }\n"
                    + _keep
                    # gh-1426 A: remembered at the ONE place the view is
                    # built, so a release defaults to it without anything
                    # re-deriving the count.
                    + _borrow.record_count_c(m)
                    + _borrow_view_c(
                        "_p",
                        _borrow_count_c,
                        _borrow_np_enum,
                        descr_fn=_borrow_descr_fn,
                        writeable=borrow_writeable,
                        who=f"{_who}.{name}",
                        arr="_view",
                        indent="    ",
                    )
                    + "\n"
                )
            elif error_negative:
                # gh-805 §B: value-or-negative-error. The int IS the result on
                # success, so this returns it; only `< 0` raises.
                #
                # Deliberately a sibling of `status_return` rather than a flag
                # on it: the two make opposite claims about what the int
                # carries, and folding them into one branch is how a method
                # ends up raising on every successful call but id 0.
                #
                # The cleanup runs BEFORE the test, as it does on every other
                # path here — a param converter's borrow must be released
                # whether the call succeeded or failed.
                # The raise itself is `_rc_raise_c` — see there for why the
                # author's text is an argument and never the format string.
                ret_body = (
                    _nogil_call(
                        ret_disp,
                        "_rc",
                        f"{c_fn}({call_args_c})",
                        nogil,
                        release=" ".join(_p_cleanup.split()),
                    )
                    + f"{_p_cleanup}"
                    f"    if (_rc < 0) {{\n"
                    + _rc_raise_c(*_raise_pair)
                    + "    }\n"
                    + _keep
                    + f"    return {ret_meta['to_py']('_rc')};\n"
                )
            elif status_return or multi_output or out_type:
                # gh-2250: ONE emitter for every fixed-output result that is
                # not a plain return -- a status (gh-432), extra outputs
                # (gh-600), a fresh output array (gh-529), and any of them
                # together. They were three sibling branches, so a status
                # beside an output was a branch that rendered neither the
                # outputs nor the array: `too few arguments` in the user's
                # build. What the call returns is `fixed_result_parts`, the
                # list both `.pyi` producers and the runtime synopsis
                # annotate.
                #
                # gh-823 Ask D: `error` / `error_message` reach the status
                # raise, through `_rc_raise_c`, as on every raising shape.
                _pre = ""
                _out_arg = ""
                _cleanup_inline = _p_cleanup.replace("\n    ", " ").strip()
                if out_type:
                    out_disp = out_type
                    out_npy = _CTYPE_TO_NPY[out_type]
                    # Buffer size: `_outbuf.length`, the rule a module
                    # function's output is sized by too (gh-1888). The first
                    # array the call parses -- an array `arg_type` input
                    # first, as the `x` it is parsed as above; it was left
                    # out, so `--arg-type 'T[]'` allocated nothing and a
                    # kernel filling `x_len` wrote past it. Else the first
                    # integer param, so ``foo(n: int) -> ndarray`` (n
                    # samples requested) allocates n (gh-65).
                    _sizing = (
                        [{"name": "x", "type": arg_type}]
                        if has_arg and arg_type.endswith("[]")
                        else []
                    ) + list(params)
                    # With neither, still an empty output: refusing the row
                    # would stop `apply` on projects that declare one -- the
                    # frozen `stale_project` example's `shape` among them --
                    # so that call is left open in gh-2109.
                    raw_len = (
                        _outbuf.length(
                            _sizing, out_type=out_type, count_fallback=True
                        )
                        or "0"
                    )
                    if out_divisor > 1:
                        len_expr = f"({raw_len} / {out_divisor})"
                    else:
                        len_expr = raw_len
                    # gh-1710: the fallback length is the caller's int, which
                    # an unsigned param parses without a range check.
                    _pre += (
                        _coerce.output_size_c(
                            "_dim",
                            len_expr,
                            f"{_who}.{name}",
                            _cleanup_inline,
                        )
                        + f"    PyObject *_out ="
                        f" PyArray_EMPTY(1, &_dim, {out_npy}, 0);\n"
                        f"    if (!_out)"
                        f" {{{_cleanup_inline} return NULL; }}\n"
                    )
                    _out_arg = (
                        f", ({out_disp} *)PyArray_DATA((PyArrayObject *)_out)"
                    )
                _pre += "".join(
                    f"    {rt} out{i + 1} = {_CTYPE_META[rt]['zero']};\n"
                    for i, rt in enumerate(multi_output)
                )
                _extra_args = "".join(
                    f", &out{i + 1}" for i in range(len(multi_output))
                )
                _res = result_local(params)
                # The status is kept as `_rc`; the array replaces the return
                # (gh-529), and a `void` kernel has none to keep.
                _decl, _var = (
                    (ret_disp, "_rc")
                    if status_return
                    else ("", "")
                    if out_type or not ret_meta
                    else (ret_disp, _res)
                )
                _call_c = _nogil_call(
                    _decl,
                    _var,
                    f"{c_fn}({call_args_c}{_extra_args}{_out_arg})",
                    nogil,
                    release=(
                        " ".join(_p_cleanup.split())
                        + (" Py_DECREF(_out);" if out_type else "")
                    ),
                )
                _check_c = (
                    "    if (_rc != 0) {\n"
                    + ("        Py_DECREF(_out);\n" if out_type else "")
                    + _rc_raise_c(*_raise_pair)
                    + "    }\n"
                    if status_return
                    else ""
                )
                # Each value with how `Py_BuildValue` takes it: `N` steals
                # the new reference a conversion returns. `PyTuple_Pack`
                # took them with `O` and leaked one per value per call.
                _vals: "list[tuple[str, str]]" = []
                _parts = _fixed_result_parts(m)
                # The lead value, when there is one: the array, else the
                # kernel's return (`None` for a `void` kernel).
                for _kind, _ct in _parts[: len(_parts) - len(multi_output)]:
                    if _kind == "array":
                        _vals.append(("N", "_out"))
                    elif ret_meta:
                        _vals.append(("N", ret_meta["to_py"](_res)))
                    else:
                        _vals.append(("O", "Py_None"))
                for _i, _rt in enumerate(multi_output):
                    _vals.append(
                        (
                            "N",
                            _CTYPE_META[_rt]["to_py"](f"out{_i + 1}")
                            if _rt in _CTYPE_META
                            else f"PyLong_FromLong(out{_i + 1})",
                        )
                    )
                if not _vals:
                    _return_c = "    Py_RETURN_NONE;\n"
                elif len(_vals) == 1:
                    _return_c = f"    return {_vals[0][1]};\n"
                else:
                    _fmt = "".join(f for f, _ in _vals)
                    _pad = " " * 25
                    _return_c = (
                        f'    return Py_BuildValue("({_fmt})",\n'
                        + ",\n".join(f"{_pad}{v}" for _, v in _vals)
                        + ");\n"
                    )
                ret_body = (
                    f"{_pre}{_call_c}{_p_cleanup}{_check_c}{_keep}{_return_c}"
                )
            elif ret_meta:
                # gh-1512: `y` unless a param already took it.
                _res = result_local(params)
                ret_expr = ret_meta["to_py"](_res)
                ret_body = (
                    _nogil_call(
                        ret_disp,
                        _res,
                        f"{c_fn}({call_args_c})",
                        nogil,
                        release=" ".join(_p_cleanup.split()),
                    )
                    + f"{_p_cleanup}"
                    f"{_keep}"
                    f"    return {ret_expr};\n"
                )
            else:
                ret_body = (
                    _nogil_call(
                        "",
                        "",
                        f"{c_fn}({call_args_c})",
                        nogil,
                        release=" ".join(_p_cleanup.split()),
                    )
                    + f"{_p_cleanup}"
                    f"{_keep}"
                    f"    Py_RETURN_NONE;\n"
                )
            # gh-1310: same rule as the variable-output record path -- the
            # cached descr builder is file-scope and the wrapper calls it, so
            # the two travel together or the fragment does not compile.
            wrapper = (
                _in_dtype_helper
                + (
                    _record.dtype_c(
                        _sid,
                        record_dtype,
                        _record.fields(m, doc_blocks, records),
                    )
                    if (borrow and record_dtype)
                    else ""
                )
                + (
                    f"static PyObject *\n"
                    f"{wrapper_prefix}_{name}({_fx_cc.params})\n"
                    f"{{\n"
                    f"{guard}"
                    f"{parse_block}"
                    # gh-1426 A: after the args exist, before the call.
                    f"{_borrow.release_resolve_c(m, kwlist=_parsed_names)}"
                    f"{ret_body}"
                    f"}}"
                )
            )
            # gh-869: `status_return` claims the whole int as a status, so
            # the binding returns None — the `.pyi` has said so since gh-432
            # and this signature line still said `-> int`, on the one face
            # that sits next to a `Py_RETURN_NONE` body. Same defect as the
            # missing `Raises`, one line down: a doc face describing a
            # different function than the one it is attached to.
            _fix_ret_hint = (
                # gh-1312: a borrow returns an ndarray view, so its synopsis
                # line says so. Left out, this read `-> complex` above a
                # body that hands back an array -- the same "doc face
                # describing a different function" defect the comment above
                # is about.
                # gh-2183: and optional when a NULL borrow answers None.
                optional_ann(m, "ndarray")
                if borrow
                # gh-2250: the list the binding returns -- a `multi_output`
                # tuple read `-> ndarray` here.
                else fixed_result_ann(m, _pyi_scalar, lambda _t: "ndarray")
            )
            _fix_demo: list[str] = []
            if has_arg or has_params:
                _fix_demo += ["", "    >>> import numpy as np"]
            else:
                _fix_demo.append("")
            _fix_demo += [*_from_line, _obj_line]
            _call_str = _demo_call_args()
            if _raise_pair is not None:
                # gh-869: a `status_return` demo showed the status code as
                # the call's output; it has none (`-> None` above).
                # gh-2232: the same rule for every way to refuse, read from
                # `declared_raise` as the binding's raise is. A method that
                # declares one says its kernel may refuse the demo's input,
                # so the demo shows the call and claims no result -- the
                # stub's zero is a guess about a kernel jm has not seen. A
                # value is bound rather than echoed, so the line stays a
                # doctest that passes whenever the call does.
                _fix_demo.append(
                    f"    >>> {'' if _fix_ret_hint == 'None' else 'y = '}"
                    f"obj.{name}({_call_str})"
                )
            elif multi_output:
                # gh-2250: a tuple, which has no `ndim` -- the demo below
                # raised AttributeError on every multi_output method.
                _fix_demo.append(f"    >>> y = obj.{name}({_call_str})")
                _fix_demo.append("    >>> len(y)")
                _fix_demo.append(f"    {len(_fixed_result_parts(m))}")
            elif out_type or borrow:
                # gh-1312: a borrow demos like every other array-returning
                # shape. Without this it fell to the scalar branch below and
                # printed `0j` as the expected output of a call that returns
                # an array -- a doctest that is wrong as well as misleading.
                _fix_demo.append(f"    >>> y = obj.{name}({_call_str})")
                _fix_demo.append("    >>> y.ndim")
                _fix_demo.append("    1")
            elif return_type != "void" and return_type in _CTYPE_META:
                _py_z = _CTYPE_META[return_type].get("py_zero", "0")
                _fix_demo.append(f"    >>> obj.{name}({_call_str})")
                _fix_demo.append(f"    {_py_z}")
            else:
                _fix_demo.append(f"    >>> obj.{name}({_call_str})")
            _fix_doc_lines = [
                f"{name}({', '.join(_doc_names)}) -> {_fix_ret_hint}".rstrip(),
                "",
                # gh-1292: no default summary of its own. `f"{name}."` here
                # outranked `_numpy_sections`' capitalised name fallback, so
                # an undocumented method read `tune.` at runtime and `Tune.`
                # in both stubs -- the split gh-867 closed, reopened one
                # shape over.
                *_runtime_doc(""),
                *_demo(_fix_demo),
            ]
            pmd_lines.append(
                f'    {{"{name}", {_fx_cc.cast}{wrapper_prefix}_{name},'
                f" {_fx_cc.flags},\n"
                f"     {_build_ml_doc(_fix_doc_lines)}}},\n"
            )

        method_c_parts.append(wrapper)

        # pyi stub for this method
        # gh-642: built once, at the top of the loop, and read by both faces.
        # `doc_params` is the args the docstring's Parameters section
        # documents, and since gh-1042 that includes the binding-level
        # `count` / `out=` the signature carries — appended above, where the
        # decision is made once for both faces.
        param_parts = list(_sig_parts)
        ret_ann = _ret_ann
        # gh-219: single-output variable_output methods take an optional
        # `out=` buffer and expose a <verb>_max_out() sibling. A method whose
        # params carry an array is eligible too, beside other params since
        # gh-1998 -- see `_outbuf.why_not`.
        # gh-805 §E: mirrors `_enable_out` above, which now offers the
        # buffer for a structured result too. Kept adjacent and kept in
        # step: a stub advertising an out= the binding rejects, or a
        # binding accepting one the stub hides, is the same defect in
        # either direction. (Hoisted for gh-1042.)
        # gh-527: a variable_output method with no input to size from is the
        # generator shape -- the parse block above seeds a leading `count` for
        # it (kwlist {"count", "out"} when an out= is offered, a positional
        # "|n" otherwise). The seed is `_count_init`: `1`, or the method's
        # `count_default` (gh-657). The stub
        # omitted it entirely, so `obj.run(4)` -- the call that actually works
        # -- failed to type-check while `obj.run(out=...)` passed. `count`
        # precedes `out` to match the kwlist order. The peer generator in
        # _stubs.py (the module-aggregated .pyi) carries the same rule.
        # gh-1042: hoisted above, so the signature and the documented list
        # cannot disagree about which binding args exist.
        if _stub_count_arg:
            # gh-1051: shared with the module-aggregated peer in `_stubs`,
            # which hard-coded `1` here and so advertised a default the
            # binding does not use. An integer literal now renders as itself.
            param_parts.append(
                f"{_count_kw}: int"
                f" = {_gluedoc.count_stub_default(_count_default)}"
            )
        if _stub_enable_out:
            param_parts.append(f"out: {_out_ann} | None = None")
        sig = ", ".join(param_parts)
        # gh-651: one renderer, shared with the module-aggregated .pyi. This
        # path used to build the numpy layout by hand and disagreed with its
        # peer on three things for the same manifest and the same header —
        # the extended description was dropped entirely, a Parameters entry
        # was a bare `x` with no `: type` (which numpydoc does not read as a
        # parameter at all), and the blank line between sections was eight
        # spaces of trailing whitespace.
        #
        # skeleton_fallback keeps this path's own answer to a *different*
        # question: an undocumented member here has always kept its section
        # skeleton, where the aggregated .pyi collapses to a one-line stub.
        _pyi_doc = (
            "\n".join(
                render_numpy_doc(
                    _block,
                    name,
                    _doc_params,
                    ret_ann,
                    m.get("doc") or "",
                    indent=8,
                    skeleton_fallback=True,
                    raises=_raises_doc,
                    none_when=empty_is_none_doc(m),
                    param_defaults=_gluedoc.binding_param_docs(
                        _count_kw,
                        batch=batch,
                        count=_stub_count_arg,
                        out=_stub_enable_out,
                        out_cols=bool(m.get("out_cols")),
                        short_out=bool(m.get("short_out")),
                    ),
                    authored_doc=m.get("doc") or "",
                    param_docs=authored_param_docs(m),
                )
            )
            + "\n"
        )
        stub = (
            f"    def {name}(self, {sig}) -> {ret_ann}:\n{_pyi_doc}"
            if sig
            else f"    def {name}(self) -> {ret_ann}:\n{_pyi_doc}"
        )
        pyi_lines.append(stub)
        # gh-2259: a batch method's `out=` has no `_max_out()` sibling -- its
        # result is exactly as long as its input or count.
        if _stub_enable_out and not batch:
            # gh-607: mirror the same count parameter as the C wrapper.
            # gh-761: including when the header says there isn't one — the
            # stub must agree with the binding, and the binding follows the
            # prototype.
            _stub_moc_decl, _stub_moc_name = _max_out_count_param_ctx(
                has_arg, has_params, params, no_count
            )
            if max_out_is_state_only(doc_blocks, f"{c_fn}_max_out"):
                _stub_moc_decl, _stub_moc_name = "", None
            # gh-684: the header wins; _gluedoc supplies the fallback.
            _mo_doc_lines = _max_out_doc(
                component,
                name,
                _stub_moc_name,
                m.get("max_out", 0),
                lambda k: (doc_blocks or {}).get(k),
                c_fn=c_fn,
                csym=csym,
                elements_per_sample=_vo_e,
            ).pyi_doc(raises=_host_raises or None)
            _mo_sig = (
                f"self, {_stub_moc_name}: int" if _stub_moc_name else "self"
            )
            pyi_lines.append(
                f"    def {name}_max_out({_mo_sig}) -> int:\n"
                + "\n".join(_mo_doc_lines)
                + "\n"
            )

    # ── serializable: generate the state-blob binding (gh-400) ──────────────
    # Calls the C triplet (size_t <c>_state_bytes(const T*); void
    # <c>_get_state(const T*, void*); int <c>_set_state(T*, const void*)) — the
    # elastic / pure-transducer face, sibling to reset. An object's scaffold
    # declares and defines it (gh-1509, make_serializable_core_ctx); a handle
    # module's is the author's.
    if serializable:
        _c_funcs, _pmd, _pyi = serializable_triplet_parts(
            component,
            Component,
            wrapper_prefix,
            csym=csym,
        )
        method_c_parts.extend(_c_funcs)
        pmd_lines.append(_pmd)
        pyi_lines.append(_pyi)

    # ── extra_methods: rows for functions the author wrote (gh-1997) ────────
    # After every generated row, in declared order. The C is the author's, in
    # the object's `_extra.c`; the prototypes go to the caller, which puts
    # them above the type (`extra_method_protos`).
    for _row in extra_methods or ():
        # The whole entry from the shared emitter, whose doc is the
        # manifest's through `authored_c_doc` (gh-1039's rule).
        _extra_pmd = _extramethods.method_def_row(_row)
        pmd_lines.append(_extra_pmd)
        pyi_lines.append("\n".join(_extramethods.pyi_member(_row)) + "\n")
    _protos = _extramethods.prototypes(list(extra_methods or ()), "_extra.c")

    method_decls = "\n\n".join(decl_lines) + "\n" if decl_lines else ""

    _method_bench_blocks = [
        _bench_method_block(
            component, m, records, csym=csym, doc_blocks=doc_blocks
        )
        for m in methods
    ]
    _filled = [b for b in _method_bench_blocks if b]
    bench_methods_timing_block = "\n" + "\n\n".join(_filled) if _filled else ""
    # gh-840: the helper and the timer locals exist to serve the timing
    # block. With no block they were dead code the compiler flagged, so they
    # follow it rather than being unconditional, and the TODO carries a
    # copy-pasteable version of both.
    _has_timing = bool(_filled)
    return {
        "method_decls": method_decls,
        "extra_buf_fields": "".join(buf_fields),
        "extra_buf_free": "".join(buf_free),
        "extra_buf_alloc": "".join(buf_alloc),
        "extra_methods_c": "\n\n".join(method_c_parts),
        "extra_methods_pymethoddef": "".join(pmd_lines),
        "pyi_extra_methods": (
            "\n" + "\n\n".join(pyi_lines) + "\n" if pyi_lines else ""
        ),
        # gh-646: the record classes a single-record method returns, declared
        # at module level above the component class that returns them. Empty
        # for every project without one, so the slot costs nothing.
        "pyi_records": _pyi_records(methods, doc_blocks),
        # gh-1264: (sid, public_name) pairs the module init must create the
        # structseq type for and register under -- see _record.registrations.
        # Consumed by the module-init assembly in _render.py, not by the
        # generic `<<key>>` substitution (a list value, so render() skips
        # it), which is also why an empty list here costs nothing. doc_blocks
        # (gh-1270) makes each entry's derived `.doc` the SAME text
        # `pyi_records` above just rendered, not a second computation of it.
        "record_registrations": _record.registrations(
            methods, wrapper_prefix, doc_blocks
        ),
        "bench_methods_timing_block": bench_methods_timing_block,
        "bench_elapsed_helper": (_BENCH_ELAPSED_HELPER if _has_timing else ""),
        "bench_timer_decls": _BENCH_TIMER_DECLS if _has_timing else "",
        "bench_todo": (
            "" if _has_timing else _bench_todo(component, methods, csym=csym)
        ),
        "varargs_binding_files": varargs_binding_files,
        # gh-1997: the block ends in a blank line, ready to sit above the
        # type banner in a standalone `_ext.c`; the module aggregator strips
        # it to sit above the fragment's `#include`.
        "extra_method_protos": (
            _protos.strip("\n") + "\n\n" if _protos else ""
        ),
        **(
            {
                "builtin_reset_c": "",
                "builtin_reset_pmd": "",
                "builtin_reset_decl": "",
                # gh-131: suppress the template's hardcoded reset() stub
                # when the user declared a [[methods]] entry named "reset";
                # pyi_extra_methods will contain the user-defined variant.
                "builtin_reset_pyi": "",
            }
            if user_has_reset
            else {}
        ),
    }


# ---------------------------------------------------------------------------
# make_properties_ctx
# ---------------------------------------------------------------------------


def _enum_name_or_fail(
    where: str, name: str, enums: dict[str, list[str]] | None
) -> str:
    """Validate one ``enum = "<name>"`` reference against the SSOT.

    A ``None`` registry means "enums are unsupported on this path" (the
    ``jm bind`` reflection path, which has no manifest to read ``[[enum]]``
    from). Declaring one there is inert rather than fatal, so a bound render
    stays byte-identical to before gh-519.

    Shared by properties (gh-519) and method parameters (gh-1021) so a typo
    reads the same whichever declared it — and so the two cannot come to
    disagree about what a valid reference is.

    Raises
    ------
    ValueError
        If *name* is absent from the registry. Raising here turns a typo into
        a jm diagnostic instead of an undeclared ``_enum_<typo>`` identifier
        in the user's compiler.
    """
    if not name or enums is None:
        return ""
    if name not in enums:
        known = ", ".join(sorted(enums)) or "(none declared)"
        raise ValueError(
            f"{where}: unknown enum '{name}'. "
            f"Declare it as a top-level [[enum]] with that name. "
            f"Known enums: {known}"
        )
    return name


def method_param_enums(
    methods: list[dict] | None, enums: dict[str, list[str]] | None
) -> list[str]:
    """``[[enum]]`` names referenced by method PARAMETERS (gh-1021).

    First-reference order, deduped — the same shape `make_properties_ctx`
    collects for properties, so :func:`make_enum_tables_ctx` can concatenate
    the two.

    Also the home of the default check: an enum parameter's ``default`` is a
    choice STRING, not the int index, because the generated C seeds the
    parameter's local with it and hands that to the lookup. A manifest that
    kept an int default from before the enum was declared would otherwise
    compile fine and raise ``ValueError: invalid <p> '0'`` on any call that
    omitted the argument — a runtime failure for a declaration jm can read.
    """
    used: list[str] = []
    for m in methods or []:
        for prm in m.get("params") or []:
            where = (
                f"method '{m.get('name', '?')}' param '{prm.get('name', '?')}'"
            )
            name = _enum_name_or_fail(where, prm.get("enum") or "", enums)
            if not name:
                continue
            dflt = prm.get("default")
            if dflt and enums is not None and dflt not in enums[name]:
                choices = ", ".join(enums[name])
                raise ValueError(
                    f"{where}: default '{dflt}' is not a choice of enum "
                    f"'{name}'. An enum parameter defaults to its choice "
                    f"STRING, not the C index. Choices: {choices}"
                )
            if name not in used:
                used.append(name)
    return used


def make_enum_tables_ctx(
    component: str,
    Component: str,
    methods: list[dict] | None = None,
    properties: list[dict] | None = None,
    enums: dict[str, list[str]] | None = None,
) -> dict:
    """Fill the ``enum_tables`` slot: every ``[[enum]]`` this type indexes.

    Emitted ONCE, for both consumers, into a slot that precedes all three C
    slots that reference the tables — ``getter_setter_methods_c``,
    ``extra_methods_c`` (where a method parameter's lookup lands, gh-1021) and
    ``getset_def`` (where a property's getter lands, gh-519). gh-519 put them
    inside the last of those, which worked while properties were the only
    consumer and is a use-before-definition the moment a method parameter
    references one.

    One emission rather than one per consumer: both index the same
    ``_enum_<Component>_<name>`` symbols (see :func:`_enum_symbols`), so a
    second copy would be a duplicate definition in the same TU, not a
    fallback.

    Returns ``{"enum_tables": ..., "enum_publish_entries": ...}``, empty when
    nothing references an enum -- so a type without one renders
    byte-identically to before. The entries are what the type publishes to
    Python (gh-2344), read by :func:`enum_publication` for the stub too.
    """
    used, looked_up, decoded = _enum_usage(
        component, Component, methods, properties, enums
    )
    if not used:
        return {"enum_tables": "", "enum_publish_entries": []}
    return {
        "enum_tables": _render_enum_tables(
            Component, used, enums or {}, looked_up, decoded
        ),
        "enum_publish_entries": _enumc.publication(
            used, enums, prefix=Component
        ),
    }


def _enum_usage(
    component: str,
    Component: str,
    methods: list[dict] | None,
    properties: list[dict] | None,
    enums: dict[str, list[str]] | None,
) -> tuple[list[str], list[str], list[str]]:
    """The enums a type's tables cover: ``(used, looked_up, decoded)``.

    One predicate for both the emitted tables (:func:`make_enum_tables_ctx`)
    and what the type publishes to Python (:func:`enum_publication`), so the
    two cannot name different enums.
    """
    used = method_param_enums(methods, enums)
    # gh-1745: every method parameter is looked up; a property only when its
    # setter is, which `_property_lookup_enum` decides for both halves.
    looked_up = list(used)
    # gh-1748: no method parameter is decoded; a property's getter decodes
    # the enum `_property_enum` names -- the same call `make_properties_ctx`
    # makes to decide whether the getter emits `_enumc.decode_c`.
    decoded: list[str] = []
    for prp in properties or []:
        name = _property_enum(component, Component, prp, enums)
        if name and name not in used:
            used.append(name)
        if name and name not in decoded:
            decoded.append(name)
        lk = _property_lookup_enum(component, Component, prp, enums)
        if lk and lk not in looked_up:
            looked_up.append(lk)
    return used, looked_up, decoded


def enum_publication(
    component: str,
    Component: str,
    methods: list[dict] | None,
    properties: list[dict] | None,
    enums: dict[str, list[str]] | None,
) -> list[dict]:
    """What one object publishes to Python (gh-2344), as ``_enumc.publication``.

    The stub asks this for the same object :func:`make_enum_tables_ctx` was
    given, so the ``.pyi`` declares exactly the attributes the extension
    adds. The entries' C table names are not read by the stub, so *Component*
    only has to be the name the object's own tables would use.
    """
    used, _, _ = _enum_usage(component, Component, methods, properties, enums)
    return _enumc.publication(used, enums, prefix=Component)


def _render_enum_tables(
    Component: str,
    used: list[str],
    enums: dict[str, list[str]],
    looked_up: list[str],
    decoded: list[str],
) -> str:
    """The ``_enum_index_<Component>`` helper + one table per enum in *used*.

    `_enumc.render_tables` in this type's namespace. This was a second copy
    of it -- byte-identical, which is the only reason gh-1450's constant
    tables did not reach objects through one and not the other. The helper
    is emitted only when *looked_up* is non-empty (gh-1745), and a
    constant-bound enum's ``_name`` reverse lookup only when it is in
    *decoded* (gh-1748).
    """
    return _enumc.render_tables(
        used,
        enums,
        prefix=Component,
        include_string_h=True,
        looked_up=looked_up,
        decoded=decoded,
    )


def _property_enum(
    component: str,
    Component: str,
    p: dict,
    enums: dict[str, list[str]] | None,
) -> str:
    """Resolve one property's ``enum = "<name>"`` declaration, or ``""``.

    A ``None`` registry means "enums are unsupported on this path" (the
    ``jm bind`` reflection path, which has no manifest to read ``[[enum]]``
    from). Declaring ``enum`` there is inert rather than fatal, so the bound
    render stays byte-identical to before gh-519.

    Raises
    ------
    ValueError
        If the enum names something absent from the ``[[enum]]`` registry, or
        if it is combined with ``buf_field`` (an array of enums has no
        meaning). Raising here turns a typo into a jm diagnostic instead of an
        undeclared ``_enum_<typo>`` identifier in the user's compiler.
    """
    name = _enum_name_or_fail(
        f"{component}.{p['name']}", p.get("enum") or "", enums
    )
    if not name:
        return ""
    if p.get("buf_field"):
        raise ValueError(
            f"{component}.{p['name']}: `enum` is not supported on a "
            f"buf_field property — an array of enum strings has no "
            f"decoded form. Drop `enum` or drop `buf_field`."
        )
    return name


def _property_lookup_enum(
    component: str,
    Component: str,
    p: dict,
    enums: dict[str, list[str]] | None,
) -> str:
    """The ``[[enum]]`` a property's SETTER looks a choice up in, or ``""``.

    gh-1745. A property's getter only decodes -- it indexes the table -- so
    the ``_enum_index_<Component>`` lookup has a caller only when the
    property is writable. This is the one predicate for both halves: the
    setter emits its :func:`_enumc.validate_c` block when it answers a name,
    and :func:`make_enum_tables_ctx` emits the lookup when any property or
    method parameter does. A read-only enum property used to get the lookup
    anyway, which ``-Wall`` reports as an unused function.
    """
    if not p.get("writable", False):
        return ""
    return _property_enum(component, Component, p, enums)


def validate_property_error(
    component: str,
    p: dict,
    state_var_names: "frozenset[str]" = frozenset(),
    *,
    csym: str,
) -> None:
    """Refuse an ``error`` / ``error_message`` the setter cannot honour.

    gh-2182. ``error`` declares that a non-zero return from the property's C
    setter refuses the value, and the binding raises it. That needs a C
    setter call whose return the binding can test, so every shape without
    one is refused, naming the way out, before anything is written:

    - a read-only property has no setter at all;
    - ``field = true`` assigns the member directly -- there is no C call;
    - a property named for a state field calls that field's accessor, which
      jm declares returning ``void``, so testing it would not compile;
    - ``error_message`` without ``error`` is the text of an exception
      nothing raises.

    The exception name is checked against `_config.ERROR_CATEGORIES`, the
    list a method's ``error`` is checked against, because ``PyExc_<name>``
    is emitted verbatim into C.

    Shared by `make_properties_ctx`, which every render of the binding goes
    through, and `_property.run`, so `jm property` refuses before the
    manifest is written.

    Raises
    ------
    Refusal
        Naming the property and what to change.
    """
    category = p.get("error") or ""
    if not category and not p.get("error_message"):
        return
    where = f"{component}.{p['name']}"
    setter = CSYM.property_setter(csym, p["name"])
    if not category:
        raise Refusal(
            f"{where}: error_message is the text a refused assignment raises "
            f'with, so it needs error as well (e.g. error = "ValueError").'
        )
    if category not in C.ERROR_CATEGORIES:
        raise Refusal(
            f"{where}: error {category!r} is not a known exception category. "
            f"Choose one of: {', '.join(sorted(C.ERROR_CATEGORIES))}."
        )
    if not p.get("writable"):
        raise Refusal(
            f"{where}: error is raised when the setter refuses a value, and "
            f"a read-only property has no setter. Add writable = true, or "
            f"drop error."
        )
    if p.get("field"):
        raise Refusal(
            f"{where}: error needs a C setter whose return jm can test, and "
            f"field = true assigns the struct member directly. Drop field "
            f"and declare {setter} returning int."
        )
    if p["name"] in state_var_names:
        raise Refusal(
            f"{where}: the setter this property calls, {setter}, is the "
            f"accessor of the state field '{p['name']}', which jm declares "
            f"returning void, so there is no status to test. Give the "
            f"property its own name and declare its setter returning int."
        )


def validate_container_property(component: str, p: dict) -> None:
    """Reject an incoherent container property (gh-543).

    Raises ``ValueError`` so the caller turns it into a jm diagnostic. Every
    check here would otherwise surface as a compiler error in generated code
    the user did not write.
    """
    pname = p["name"]
    kind = p.get("type", "")
    where = f"{component}.{pname}"
    vtype = p.get("value_type") or T.OBJECT_VALUE_TYPE
    if not T.is_valid_value_type(vtype):
        supported = ", ".join(sorted(T._CTYPE_META))
        raise ValueError(
            f"{where}: unsupported value_type '{vtype}'. Use "
            f"'{T.OBJECT_VALUE_TYPE}' (value_fn returns a PyObject *) or one "
            f"of: {supported}"
        )
    # A dict always has a key_fn -- `CSYM.container_accessors` defaults it,
    # so there is nothing to require here, only a misuse to reject.
    if kind != "dict" and p.get("key_fn"):
        raise ValueError(
            f"{where}: key_fn is meaningful only for a dict property; "
            f"a {kind} is keyed by position. Drop key_fn, or use type = "
            f'"dict".'
        )
    if p.get("writable"):
        raise ValueError(
            f"{where}: a container property is read-only. jm generates the "
            f"container fresh on every read, so a setter would mutate a copy "
            f"the caller never sees. Expose a method that mutates the core "
            f"instead."
        )
    for clash in ("field", "buf_field", "expr", "enum"):
        if p.get(clash):
            raise ValueError(
                f"{where}: `{clash}` cannot be combined with a "
                f"container property -- the value comes from value_fn, not "
                f"from a struct member or an expression."
            )


#: gh-2347: the keys a `json` property refuses -- each names another backing
#: (a struct member, a buffer, an expression, a container's accessors, a
#: codec, a capsule) or a decode (`enum`) that the JSON text replaces.
_JSON_CLASHES = (
    "field",
    "buf_field",
    "expr",
    "enum",
    "codec",
    "capsule",
    "value_type",
    "count_fn",
    "key_fn",
    "value_fn",
    "entry_fn",
)


def validate_json_property(component: str, p: dict) -> None:
    """Refuse an incoherent `json` property, and `py_type` on any other kind.

    gh-2347. Raised at render time, before anything is written, so a
    misdeclaration is a jm diagnostic rather than C the author never wrote.

    >>> validate_json_property("r", {"name": "a", "type": "json"})
    >>> validate_json_property("r", {"name": "a", "type": "json",
    ...                              "writable": True})  # doctest: +ELLIPSIS
    Traceback (most recent call last):
    ...
    just_makeit._report.Refusal: r.a: a json property is read-only. ...
    >>> validate_json_property("r", {"name": "a", "type": "double",
    ...                              "py_type": "int"})  # doctest: +ELLIPSIS
    Traceback (most recent call last):
    ...
    just_makeit._report.Refusal: r.a: py_type annotates a json property only; ...
    """
    where = f"{component}.{p.get('name', '?')}"
    if not T.is_json_type(str(p.get("type", ""))):
        if p.get("py_type"):
            raise Refusal(
                f"{where}: py_type annotates a json property only; a "
                f"{p.get('type')} property's stub type comes from its C type. "
                f'Drop py_type, or use type = "json".'
            )
        return
    if p.get("writable"):
        raise Refusal(
            f"{where}: a json property is read-only. The value is parsed "
            f"fresh from the C getter's text on every read, so a setter "
            f"would mutate a copy the caller never sees. Expose a method "
            f"that mutates the core instead."
        )
    for clash in _JSON_CLASHES:
        if p.get(clash):
            raise Refusal(
                f"{where}: `{clash}` cannot be combined with a json property "
                f"-- the value is the JSON text its getter returns."
            )
    if p.get("error") or p.get("error_message"):
        raise Refusal(
            f"{where}: a json property has no setter, so `error` has nothing "
            f"to test. A NULL from the getter already raises."
        )
    py_type = p.get("py_type")
    if py_type:
        why = T.json_py_type_error(str(py_type))
        if why:
            raise Refusal(
                f"{where}: py_type {py_type!r} {why}. Use builtin generics "
                f'and Any, e.g. "list[dict[str, Any]]".'
            )


def _json_getter(
    Component: str, pname: str, getter_fn: str, guard: str
) -> str:
    """The getter of a `json` property (gh-2347).

    The C getter returns malloc'd JSON text the binding owns: parsed with
    ``json.loads``, then ``free``d on every path. ``NULL`` raises
    ``RuntimeError`` naming the getter; text that does not parse (or is not
    UTF-8) raises ``ValueError`` naming the property, with the decoder's
    message.
    """
    return (
        f"static PyObject *\n"
        f"{Component}_getprop_{pname}"
        f"({Component}Object *self, void *Py_UNUSED(closure))\n"
        f"{{\n"
        f"{guard}"
        f"    char *_js = {getter_fn}(self->handle);\n"
        f"    if (!_js) {{\n"
        f"        PyErr_SetString(PyExc_RuntimeError,\n"
        f'            "{pname}: {getter_fn} returned NULL");\n'
        f"        return NULL;\n"
        f"    }}\n"
        f'    PyObject *_mod = PyImport_ImportModule("json");\n'
        f"    if (!_mod) {{\n"
        f"        free(_js);\n"
        f"        return NULL;\n"
        f"    }}\n"
        f'    PyObject *_v = PyObject_CallMethod(_mod, "loads", "s", _js);\n'
        f"    Py_DECREF(_mod);\n"
        f"    free(_js);\n"
        f"    if (!_v && PyErr_ExceptionMatches(PyExc_ValueError)) {{\n"
        f"        /* JSONDecodeError and UnicodeDecodeError are both\n"
        f"           ValueErrors: re-raise naming the property. */\n"
        f"#if PY_VERSION_HEX >= 0x030C0000\n"
        f"        PyObject *_e = PyErr_GetRaisedException();\n"
        f"#else\n"
        f"        PyObject *_t, *_e, *_tb;\n"
        f"        PyErr_Fetch(&_t, &_e, &_tb);\n"
        f"        PyErr_NormalizeException(&_t, &_e, &_tb);\n"
        f"        Py_XDECREF(_t);\n"
        f"        Py_XDECREF(_tb);\n"
        f"#endif\n"
        f"        PyErr_Format(PyExc_ValueError,\n"
        f'            "{pname}: {getter_fn} returned malformed JSON: %S",\n'
        f"            _e ? _e : Py_None);\n"
        f"        Py_XDECREF(_e);\n"
        f"    }}\n"
        f"    return _v;\n"
        f"}}"
    )


def _container_getter(
    component: str,
    Component: str,
    p: dict,
    guard: str,
    cdc: dict | None = None,
    *,
    csym: str,
) -> tuple[str, list[str]]:
    """Render a container property's getter, plus the decls it needs.

    Returns ``(getter_source, core_header_decls)``. The header decls are the
    accessors that are plain C and so belong in the sacred ``_core.h``; a
    ``PyObject *``-returning ``value_fn`` is *not* among them (it needs
    ``Python.h``) and is instead forward-declared inline, immediately above the
    getter. That forward declaration is mandatory rather than tidy: a
    hand-written ``*_extra.c`` is ``#include``d *after* the object fragment in
    the same translation unit, so the definition is not yet visible here.
    """
    pname = p["name"]
    kind = p["type"]
    fns = CSYM.container_accessors(csym, p)
    vtype = p.get("value_type") or T.OBJECT_VALUE_TYPE
    state_t = f"const {csym}_state_t *"

    decls = [
        f"/**\n"
        f" * @brief Number of entries in {pname}.\n"
        f" * @param state  Must be non-NULL.\n"
        f" */\n"
        f"size_t {fns['count_fn']}({state_t}state);"
    ]
    if kind == "dict":
        decls.append(
            f"/**\n"
            f" * @brief Key of entry @p i of {pname}, or NULL if out of range.\n"
            f" * @param state  Must be non-NULL.\n"
            f" * @param i      Entry index, 0-based.\n"
            f" */\n"
            f"const char *{fns['key_fn']}({state_t}state, size_t i);"
        )

    fwd = ""
    # Statements that must run before the value conversion (a NULL guard for a
    # pointer-valued accessor); empty for every other value type.
    value_pre = ""
    if cdc is not None:
        # gh-554: a codec property. jm generates the per-entry decode from the
        # [codec.X] table (the read mirror of the write pack), so there is no
        # hand-written value_fn — the value comes from a static decode helper
        # (emitted inline as `fwd`) over the entry_fn cursor.
        fwd, value_expr, _entry_decls = _codec.render_decode(
            component,
            Component,
            p,
            cdc,
            csym=csym,
        )
        decls.extend(_entry_decls)
    elif vtype == T.OBJECT_VALUE_TYPE:
        # The escape hatch: the core owns the conversion, so the value comes
        # back already a PyObject *. It must return a NEW reference and set an
        # exception when it returns NULL.
        value_expr = f"{fns['value_fn']}(self->handle, _i)"
        fwd = (
            f"/* gh-543: implemented by hand (Python-aware, so it cannot live"
            f" in the pure-C\n"
            f" * core). Must return a new reference, or NULL with an"
            f" exception set. */\n"
            f"PyObject *{fns['value_fn']}({state_t}state, size_t i);\n\n"
        )
    else:
        _vmeta = _CTYPE_META[vtype]
        # Same pointer-spacing idiom the parse builders use (_render.py:622):
        # `const char *` already ends in the star, so no separating space.
        _vdisp = vtype
        if not _vdisp.endswith("*"):
            _vdisp += " "
        if _vdisp.endswith("*"):
            # A pointer-valued accessor is the one typed case that can hand
            # back NULL, and the conversion would dereference it -- for the
            # only such type today, `const char *`, PyUnicode_FromString(NULL)
            # reaches strlen(NULL) and crashes. Same class as the gh-521
            # unchecked table index, so it gets the same treatment: bind the
            # call to a local, check it, then convert. `_r` is declared inside
            # the loop body, so it cannot collide with the value local.
            value_pre = (
                f"        {_vdisp}_r = "
                f"{fns['value_fn']}(self->handle, _i);\n"
                f"        if (!_r) {{\n"
                f"            PyErr_Format(PyExc_RuntimeError,\n"
                f'                "{pname}: {fns["value_fn"]} returned NULL'
                f' at index %zu", _i);\n'
                f"            Py_DECREF(_c);\n"
                f"            return NULL;\n"
                f"        }}\n"
            )
            value_expr = _vmeta["to_py"]("_r")
        else:
            value_expr = _vmeta["to_py"](
                f"{fns['value_fn']}(self->handle, _i)"
            )
        decls.append(
            f"/**\n"
            f" * @brief Value of entry @p i of {pname}.\n"
            f" * @param state  Must be non-NULL.\n"
            f" * @param i      Entry index, 0-based.\n"
            f" */\n"
            f"{_vdisp}{fns['value_fn']}({state_t}state, size_t i);"
        )

    head = (
        f"{fwd}"
        f"static PyObject *\n"
        f"{Component}_getprop_{pname}"
        f"({Component}Object *self, void *Py_UNUSED(closure))\n"
        f"{{\n"
        f"{guard}"
        f"    size_t _n = {fns['count_fn']}(self->handle);\n"
    )

    if kind == "dict":
        body = (
            f"    PyObject *_c = PyDict_New();\n"
            f"    if (!_c) return NULL;\n"
            f"    for (size_t _i = 0; _i < _n; _i++) {{\n"
            f"        const char *_k = {fns['key_fn']}(self->handle, _i);\n"
            f"        if (!_k) {{\n"
            f"            PyErr_Format(PyExc_RuntimeError,\n"
            f'                "{pname}: {fns["key_fn"]} returned NULL'
            f' at index %zu", _i);\n'
            f"            Py_DECREF(_c);\n"
            f"            return NULL;\n"
            f"        }}\n"
            f"{value_pre}"
            f"        PyObject *_v = {value_expr};\n"
            f"        if (!_v) {{\n"
            f"            Py_DECREF(_c);\n"
            f"            return NULL;\n"
            f"        }}\n"
            f"        if (PyDict_SetItemString(_c, _k, _v) != 0) {{\n"
            f"            Py_DECREF(_v);\n"
            f"            Py_DECREF(_c);\n"
            f"            return NULL;\n"
            f"        }}\n"
            f"        Py_DECREF(_v);\n"
            f"    }}\n"
            f"    return _c;\n"
            f"}}"
        )
    else:
        # PyList_New/PyTuple_New zero every slot, so Py_DECREF on a
        # part-filled container is safe; SET_ITEM steals, so the success path
        # must NOT decref the value.
        _New = "PyList_New" if kind == "list" else "PyTuple_New"
        _SET = "PyList_SET_ITEM" if kind == "list" else "PyTuple_SET_ITEM"
        body = (
            f"    PyObject *_c = {_New}((Py_ssize_t)_n);\n"
            f"    if (!_c) return NULL;\n"
            f"    for (size_t _i = 0; _i < _n; _i++) {{\n"
            f"{value_pre}"
            f"        PyObject *_v = {value_expr};\n"
            f"        if (!_v) {{\n"
            f"            Py_DECREF(_c);\n"
            f"            return NULL;\n"
            f"        }}\n"
            f"        {_SET}(_c, (Py_ssize_t)_i, _v);\n"
            f"    }}\n"
            f"    return _c;\n"
            f"}}"
        )
    return head + body, decls


def _container_pyi(p: dict, cdc: dict | None = None) -> str:
    """Annotation for a container property (gh-543; gh-554 codec)."""
    if cdc is not None:
        # gh-554: the value is the codec's Python union (read form, `list`).
        elem = _codec.property_py_type(p, cdc)
    else:
        vtype = p.get("value_type") or T.OBJECT_VALUE_TYPE
        elem = "Any" if vtype == T.OBJECT_VALUE_TYPE else _pyi_scalar(vtype)
    kind = p["type"]
    if kind == "dict":
        return f"dict[str, {elem}]"
    if kind == "list":
        return f"list[{elem}]"
    return f"tuple[{elem}, ...]"


def make_properties_ctx(
    component: str,
    Component: str,
    properties: list[dict],
    state_var_names: frozenset[str] = frozenset(),
    doc_blocks: dict | None = None,
    enums: dict[str, list[str]] | None = None,
    codecs: dict | None = None,
    *,
    csym: str,
) -> dict[str, str]:
    """Generate getset_def and tp_getset_decl context keys for Python properties.

    state_var_names: names already declared by make_state_ctx(); those are
    excluded from property_decls to avoid duplicate C declarations.

    Each property dict has: name, type (a _CTYPE_META key), writable (bool).

    enums: the project's ``[[enum]]`` registry (``C.enums(cfg)``). gh-519 — a
    property may declare ``enum = "<name>"``, which makes its Python face an
    ordered string instead of the raw int. ``None`` (the default) means the
    caller has no registry to offer, in which case ``enum`` is ignored and the
    output is byte-identical to the pre-gh-519 render.

    gh-2182: a writable property may declare ``error`` / ``error_message``.
    Its setter then tests the C setter's ``int`` return and raises on a
    non-zero one, the setter's prototype says ``int``, and both doc faces
    carry a ``Raises`` section. `validate_property_error` refuses the key
    wherever there is no C setter call to test. Without it the setter
    discards the return, as it always has.
    """
    _EMPTY: dict[str, str] = {
        "getset_def": "",
        "tp_getset_decl": "",
        "property_decls": "",
        "property_struct_fields": "",
        "property_stubs_pyi": "",
        "pyi_property_typing": "",
    }
    if not properties:
        return _EMPTY

    guard = (
        "    if (!self->handle) {\n"
        '        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
        "        return NULL;\n"
        "    }\n"
    )

    getter_parts: list[str] = []
    getset_entries: list[str] = []
    decl_lines: list[str] = []
    struct_field_lines: list[str] = []
    pyi_parts: list[str] = []
    # gh-519: enums actually referenced by this component's properties, in
    # first-reference order (mirrors _handle._enums_used) — only those get a
    # table emitted.
    enums_used: list[str] = []

    for p in properties:
        pname: str = p["name"]
        ctype: str = p.get("type") or p.get("ctype", "size_t")
        writable: bool = p.get("writable", False)
        field: bool = p.get("field", False)
        buf_field: str = p.get("buf_field", "")
        len_field: str = p.get("len_field", "n")
        valid_field: str = p.get("valid_field", "")
        # gh-543: a container property is backed by count/key/value accessors
        # rather than a scalar C value, so it never consults _CTYPE_META.
        container: bool = T.is_container_type(ctype)

        meta = _CTYPE_META.get(ctype, _CTYPE_META["size_t"])
        disp = ctype

        # gh-543: validate before anything is rendered, so an incoherent
        # declaration is a jm diagnostic rather than a compiler error in code
        # the user never wrote.
        if container:
            validate_container_property(component, p)
        # gh-2347: also refuses `py_type` on every other kind.
        validate_json_property(component, p)
        is_json: bool = T.is_json_type(ctype)
        if not is_json:
            validate_property_error(component, p, state_var_names, csym=csym)
        # gh-2182: what a refused assignment raises, or None; read once, by
        # the setter's binding, its prototype and both doc faces.
        setter_fn = CSYM.property_setter(csym, pname)
        setter_raise = property_declared_raise(p, setter_fn)

        # gh-519: an `enum`-decorated property stores the SSOT int in C but
        # presents the value as its string on the Python side.
        p_enum = _property_enum(component, Component, p, enums)
        if p_enum and p_enum not in enums_used:
            enums_used.append(p_enum)
        enum_index_fn, enum_table = (
            _enum_symbols(Component, p_enum) if p_enum else ("", "")
        )

        def _decode_stmts(
            acc: str,
            _t: str = enum_table,
            _e: str = p_enum,
            _p: str = pname,
        ) -> str:
            """Statements ending in a ``return`` that decode *acc*.

            gh-519: an enum is checked before it becomes a string -- C owns
            the stored value, often decoded from an external source, so an
            unknown one is reachable input. `_enumc.decode_c` is the one
            emitter for that, shared with every other face (gh-1450).
            """
            if not _t:
                return f"    return {meta['to_py'](acc)};\n"
            return _enumc.decode_c(_p, _e, acc, enums, prefix=Component) + "\n"

        if _borrow.publishes_capsule(p):
            # gh-788 gap 4: the PRODUCING side of gh-432. That issue taught
            # jm to *consume* a foreign pointer arriving as a named
            # `PyCapsule` (or duck-unwrapped from an object's `_capsule`);
            # nothing could hand one out. doppler's telemetry object is the
            # attach point for every instrumented component — it lends a
            # borrowed `dp_tlm_t *` so their `set_telemetry` can take it —
            # and with no way to declare that, the whole module stayed
            # `no_generate`.
            #
            # gh-286's `kind = "capsule"` is a different shape entirely:
            # free functions over an opaque capsule *as the state*, not a
            # property on a `PyTypeObject`.
            #
            # The capsule never frees the pointer, and that is the
            # load-bearing detail: it lends a pointer the object still owns,
            # and the owner frees it on `__dealloc__`. It holds the object
            # itself instead (gh-2328), so a consumer that keeps the capsule
            # keeps the pointer's owner.
            # gh-794: the body moved to `capsule_new_c` so a handle type can
            # publish the identical contract. One emitter, two callers -- a
            # second copy is a place for it to be changed on one side only.
            _cap_expr = p.get("expr") or "self->handle"
            getter = (
                f"static PyObject *\n"
                f"{Component}_getprop_{pname}"
                f"({Component}Object *self,"
                f" void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"{guard}"
                + _capsule_new_c(_cap_expr, p["capsule"], Component)
                + "\n}"
            )
        elif is_json:
            if pname in state_var_names:
                # gh-2347: the state field's own accessor is a scalar getter
                # of the same name, so the char * one would conflict with it.
                raise Refusal(
                    f"{component}.{pname}: a json property cannot share a "
                    f"state variable's name -- that variable already has a "
                    f"getter, and this one returns char *. Rename the "
                    f"property."
                )
            _jget = CSYM.property_getter(csym, pname)
            getter = _json_getter(Component, pname, _jget, guard)
            decl_lines.append(
                f"/**\n"
                f" * @brief Get {pname}, as JSON text.\n"
                f" * @param state  Must be non-NULL.\n"
                f" * @return A malloc'd, NUL-terminated UTF-8 JSON"
                f" document the caller\n"
                f" *         owns and must free(), or NULL on failure.\n"
                f" *         The Python property parses it with"
                f" json.loads and frees it.\n"
                f" */\n"
                f"{T.JSON_GETTER_CTYPE}{_jget}"
                f"(const {csym}_state_t *state);"
            )
        elif container:
            _cdc = None
            if _codec.is_codec_property(p):
                _cdc = (codecs or {}).get(p["codec"])
                if _cdc is None:
                    raise _codec.CodecError(
                        f"{component}.{pname}: property references codec "
                        f"'{p['codec']}', not declared in [codec.*]."
                    )
            getter, _c_decls = _container_getter(
                component,
                Component,
                p,
                guard,
                cdc=_cdc,
                csym=csym,
            )
            if pname not in state_var_names:
                decl_lines.extend(_c_decls)
        elif buf_field:
            _elem_ct = ctype[:-2] if ctype.endswith("[]") else ctype
            _elem_meta = _CTYPE_META.get(
                _elem_ct, _CTYPE_META["float _Complex"]
            )
            _np_enum = _NP_ENUM.get(_elem_meta["py_type"], "NPY_CFLOAT")
            _valid_check = (
                f"    if (!self->handle->{valid_field}) Py_RETURN_NONE;\n"
                if valid_field
                else ""
            )
            # gh-1312: one emitter for "borrow a pointer, pin self", shared
            # with an array state's get_<name>_view(). This site used to
            # ignore PyArray_SetBaseObject's return and Py_INCREF *after* it
            # -- a failing call does not steal the reference, so that leaked
            # one and handed back an array with no base at all.
            #
            # writeable=True preserves this property's behaviour, which
            # differs from the state view's. The asymmetry is deliberate here
            # and recorded in docs/memory-ownership.md rather than quietly
            # normalised: a consumer writing through a buf_field property
            # today would break.
            getter = (
                f"static PyObject *\n"
                f"{Component}_getprop_{pname}"
                f"({Component}Object *self,"
                f" void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"{guard}"
                f"{_valid_check}"
                + _borrow_view_c(
                    f"self->handle->{buf_field}",
                    f"self->handle->{len_field}",
                    _np_enum,
                    writeable=True,
                    who=f"{Component}.{pname}",
                )
                + "\n}"
            )
        elif p.get("expr"):
            # gh-602: expr is arbitrary author-supplied C, unlike the field/
            # call accessors below (a member access or function call, both
            # already safe under a cast with no parens needed). to_py()'s
            # cast binds tighter than anything lower-precedence, so a ternary
            # or comma expression must be parenthesized before the cast is
            # applied, or the cast silently lands on just the first operand.
            # The enum-decode path (_decode_stmts below, when p_enum is set)
            # already wraps its accessor in its own parens (`(long)(acc)`) —
            # skip adding a second layer there.
            _expr = p["expr"] if p_enum else f"({p['expr']})"
            getter = (
                f"static PyObject *\n"
                f"{Component}_getprop_{pname}"
                f"({Component}Object *self,"
                f" void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"{guard}"
                f"{_decode_stmts(_expr)}"
                f"}}"
            )
        elif field:
            # When a property aliases an existing state field (same name), do
            # not re-emit the struct member — make_state_ctx already declared
            # it.  Otherwise the struct ends up with duplicate fields and the
            # compiler errors out (gh-70).
            if pname not in state_var_names:
                struct_field_lines.append(f"    {disp} {pname};")
            getter = (
                f"static PyObject *\n"
                f"{Component}_getprop_{pname}"
                f"({Component}Object *self,"
                f" void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"{guard}"
                f"{_decode_stmts(f'self->handle->{pname}')}"
                f"}}"
            )
        else:
            _call = f"{CSYM.property_getter(csym, pname)}(self->handle)"
            # gh-519: the enum form binds the call into its own local before
            # the range check, so the C getter is evaluated exactly once.
            # gh-2231: no `<<IMPLEMENT>>` marker here. This body is finished
            # glue -- the call IS the implementation; what the author fills
            # in is the C getter's stub in `_core.c`, which carries its own.
            getter = (
                f"static PyObject *\n"
                f"{Component}_getprop_{pname}"
                f"({Component}Object *self,"
                f" void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"{guard}"
                f"{_decode_stmts(_call)}"
                f"}}"
            )
            if pname not in state_var_names:
                decl_lines.append(
                    f"/**\n"
                    f" * @brief Get {pname}.\n"
                    f" * @param state  Must be non-NULL.\n"
                    f" * @return Current {pname} value ({disp}).\n"
                    f" */\n"
                    f"{disp} {CSYM.property_getter(csym, pname)}"
                    f"(const {csym}_state_t *state);"
                )

        getter_parts.append(getter)

        setter_name = "NULL"
        if writable:
            setter_name = f"(setter){Component}_setprop_{pname}"
            # gh-1745: the predicate `make_enum_tables_ctx` emits the lookup
            # from, so a setter never calls one that was not emitted.
            if _property_lookup_enum(component, Component, p, enums):
                # gh-519: accept the Python string, resolve it through the
                # SSOT table, and assign the resolved int wherever the plain
                # setter would have assigned `v`. "s" already raises TypeError
                # for a non-str, so only the unknown-choice case needs a
                # hand-written error.
                # gh-1026: through the shared emitter. This face already
                # named the choices — it is the one gh-1021 copied FROM — so
                # the consolidation must not quietly move it onto a shorter
                # message. Its `Component` prefix is the object-scoped
                # namespace; see `_enumc.symbols`.

                parse_block = (
                    "    const char *v_str = NULL;\n"
                    '    if (!PyArg_Parse(value, "s", &v_str)) return -1;\n'
                    + _enumc.validate_c(
                        pname,
                        p_enum,
                        enums,
                        prefix=Component,
                        src="v_str",
                        result="v_idx",
                        fail="return -1;",
                    )
                    + "\n"
                    f"    {disp} v = ({disp})v_idx;\n"
                )
            else:
                # gh-2035: the one PyObject -> C scalar conversion, which a
                # composer's settings and fields call too.
                parse_block = _scalar_parse_c(
                    disp, "value", "v", "return -1;", label=pname, meta=meta
                )
            if field:
                assign_line = f"    self->handle->{pname} = v;\n"
            elif setter_raise is not None:
                # gh-2182: the C setter may refuse the value. Its return is
                # tested the way a `status_return` method's is, through the
                # one raise emitter -- `return -1;` because a setter slot
                # returns int, not a PyObject *.
                assign_line = (
                    f"    int _rc = {setter_fn}(self->handle, v);\n"
                    f"    if (_rc != 0) {{\n"
                    + _rc_raise_c(*setter_raise, fail="return -1;")
                    + "    }\n"
                )
            else:
                assign_line = f"    {setter_fn}(self->handle, v);\n"
            if not field and pname not in state_var_names:
                _set_ret = setter_return_type(p)
                _set_doc = (
                    f" * @return 0 to accept the value; non-zero refuses"
                    f" it,\n"
                    f" *         which Python raises as"
                    f" {setter_raise[0]}.\n"
                    if setter_raise is not None
                    else ""
                )
                decl_lines.append(
                    f"/**\n"
                    f" * @brief Set {pname}.\n"
                    f" * @param state  Must be non-NULL.\n"
                    f" * @param val    New value ({disp}).\n"
                    f"{_set_doc}"
                    f" */\n"
                    f"{_set_ret} {setter_fn}"
                    f"({csym}_state_t *state, {disp} val);"
                )
            setter = (
                f"static int\n"
                f"{Component}_setprop_{pname}"
                f"({Component}Object *self,"
                f" PyObject *value, void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return -1;\n"
                f"    }}\n"
                f"{parse_block}"
                f"{assign_line}"
                f"    return 0;\n"
                f"}}"
            )
            getter_parts.append(setter)

        # Property __doc__ precedence: TOML `doc` > getter @brief > the
        # field's own trailing `/**<` > name. PyGetSetDef's 4th field is the
        # doc.
        #
        # gh-671: the trailing member doc slots in *below* both authored
        # sources deliberately, so nothing already documented changes — it
        # only fills what was falling through to the name stub. For a
        # field-backed property that trailing comment is the most plausible
        # place the documentation already exists: doppler had ~518 documented
        # struct fields against 369 properties documented the redundant way,
        # the same sentence maintained twice and drifting independently.
        # gh-1394: the one chain, shared with `jm status --docs`, which
        # reports whichever properties it answers with a name stub.
        _pdoc = property_doc(component, p, doc_blocks, csym=csym)[0]
        # gh-2182: a setter that may refuse documents what it raises, on
        # both faces, through the renderer a method's Raises goes through --
        # so the runtime text is the stub's text without its indent. A
        # property declaring no `error` keeps its one-line summary.
        _raises = property_raises_doc(p, setter_fn)
        _rt_doc = (
            render_runtime_doc(
                None,
                pname,
                [],
                "None",
                _pdoc,
                authored_doc=p.get("doc", ""),
                raises=_raises,
            )
            if _raises
            else [_pdoc]
        )
        getset_entries.append(
            f'    {{ "{pname}", (getter){Component}_getprop_{pname},'
            f" {setter_name}, {_build_ml_doc(_rt_doc)}, NULL }},"
        )

        # gh-446: standalone .pyi stubs for a manifest property (PyGetSetDef
        # -> a real `@property` descriptor), independent of the getter/
        # setter *methods* make_state_ctx stubs out for state vars.
        if p_enum:
            # gh-519: the Python face is the ordered choice set, not an int.
            # Reuse _stubs._py's string_enum rendering so both stub writers
            # spell Literal identically (local import: _stubs imports
            # _context, so a module-level import would cycle).
            from .._stubs import _py as _stubs_py

            py_t = _stubs_py("string_enum:" + ",".join(enums[p_enum]))
        elif p.get("capsule"):
            # gh-788: a PyCapsule has no Python type to name — the whole
            # point is that its contents are opaque to Python and meaningful
            # only to the C that unwraps it. `Any` is what gh-432's
            # capsule-typed *params* already annotate to, so the producing
            # and consuming faces of the same pointer read alike.
            py_t = "Any"
        elif is_json:
            # gh-2347: the declared annotation, validated above to name only
            # builtins and Any (whose import the stub derives from its text).
            py_t = str(p.get("py_type") or "Any")
        elif container:
            _pcdc = (
                (codecs or {}).get(p["codec"])
                if _codec.is_codec_property(p)
                else None
            )
            py_t = _container_pyi(p, _pcdc)
        elif buf_field:
            py_t = _pyi_ndarray(ctype)
        else:
            py_t = _pyi_scalar(ctype)
        # gh-744: a property's docstring is a bare summary, so it never went
        # through `render_numpy_doc` and was emitted on one line whatever its
        # length. `summary_docstring` keeps the one-line shape when it fits,
        # so only the overlong ones change.
        pyi_block = [
            "",
            "    @property",
            f"    def {pname}(self) -> {py_t}:",
            # gh-1493: a manifest `doc` as written -- the runtime face beside
            # this already kept it whole, so the two disagreed. A header or
            # struct-field doc is still a summary for jm to wrap.
            *(
                render_numpy_doc(
                    None,
                    pname,
                    [],
                    "None",
                    _pdoc,
                    indent=8,
                    authored_doc=p.get("doc", ""),
                    raises=_raises,
                )
                if _raises
                else authored_docstring(authored_doc_lines(p["doc"]), 8)
                if p.get("doc")
                else summary_docstring(_pdoc, indent=8)
            ),
        ]
        if writable:
            # gh-1724: an array setter's value is an input array, spelled by
            # the one helper; the getter keeps the returned ndarray's type.
            set_t = T.array_param_annotation(ctype) if buf_field else py_t
            pyi_block += [
                f"    @{pname}.setter",
                f"    def {pname}(self, value: {set_t}) -> None: ...",
            ]
        pyi_parts.append("\n".join(pyi_block))

    getset_body = "\n".join(getter_parts)
    # gh-1021: the tables used to ride along here, inside getset_def. They
    # cannot any more. `getset_def` is the LAST of the three C slots
    # (`getter_setter_methods_c`, `extra_methods_c`, `getset_def`), and a
    # method parameter's enum is indexed from the middle one — so a table
    # emitted here is defined below its first use. They now live in the
    # `enum_tables` slot above all three, filled once by
    # :func:`make_enum_tables_ctx` from the union of both consumers.
    entries_str = "\n".join(getset_entries)
    getset_def = (
        f"{getset_body}\n\n"
        f"static PyGetSetDef {Component}_getset[] = {{\n"
        f"{entries_str}\n"
        f"    {{NULL, NULL, NULL, NULL, NULL}}\n"
        f"}};\n"
    )
    tp_getset_decl = f"\n    .tp_getset    = {Component}_getset,"
    property_decls = "\n".join(decl_lines) + "\n" if decl_lines else ""
    property_struct_fields = (
        "\n" + "\n".join(struct_field_lines) if struct_field_lines else ""
    )
    property_stubs_pyi = "\n".join(pyi_parts) + "\n" if pyi_parts else ""

    return {
        "getset_def": getset_def,
        "tp_getset_decl": tp_getset_decl,
        "property_decls": property_decls,
        "property_struct_fields": property_struct_fields,
        "property_stubs_pyi": property_stubs_pyi,
        # gh-519: the standalone .pyi hardcodes `from typing import Any`; an
        # enum property annotates as Literal[...] and needs it imported.
        "pyi_property_typing": ", Literal" if enums_used else "",
    }
