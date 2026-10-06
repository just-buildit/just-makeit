"""_outbuf.py — does this method offer an `out=` buffer, and why not.

gh-1079. A `variable_output` method may accept an optional caller-owned `out=`
array — zero-alloc, safe to retain, parity with blockwise `steps(x, out=)`. The
predicate deciding that was spelled **three** times:

* `_context/_methods.make_methods_ctx` — the binding's `_kwlist` and parse
  block;
* the same file's `.pyi` builder — the standalone stub's signature and its
  `Parameters` list;
* `_stubs` — the module-aggregated `.pyi`.

Three copies of one question, and the two `.pyi` writers have already been
caught disagreeing about jm's own binding arguments twice (gh-1042 over whether
they are documented at all, gh-1051 over a default's value). `_context/_methods`
states the rule they must hold to, in its own words:

    a stub advertising an out= the binding rejects, or a binding accepting one
    the stub hides, is the same defect in either direction

That sentence had no mechanism behind it. This is the mechanism.

What the answer is
------------------
`out=` is offered to a single-output `variable_output` method whose output
length jm can size:

* no params at all — the *generator* shape, sized from the synthesized count;
* exactly one array param — sized from that array's length;
* all-scalar params — sized from ``<m>_max_out(state)``, the same expression
  the internal allocation uses.

and withheld otherwise. :func:`why_not` names which of those it is, so the
reason is available to a diagnostic instead of being implicit in a boolean.

What it is NOT
--------------
An array *beside* other params (``Farrow.delay(x, mu)``) stays excluded. There
is a length to size from, so this is not a sizing gap — gh-412 carved the shape
out deliberately and widening it is its own piece of work. :func:`why_not` says
so in those words rather than returning a bare False, because "jm will not"
and "jm cannot" are different answers and a bool makes them look alike.

This section used to say the all-scalar shape got no `out=` either, and that
its bound was unusable because ``max_out()`` may legally answer ``0``. gh-1079
shipped that half and the text outlived it by one release. What the zero
actually needs is not a refusal but a *bounded* kernel — see :func:`why_not`'s
closing comment and gh-1091.
"""

from __future__ import annotations


def single_array_param(has_arg: bool, params: list[dict]) -> bool:
    """Is this method's sole input one array param, declared as a param?

    gh-219 follow-up: a method's primary array input is sometimes declared as
    the only entry in ``params`` (``arg_type = "void"`` plus one array) rather
    than through ``arg_type``. That is functionally the same as ``has_arg``
    for sizing an output buffer; genuine *extra* params (``Farrow.delay(x,
    mu)``) are what stay ineligible.

    Examples
    --------
    >>> single_array_param(False, [{"name": "x", "type": "float[]"}])
    True
    >>> single_array_param(False, [{"name": "n", "type": "size_t"}])
    False
    >>> single_array_param(True, [{"name": "x", "type": "float[]"}])
    False
    """
    return (
        not has_arg
        and len(params) == 1
        and str(params[0].get("type", "")).endswith("[]")
    )


def why_not(
    *,
    variable_output: bool,
    multi_output: bool,
    has_arg: bool,
    params: list[dict],
) -> str:
    """``""`` when `out=` is offered, else the reason it is not.

    A string rather than a bool because the reasons are not interchangeable:
    "this method allocates per call" is a property of the shape, while "jm
    cannot size the buffer" is a gap with an issue against it. A predicate
    that returned False for both would make them look like one thing.

    Examples
    --------
    >>> why_not(variable_output=False, multi_output=False,
    ...         has_arg=True, params=[])
    'not variable_output'
    >>> why_not(variable_output=True, multi_output=False,
    ...         has_arg=True, params=[])
    ''
    >>> why_not(variable_output=True, multi_output=False, has_arg=False,
    ...         params=[{"name": "n", "type": "size_t"}])
    ''
    >>> why_not(variable_output=True, multi_output=False, has_arg=True,
    ...         params=[{"name": "mu", "type": "double"}])
    'extra params beside the array input (gh-1079)'
    """
    if not variable_output:
        return "not variable_output"
    if multi_output:
        # Two output arrays would need two buffers and a rule for pairing
        # them with the caller's; one `out=` cannot say which.
        return "multi_output"
    if not params or single_array_param(has_arg, params):
        return ""
    if any(str(p.get("type", "")).endswith("[]") for p in params):
        # An array among several params — `Farrow.delay(x, mu)`, whether the
        # array arrives through `arg_type` or as a param. There IS a length to
        # size from, and gh-412 carved the shape out of the `out=` feature
        # deliberately; widening it is a separate piece of work from gh-1079,
        # which asks about the ALL-SCALAR shape.
        return "an array beside other params (gh-412)"
    if has_arg:
        # An `arg_type` array plus extra params — `Farrow.delay(x, mu)`. The
        # `has_arg` branch builds its own kwlist around the array input and
        # does not thread extra params through it, so this one is still a
        # parse-block gap rather than a sizing one. Named separately for that
        # reason: it is a different piece of work from the shape below.
        return "extra params beside the array input (gh-1079)"
    # gh-1079: the all-scalar shape. Sized from `<m>_max_out(state)`, which is
    # the same expression the internal allocation uses for this shape — so the
    # caller's buffer is validated against exactly what the binding would have
    # allocated itself, which is `_capacity_exprs`' stated invariant.
    #
    # Safe because the buffer is always validated against a capacity the
    # kernel is then held to. `max_out()` returning 0 is documented as
    # "unknown", and every other shape falls back to the call's own length;
    # this one has none, so an unknown bound used to mean a zero-length
    # allocation and a heap overflow.
    #
    # Two different mechanisms close that, and gh-1091 is why they are named
    # separately rather than as one:
    #
    # * without `pass_capacity` the kernel writes blind, so gh-1085 refuses a
    #   zero bound outright and `max_out()` is non-zero wherever the method
    #   runs at all — "at least max_out(state)" is then worth checking;
    # * with `pass_capacity` the kernel is handed the capacity and must clamp,
    #   so a zero needs no refusing: it means "write nothing". The `out=` path
    #   passes the caller's own `PyArray_SIZE`, so the bound the kernel is
    #   told is true regardless of what `max_out()` said.
    #
    # Reading those as one rule is what made a non-blocking drain raise on the
    # empty case it is designed for.
    return ""


def enabled(
    *,
    variable_output: bool,
    multi_output: bool,
    has_arg: bool,
    params: list[dict],
) -> bool:
    """Does this method's binding accept — and its stubs publish — ``out=``?

    THE predicate. Both `.pyi` generators and the binding call it, so the
    three faces cannot come to disagree about which methods have the argument.
    """
    return not why_not(
        variable_output=variable_output,
        multi_output=multi_output,
        has_arg=has_arg,
        params=params,
    )


def element(
    *,
    variable_output: bool,
    record_dtype: str,
    borrow: object,
    out_type: str,
    return_type: str,
) -> str:
    """The element C type of a variable-output method's output buffer.

    A ``record_dtype`` names it outright -- the struct the kernel writes rows
    of (gh-788) -- and wins over ``out_type``, which wins over
    ``return_type``. A ``T[]`` spelling is reduced to ``T`` (gh-201): the
    buffer holds elements. ONE answer for the binding's ``*out`` parameter,
    its ``sizeof`` and data-pointer cast, and the ``out=`` annotation of both
    ``.pyi`` generators (gh-1724), so they cannot describe different buffers.

    >>> element(variable_output=True, record_dtype="", borrow=None,
    ...         out_type="float[]", return_type="size_t")
    'float'
    >>> element(variable_output=True, record_dtype="rec_t", borrow=None,
    ...         out_type="", return_type="size_t")
    'rec_t'
    """
    from . import _record

    src = (
        record_dtype
        if _record.is_record_array(variable_output, record_dtype, borrow)
        else (out_type if (variable_output and out_type) else return_type)
    )
    return src[:-2] if src.endswith("[]") else src


#: The closing advice of :func:`element_why_not`, per array-result shape:
#: what a ``jm method`` command line -- and, in backticks, a manifest row --
#: changes to name the element. ``jm object`` passes its own.
NAME_THE_ELEMENT = {
    "variable_output": (
        "Name the element with --return-type <T> (`return_type`),\n"
        "  --out-type <T> (`out_type`) or --record-dtype <struct> "
        "(`record_dtype`),\n"
        "  or drop --variable-output (`variable_output`)."
    ),
    "batch": (
        "Name the element with --return-type <T> (`return_type`), or drop "
        "--batch\n"
        "  (`batch`)."
    ),
}


def element_why_not(
    what: str,
    *,
    variable_output: bool,
    batch: bool = False,
    record_dtype: str,
    borrow: object,
    out_type: str,
    return_type: str,
    remedy: str = "",
) -> str:
    """Why an array-result method has no output element, or ``""``.

    gh-1885. A ``variable_output`` method returns an ARRAY of its element
    type -- :func:`element` names it -- and the binding allocates that array
    from the element's numpy dtype and its ``sizeof``. ``void`` has neither,
    so the render indexed ``_CTYPE_META["void"]`` and died with a bare
    ``KeyError``. Two families reached it: a ``void`` return type with no
    ``out_type`` / ``record_dtype`` (``jm method --return-type void
    --variable-output``, a ``consumer`` preset's ``--variable-output``), and
    ``jm object --variable-output`` on a ``void`` ``--arg-type``, whose
    method takes its element from the arg type when no return type is given.
    A ``batch`` method is the same array one shape over -- one element per
    input, typed by ``return_type`` alone -- and crashed the same way. Every
    one crashed AFTER the row was persisted, so each later ``status`` /
    ``apply`` crashed on the manifest jm had just written.

    ONE answer for every face that can meet such a row: ``jm method`` and
    ``jm object`` ask before they write anything, and the binding asks
    before it renders -- which is where a manifest an older jm wrote meets
    it, under ``apply`` and ``status`` (both before writing the tree) and
    every command that re-renders the object. ``regenerate`` refuses too,
    but only after deleting the component it is about to rebuild: gh-1867.

    ``batch`` is asked first because the binding renders it first: a batch
    method that is also ``variable_output`` is generated as a batch, from
    ``return_type``, whatever :func:`element` would have answered.

    Parameters
    ----------
    what : str
        Names the method in the message, e.g. ``"method 'o.run'"``.
    variable_output, record_dtype, borrow, out_type, return_type
        As :func:`element` takes them.
    batch : bool
        The method is a 1:1-rate array method (``--batch``).
    remedy : str
        The closing advice, when the face asking has different flags from
        ``jm method`` (``jm object`` has no ``--out-type``). Empty takes
        the shape's :data:`NAME_THE_ELEMENT`.

    Returns
    -------
    str
        The refusal's text, without the ``error:`` prefix the CLI adds, or
        ``""`` when the method has an element or returns no array.

    Examples
    --------
    >>> element_why_not("method 'o.run'", variable_output=True,
    ...     record_dtype="", borrow=None, out_type="", return_type="float")
    ''
    >>> element_why_not("method 'o.run'", variable_output=False,
    ...     record_dtype="", borrow=None, out_type="", return_type="void")
    ''
    >>> element_why_not("method 'o.run'", variable_output=True,
    ...     record_dtype="rec_t", borrow=None, out_type="",
    ...     return_type="void")
    ''
    >>> print(element_why_not("method 'o.run'", variable_output=True,
    ...     record_dtype="", borrow=None, out_type="",
    ...     return_type="void").splitlines()[0])
    method 'o.run' is variable_output, but its output element is 'void'.
    >>> print(element_why_not("method 'o.run'", variable_output=False,
    ...     batch=True, record_dtype="", borrow=None, out_type="",
    ...     return_type="void").splitlines()[0])
    method 'o.run' is batch, but its output element is 'void'.
    """
    if batch:
        shape, noun = "batch", "batch"
        elem = return_type[:-2] if return_type.endswith("[]") else return_type
    elif variable_output:
        shape, noun = "variable_output", "variable-output"
        elem = element(
            variable_output=variable_output,
            record_dtype=record_dtype,
            borrow=borrow,
            out_type=out_type,
            return_type=return_type,
        )
    else:
        return ""
    if elem.strip() != "void":
        return ""
    return (
        f"{what} is {shape}, but its output element is 'void'.\n"
        f"  A {noun} method returns an array of its element type, and void "
        "has\n"
        "  none: there is no dtype to allocate and no sizeof to fill.\n"
        f"  {remedy or NAME_THE_ELEMENT[shape]}"
    )
