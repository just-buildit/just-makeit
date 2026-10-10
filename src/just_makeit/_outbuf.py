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

* an ``arg_type`` input, with or without params beside it — sized from the
  input's length, the ``n_in`` the kernel and ``<m>_max_out()`` are handed
  (params beside it since gh-2028);
* no params at all — the *generator* shape, sized from the synthesized count;
* params with an array among them — sized from the FIRST array param's length
  (:func:`sizing_param`), in the samples its ``elements_per_sample`` declares
  (:func:`interleave`), whether it is the only param or sits beside scalars
  (``Farrow.delay(x, mu)``) or other arrays (``Resampler.execute_ctrl(x,
  ctrl)``);
* all-scalar params — sized from ``<m>_max_out(state)``, the same expression
  the internal allocation uses.

and withheld otherwise. :func:`why_not` names which of those it is, so the
reason is available to a diagnostic instead of being implicit in a boolean.

The first array is not a new choice made here. It is the length the binding
already hands ``<m>_max_out()`` (gh-607) and already allocates from when
``max_out()`` answers 0 (gh-421), for every one of these shapes, with or
without ``out=``. The caller's buffer is validated against exactly what the
binding would have allocated itself -- ``_capacity_exprs``' invariant -- so an
``out=`` adds no bound the allocating path did not already trust.

What it is NOT
--------------
A ``multi_output`` method: two output arrays would need two buffers, and one
``out=`` cannot say which it is.

Params beside an ``arg_type`` input were excluded until gh-2028. Their parse
dropped them until gh-1960, so there was no call to thread an ``out=``
through; then the doc held it: ``out`` joined the Python arguments and broke
the positional match of the header's ``@param in`` onto ``x``. A binding
argument no longer takes an authored ``@param``'s positional slot
(`_docstring.render_numpy_method_doc`), and ``out=`` is the trailing
optional argument of the parse every params shape uses.

An array beside other params was excluded too, until gh-1998. gh-412 had
carved it out of the `out=` feature while making those methods
keyword-capable, and the sizing was never the obstacle -- doppler hand-wrote
``Farrow.delay(x, mu, out=)`` and ``Resampler.execute_ctrl(x, ctrl, out=)``
sized from ``x``, which is the rule above.

This section used to say the all-scalar shape got no `out=` either, and that
its bound was unusable because ``max_out()`` may legally answer ``0``. gh-1079
shipped that half and the text outlived it by one release. What the zero
actually needs is not a refusal but a *bounded* kernel — see :func:`why_not`'s
closing comment and gh-1091.

The length of a buffer the kernel is never told
-----------------------------------------------
A module function's ``out_type`` and a method's fixed ``out_type`` hand the
kernel a bare ``T *out`` and no capacity, so the length the binding
allocates is a contract the kernel writes to blind. :func:`length` is that
rule, once, for every such face (gh-1888); :func:`length_why_not` refuses a
declaration it cannot answer for, before anything is written.
"""

from __future__ import annotations

from ._types import _CTYPE_META, is_array_param_type, parse_out_type


def sizing_param(has_arg: bool, params: list[dict]) -> "dict | None":
    """The array param a variable-output method's output is sized from.

    With an ``arg_type`` input the block input sizes it, and that is not a
    param, so the answer is ``None``. Otherwise it is the FIRST array param:
    the length ``<m>_max_out()`` is given (gh-607) and the fallback capacity
    the binding allocates when ``max_out()`` answers 0. ``None`` when the
    params are all scalars, which is the gh-1079 shape sized from
    ``<m>_max_out(state)`` alone.

    Examples
    --------
    >>> sizing_param(False, [{"name": "mu", "type": "double"},
    ...                      {"name": "x", "type": "float[]"}])["name"]
    'x'
    >>> sizing_param(True, [{"name": "x", "type": "float[]"}]) is None
    True
    >>> sizing_param(False, [{"name": "n", "type": "size_t"}]) is None
    True
    """
    if has_arg:
        return None
    return next(
        (p for p in params if str(p.get("type", "")).endswith("[]")), None
    )


def interleave(has_arg: bool, params: list[dict], what: str = "") -> int:
    """Elements per sample of a variable-output method's counts (gh-1996).

    The ``elements_per_sample`` of :func:`sizing_param`, 1 when there is
    none. A variable-output kernel counts everything in one unit -- the
    input it is handed, the capacity ``max_out`` it is told, the ``n_out``
    it returns -- and that unit is the sizing array's sample. So this is the
    factor the binding converts every count by on its way between numpy
    elements and the kernel: the input divides by it, the capacity divides
    by it, and the output is allocated and returned as ``n_out`` times it.

    It was read for the input's ``<p>_len`` local only, which the
    variable-output binding never uses: the key was accepted and ignored,
    so the kernel was handed element counts and, under ``pass_capacity``,
    wrote that many SAMPLES into a buffer of that many elements.

    :func:`interleave_why_not` refuses a method whose output cannot be
    counted in that unit.

    Examples
    --------
    >>> interleave(False, [{"name": "x", "type": "int16_t[]",
    ...                     "elements_per_sample": 2}])
    2
    >>> interleave(False, [{"name": "n", "type": "size_t"}])
    1
    """
    from . import _coerce

    p = sizing_param(has_arg, params)
    return _coerce.elements_per_sample(p, what) if p else 1


def interleave_why_not(
    what: str,
    *,
    has_arg: bool,
    params: list[dict],
    out_elems: list[str],
) -> str:
    """Why this method's output cannot be counted in samples, or ``""``.

    gh-1996. :func:`interleave` reads the sizing array's
    ``elements_per_sample`` as the unit of every count the kernel sees, the
    output's included: the binding allocates and returns ``n_out * E``
    elements. That holds when the output carries the same element as the
    interleaved input -- ``int16_t`` I/Q in, ``int16_t`` I/Q out, the
    half-band decimator this was filed from. When the output element
    differs (``float _Complex`` or a record row out of ``int16_t`` I/Q in)
    the number of output elements per sample is a fact about the output
    that nothing declares, and a guess either way is a wrong-length result.
    So it is refused rather than guessed, before anything is written.

    Parameters
    ----------
    what : str
        Names the method, e.g. ``"method 'hb.execute'"``.
    has_arg, params
        As :func:`sizing_param` takes them.
    out_elems : list of str
        The element C type of every output array the binding allocates:
        :func:`element`'s answer, then each ``multi_output`` element.

    Returns
    -------
    str
        The refusal's text, or ``""`` when the output can be counted.

    Examples
    --------
    >>> x = {"name": "x", "type": "int16_t[]", "elements_per_sample": 2}
    >>> interleave_why_not("method 'h.run'", has_arg=False, params=[x],
    ...                    out_elems=["int16_t"])
    ''
    >>> print(interleave_why_not("method 'h.run'", has_arg=False,
    ...     params=[x], out_elems=["float _Complex"]).splitlines()[0])
    method 'h.run' declares elements_per_sample = 2 on 'x' (int16_t), but
    """
    p = sizing_param(has_arg, params)
    if p is None:
        return ""
    e = interleave(has_arg, params, what)
    if e == 1:
        return ""
    in_elem = str(p["type"])[:-2]
    other = [t for t in out_elems if t != in_elem]
    if not other:
        return ""
    return (
        f"{what} declares elements_per_sample = {e} on '{p['name']}' "
        f"({in_elem}), but\n"
        f"  its output element is '{other[0]}'. The kernel counts its "
        f"output in the same\n"
        f"  samples as '{p['name']}' (n_out, and max_out), so the binding "
        f"returns n_out * {e}\n"
        f"  elements -- which is only right when the output is "
        f"{in_elem} too.\n"
        f"  Return {in_elem} samples (`return_type` / `out_type`), or drop "
        f"`elements_per_sample`\n"
        f"  and count {in_elem} elements in the kernel."
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
    >>> why_not(variable_output=True, multi_output=False, has_arg=False,
    ...         params=[{"name": "x", "type": "float _Complex[]"},
    ...                 {"name": "mu", "type": "double"}])
    ''
    >>> why_not(variable_output=True, multi_output=False, has_arg=True,
    ...         params=[{"name": "mu", "type": "double"}])
    ''
    >>> why_not(variable_output=True, multi_output=True, has_arg=True,
    ...         params=[])
    'multi_output'
    """
    if not variable_output:
        return "not variable_output"
    if multi_output:
        # Two output arrays would need two buffers and a rule for pairing
        # them with the caller's; one `out=` cannot say which.
        return "multi_output"
    # Every remaining shape is offered `out=`.
    #
    # gh-2028: an `arg_type` input plus params. Its parse read the input
    # alone until gh-1960; it is `_build_params_parse` now, whose trailing
    # optional `out=` this is, and it is sized from the input exactly as
    # the same method without params is: `n_in`, which `<m>_max_out()` is
    # handed. What kept it out after gh-1960 was the doc -- `out` joining
    # the Python args moved the header's `@param in` off `x` -- and
    # `_docstring.render_numpy_method_doc` no longer lets a binding
    # argument take an authored `@param`'s positional slot.
    #
    # gh-1998: an array param, alone or beside others -- `Farrow.delay(x,
    # mu)`, `Resampler.execute_ctrl(x, ctrl)`. Sized from the FIRST array
    # (`sizing_param`, in its `interleave` -- gh-1996 counts every shape's
    # `out=` in samples), which is not a choice made here: it is the count
    # the binding already
    # hands `<m>_max_out()` (gh-607) and the fallback it already allocates
    # (gh-421) for these shapes, with or without `out=`. gh-412 carved them
    # out of `out=` only while making them keyword-capable; the sizing was
    # never in doubt, and doppler hand-wrote exactly this binding for both.
    #
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


# -- the length of a buffer the kernel is never told (gh-1888) ---------------


def is_count(param: dict) -> bool:
    """Can *param* hold an output's length: is it an integer scalar?

    gh-65's test, and the one reading of it: a method's fixed output falls
    back to the first such param, and a function's ``out_type = "T[n]"``
    must name one.

    Examples
    --------
    >>> is_count({"name": "n", "type": "size_t"})
    True
    >>> is_count({"name": "x", "type": "int32_t[]"})
    False
    >>> is_count({"name": "g", "type": "double"})
    False
    """
    t = str(param.get("type", ""))
    return (
        not is_array_param_type(t)
        and _CTYPE_META.get(t, {}).get("kind") == "int"
    )


def length(
    params: "list[dict]",
    *,
    out_type: str = "",
    out_size: str = "",
    count_fallback: bool = False,
) -> str:
    """The C length of an output the kernel fills blind, or ``""``.

    gh-1888. A module function's ``out_type`` -- fixed, variable-output,
    ``str`` -- and a method's fixed ``out_type`` all allocate their output
    per call and hand the kernel a bare ``T *out``, never its length. So
    the length is a contract: the binding allocates exactly it, and the
    kernel writes exactly it. Each face had its own copy of this choice,
    and each fell back to a constant -- one element for a function, none
    for a method -- that no kernel can know to stay inside.

    In order, the first that answers:

    1. ``out_size``, a C expression the row declares (a variable-output
       function's capacity, gh-335);
    2. the ``[n]`` of ``out_type = "T[n]"``, naming the integer param that
       holds it (gh-29; :func:`length_why_not` holds it to one);
    3. the first array param's ``<name>_len`` -- the count
       ``_coerce.array_count_c`` puts in that local, in samples under an
       ``elements_per_sample``;
    4. with *count_fallback*, the first integer scalar param -- a method's
       ``read(n)`` (gh-65). The function face does not guess: a count
       there is named with ``[n]``, or refused.

    Parameters
    ----------
    params : list of dict
        The call's arguments, ``{"name", "type"}`` each, in the order the
        binding parses them -- a method's array ``arg_type`` input
        included, as the ``x`` it is parsed as.
    out_type, out_size : str
        The row's keys, as declared; empty when absent.
    count_fallback : bool
        Read step 4.

    Returns
    -------
    str
        The C expression, or ``""`` when nothing the call carries gives
        the length.

    Examples
    --------
    >>> n = {"name": "n", "type": "size_t"}
    >>> x = {"name": "x", "type": "float[]"}
    >>> length([x, n], out_type="float")
    'x_len'
    >>> length([x, n], out_type="float[n]")
    'n'
    >>> length([n], out_type="float", out_size="2 * n + 1")
    '2 * n + 1'
    >>> length([n], out_type="float")
    ''
    >>> length([n], out_type="float", count_fallback=True)
    'n'
    """
    if out_size:
        return out_size
    named = parse_out_type(out_type)[1] if out_type else None
    if named:
        return named
    for p in params:
        if is_array_param_type(str(p.get("type", ""))):
            return f"{p['name']}_len"
    if count_fallback:
        for p in params:
            if is_count(p):
                return str(p["name"])
    return ""


def length_why_not(
    what: str,
    params: "list[dict]",
    *,
    out_type: str,
    out_size: str = "",
    variable_output: bool = False,
) -> str:
    """Why a module function's allocated output has no length, or ``""``.

    gh-1888. Asked by ``jm function`` before it writes anything -- which
    ``apply``'s replay and so ``status`` go through too -- and by the
    binding before it renders, so a row an older jm wrote meets the same
    words. Two ways a row fails :func:`length`:

    * its ``[n]`` names no integer param of the function. Naming nothing
      does not compile; naming a ``double`` truncates, and an array's
      pointer is no count at all;
    * nothing gives a length: no ``out_size``, no ``[n]``, no array param.
      jm allocated ONE element, and a kernel filling the ``n`` it was
      called with wrote past it.

    Parameters
    ----------
    what : str
        Names the function, e.g. ``"function 'ramp'"``.
    params : list of dict
        Its params, as :func:`length` takes them.
    out_type, out_size : str
        The row's keys; ``out_type`` is non-empty, or there is no output.
    variable_output : bool
        The output is a capacity the function reports a count within, so
        the advice names ``out_size``.

    Returns
    -------
    str
        The refusal, without the ``error:`` prefix the CLI adds, or ``""``.

    Examples
    --------
    >>> n = {"name": "n", "type": "int"}
    >>> length_why_not("function 'ramp'", [n], out_type="float[n]")
    ''
    >>> print(length_why_not("function 'ramp'", [n],
    ...                      out_type="float").splitlines()[0])
    function 'ramp' returns a fresh out_type 'float', but nothing says how
    >>> print(length_why_not("function 'ramp'", [n],
    ...                      out_type="float[m]").splitlines()[0])
    function 'ramp' sizes its output from 'm' (out_type 'float[m]'), but
    """
    named = parse_out_type(out_type)[1]
    if named:
        if any(p.get("name") == named and is_count(p) for p in params):
            return ""
        have = ", ".join(str(p["name"]) for p in params if is_count(p))
        return (
            f"{what} sizes its output from '{named}' (out_type "
            f"'{out_type}'), but\n"
            f"  '{named}' is not one of its integer parameters "
            f"({'those are: ' + have if have else 'it has none'}).\n"
            "  The length is read from that argument at the call, so it "
            "must name an\n"
            "  integer parameter the function takes."
        )
    if length(params, out_type=out_type, out_size=out_size):
        return ""
    elem = parse_out_type(out_type)[0]
    remedy = (
        "Give its capacity with --out-size EXPR (`out_size`), a C "
        "expression over\n"
        "  the arguments, or take an array parameter, whose length it "
        "then is."
        if variable_output
        else f"Name the integer parameter that holds it -- --out-type "
        f"'{elem}[n]'\n"
        f'  (`out_type = "{elem}[n]"`) -- or take an array parameter, '
        "whose length it\n"
        "  then is."
    )
    return (
        f"{what} returns a fresh out_type '{out_type}', but nothing says "
        "how\n"
        "  long it is. jm allocates it and the C function fills it through "
        "a bare\n"
        "  pointer, never told its length, so the length must come from "
        "the call:\n"
        "  this one has no array parameter and names no length.\n"
        f"  {remedy}"
    )
