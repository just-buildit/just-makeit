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
* exactly one array param — sized from that array's length.

and withheld otherwise. :func:`why_not` names which of those it is, so the
reason is available to a diagnostic instead of being implicit in a boolean.

What it is NOT
--------------
The all-scalar-params shape still gets no `out=`, and that is the open half of
gh-1079 rather than an oversight. Sizing it means reading
``<m>_max_out(state)``, which is legal for the author's C to answer ``0`` for —
"unknown", the sizing contract jm already documents. A buffer validated against
an unknown bound is not validated, so offering `out=` there is a decision about
what jm does when it cannot bound the write, not a change to this predicate.
Left to the issue deliberately, and named here so the next reader finds the
question rather than a gap.
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
    'all-scalar params: no length to size the buffer from (gh-1079)'
    """
    if not variable_output:
        return "not variable_output"
    if multi_output:
        # Two output arrays would need two buffers and a rule for pairing
        # them with the caller's; one `out=` cannot say which.
        return "multi_output"
    if not params or single_array_param(has_arg, params):
        return ""
    if has_arg:
        # An `arg_type` array plus extra params — `Farrow.delay(x, mu)`. There
        # IS a length to size from; what is missing is the parse block, which
        # is the same open half as below.
        return "extra params beside the array input (gh-1079)"
    return "all-scalar params: no length to size the buffer from (gh-1079)"


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
