"""_gluedoc.py — docstrings for the methods jm generates for its own machinery.

``state_bytes`` / ``get_state`` / ``set_state``, ``destroy``, ``__enter__`` and
``__exit__`` are 100% jm-owned glue. A downstream project cannot document them
by writing C Doxygen the way it documents ``step()`` or a custom method — there
is no hand-written declaration to attach a comment to. The text therefore has to
come from jm, and this module is where it lives (gh-647).

One definition, both faces. Each method is described once as a
:class:`_docstring.DoxyBlock` and rendered twice: through
:func:`_docstring.render_numpy_doc` for the ``.pyi``, and through
:func:`_docstring.render_runtime_doc` for the runtime ``PyMethodDef`` (which
``_context._parse._build_ml_doc`` then escapes into a C literal). Since
gh-642 those two share one section builder, so the faces differ only in
indent, delimiters and the ``Examples`` block. That is deliberate — the
previous literals had already drifted:

- ``get_state`` said "Serialize the **engine's** mutable state", naming a
  component from some long-gone example rather than the object it documents;
- ``destroy`` said "Release C resources immediately." in the ``.pyi`` and
  "Release resources." at runtime;
- ``__enter__`` / ``__exit__`` had no docstring on either face (``NULL`` in the
  method table), so ``help()`` showed nothing at all.

**The prose describes what the generated C actually does**, checked against it
rather than assumed: every accessor raises ``RuntimeError`` once ``destroy()``
has run, ``set_state`` validates type *and* length before handing the blob to
the C API, ``destroy`` is idempotent, and ``__exit__`` returns ``None`` so it
never suppresses an exception.

No ``Examples`` section is emitted, on either face. The generated ``.pyi``
docstrings are harvested and executed by the doctest gate, so an example here
would have to construct a real object of an arbitrary component — and a
placeholder would fail the gate outright. (This is a property of *these*
blocks carrying no ``@code``, not a rule of the runtime face: gh-642 renders
``Examples`` at runtime wherever a header supplies one.)
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ._docstring import DoxyBlock, render_numpy_doc, render_runtime_doc


@dataclass
class GlueMethod:
    """One generated glue method: its Python signature and its documentation.

    Attributes
    ----------
    name : str
        Python-visible method name.
    py_params : list of tuple(str, str)
        ``(name, annotation)`` for each parameter after ``self``, in order.
        Drives the rendered ``Parameters`` section's types.
    ret_ann : str
        Python return annotation, for the ``Returns`` section.
    block : DoxyBlock
        The prose: brief, extended description, per-parameter and return text.
    """

    name: str
    py_params: list[tuple[str, str]] = field(default_factory=list)
    ret_ann: str = "None"
    block: DoxyBlock = field(default_factory=DoxyBlock)
    #: True when ``block.body`` holds one PARAGRAPH per entry (jm's own
    #: definitions below), False when it holds one LINE per entry (a parsed
    #: header block). Decides whether :meth:`_spaced` separates them.
    body_is_paragraphs: bool = True

    def with_header_block(self, block: DoxyBlock) -> "GlueMethod":
        """This method documented by the PROJECT's header instead of jm's.

        gh-1052. A header block's ``body`` holds *lines* — that is what the
        parser produces — where jm's own definitions below hold one paragraph
        per entry. :meth:`_spaced` blank-separates entries, which is right for
        the second and catastrophic for the first: every source line became
        its own paragraph, so the longer and better-written the authored block,
        the worse it read. Live in doppler on ``conv_enc_encode_max_out``,
        ``viterbi_decode_max_out`` and several ``execute_max_out`` entries.

        The substitution is a method rather than a bare ``replace(gm,
        block=...)`` so the provenance cannot be set halfway: a caller that
        swapped the block and forgot the flag would reintroduce this exactly.

        Examples
        --------
        >>> gm = GlueMethod("f", block=DoxyBlock(brief="B.", body=["one"]))
        >>> gm.with_header_block(DoxyBlock(brief="B.", body=["a", "b"]))._spaced().body
        ['a', 'b']
        """
        return replace(self, block=block, body_is_paragraphs=False)

    def _spaced(self) -> DoxyBlock:
        """This method's block with its paragraphs blank-line separated.

        ``DoxyBlock.body`` holds *lines*, not paragraphs -- that is what the
        parser produces, and ``group_paragraphs`` joins each run of consecutive
        non-blank lines. Authoring one paragraph per list entry here would
        therefore render them merged into a single blob. The blank entries are
        inserted at render time so the definitions above stay readable as
        paragraphs.

        gh-1052: only for jm's OWN prose. A body that came from the project's
        header is already lines, and blank-separating those turns one
        paragraph into one paragraph per line — see :meth:`with_header_block`.
        """
        if not self.body_is_paragraphs:
            return self.block
        body: list[str] = []
        for para in self.block.body:
            if body:
                body.append("")
            body.append(para)
        return replace(self.block, body=body)

    def pyi_doc(
        self,
        indent: int = 8,
        raises: "list[tuple[str, str]] | None" = None,
    ) -> list[str]:
        """Rendered numpy docstring lines for the ``.pyi`` face.

        *raises* is the manifest-declared exception (gh-869), forwarded
        unchanged to :meth:`c_doc_lines`' renderer as well — a glue method
        that raises must say so on both faces or a face-parity gate reports
        parity over a binding that raises.
        """
        return render_numpy_doc(
            self._spaced(),
            self.name,
            self.py_params,
            self.ret_ann,
            indent=indent,
            raises=raises,
        )

    def pyi_params(self, defaults: bool = False) -> str:
        """The stub signature's parameter list, ``self`` included.

        Built from :attr:`py_params` — the same list that drives the rendered
        ``Parameters`` section — so a documented parameter cannot go missing
        from the signature. griffe reports that mismatch as "documented
        parameter not in the signature", and ``__exit__`` had it: a
        ``*args: object`` signature over three documented names.

        Parameters
        ----------
        defaults : bool, optional
            Append ``= ...`` to each parameter. Wanted for ``__exit__``, whose
            C binding is ``METH_VARARGS`` and so tolerates any arity.

        Examples
        --------
        >>> glue_methods("Fir")["set_state"].pyi_params()
        'self, blob: bytes'
        >>> glue_methods("Fir")["state_bytes"].pyi_params()
        'self'
        """
        tail = " = ..." if defaults else ""
        return ", ".join(
            ["self"] + [f"{n}: {a}{tail}" for n, a in self.py_params]
        )

    def c_doc_lines(
        self, raises: "list[tuple[str, str]] | None" = None
    ) -> list[str]:
        """Logical doc lines for the runtime ``PyMethodDef`` entry.

        The same prose as :meth:`pyi_doc` with the stub-only parts removed —
        no indent, no ``\"\"\"`` delimiters. It used to be a second
        hand-written copy of that layout, and had already drifted from it in
        two ways a reader would notice: a parameter was emitted as a bare
        ``blob`` rather than ``blob : bytes`` (numpydoc does not read the
        former as a parameter at all), and ``Returns`` carried a description
        with no type line above it. Routing through the shared renderer
        (gh-642) is what stops a third difference appearing.

        Emitted as wrapped lines rather than one long string because each
        entry becomes a C string literal in generated source that a human
        reads and clang-format will not reflow.
        """
        return render_runtime_doc(
            self._spaced(),
            self.name,
            self.py_params,
            self.ret_ann,
            raises=raises,
        )


def _serialization(Component: str) -> list[GlueMethod]:
    """The ``--serializable`` triplet, parametrised by the wrapped type."""
    destroyed = (
        f"Raises ``RuntimeError`` if the {Component} has already been "
        f"destroyed."
    )
    return [
        GlueMethod(
            name="state_bytes",
            ret_ann="int",
            block=DoxyBlock(
                brief="Size in bytes of this object's serialized state.",
                body=[
                    "The exact length `get_state` returns and `set_state` "
                    "requires. It depends on how the object was constructed "
                    "(state arrays are sized at construction), so read it "
                    "from the instance rather than assuming a constant.",
                    destroyed,
                ],
                returns="Byte length of one serialized state blob.",
            ),
        ),
        GlueMethod(
            name="get_state",
            ret_ann="bytes",
            block=DoxyBlock(
                brief="Serialize this object's mutable state to bytes.",
                body=[
                    "Captures exactly the state that evolves as the object "
                    "runs, so a blob taken now and restored later resumes "
                    "from this point. Construction parameters are not "
                    "included: restore into an object built the same way.",
                    "The blob is opaque and always `state_bytes()` long. Its "
                    "layout is an implementation detail of the C core and is "
                    "not a stable format across builds.",
                    destroyed,
                ],
                returns="Opaque snapshot, `state_bytes()` bytes long.",
            ),
        ),
        GlueMethod(
            name="set_state",
            py_params=[("blob", "bytes")],
            block=DoxyBlock(
                brief="Restore mutable state from a `get_state()` blob.",
                body=[
                    "Overwrites the live state in place; the object keeps the "
                    "parameters it was constructed with. Length is validated "
                    "against `state_bytes()` before the blob is handed to the "
                    "C core, and the core may reject it as well.",
                    "Raises ``TypeError`` if *blob* is not bytes, "
                    "``ValueError`` if its length differs from "
                    f"`state_bytes()` or the core rejects it, and "
                    f"``RuntimeError`` if the {Component} has already been "
                    "destroyed.",
                ],
                params=[
                    (
                        "blob",
                        "A `get_state()` blob from this type, exactly "
                        "`state_bytes()` long.",
                    )
                ],
            ),
        ),
    ]


def _lifecycle(
    Component: str, close_name: str = "destroy", finalizes: bool = False
) -> list[GlueMethod]:
    """``destroy``/``close`` plus the context-manager protocol.

    ``finalizes`` (gh-805 §H) switches the context-manager prose from
    *releases* to *finalizes*. It is a separate argument from ``close_name``
    because the two answer different questions — *what is called* and *what
    survives* — and a name cannot carry the second. Naming the finalizer while
    still promising the object is released would leave both faces agreeing on
    the same wrong sentence, which is exactly the failure the key removes and
    the one a doc-parity gate cannot see.
    """
    _cm_effect = (
        "finalized deterministically on exit rather than at collection time"
        if finalizes
        else "released deterministically on exit rather than at collection "
        "time"
    )
    _exit_brief = (
        f"Exit a context manager, finalizing the {Component}."
        if finalizes
        else f"Exit a context manager, releasing the {Component}."
    )
    _exit_body = (
        [
            f"Equivalent to calling `{close_name}()`. The {Component} is "
            f"**not** released here: it stays usable, which is what makes "
            f"results gathered during the `with` body readable after it. "
            f"The memory is freed when the object is collected.",
            "Returns ``None``, so an exception raised inside the `with` "
            "body propagates normally; this never suppresses one.",
        ]
        if finalizes
        else [
            f"Equivalent to calling `{close_name}()`. Returns "
            "``None``, so an exception raised inside the `with` body "
            "propagates normally; this never suppresses one.",
        ]
    )
    return [
        GlueMethod(
            name=close_name,
            block=DoxyBlock(
                brief="Release the underlying C resources immediately.",
                body=[
                    "Ordinarily unnecessary: the resources are freed when "
                    "the object is garbage-collected. Call this to release "
                    "them at a definite point instead, or use the object as "
                    "a context manager, which calls it on exit.",
                    "Idempotent: calling it again on an already-released "
                    "object does nothing. Every other method raises "
                    "``RuntimeError`` once it has run.",
                ],
            ),
        ),
        GlueMethod(
            name="__enter__",
            # Unquoted: the forward-reference quoting belongs to the emitted
            # signature, not to the numpy Returns type column.
            ret_ann=Component,
            block=DoxyBlock(
                brief="Enter a context manager, returning this object.",
                body=[
                    f"Lets a {Component} be used in a `with` statement so its "
                    f"C resources are {_cm_effect}.",
                ],
                returns="This same object, not a copy.",
            ),
        ),
        GlueMethod(
            name="__exit__",
            py_params=[
                ("exc_type", "object | None"),
                ("exc", "object | None"),
                ("tb", "object | None"),
            ],
            block=DoxyBlock(
                brief=_exit_brief,
                body=_exit_body,
                params=[
                    ("exc_type", "Exception class, or None. Ignored."),
                    ("exc", "Exception instance, or None. Ignored."),
                    ("tb", "Traceback object, or None. Ignored."),
                ],
            ),
        ),
    ]


def glue_method_names() -> frozenset[str]:
    """Every Python name jm generates glue documentation for (gh-707).

    Used by ``_docsync`` to recognise a doc slot that jm owns outright. Both
    spellings of the explicit-release method are included: an object is either
    ``destroy``-shaped or ``close``-shaped, and the transplant sees only a name
    in a fragment, not which shape produced it.

    The membership is what licenses the more permissive reclaim rule there —
    these methods have **no authoring path**. A downstream cannot document
    ``state_bytes`` with Doxygen, because there is no declaration to attach a
    comment to; that is why this module exists (gh-647).
    """
    return frozenset(
        {
            "state_bytes",
            "get_state",
            "set_state",
            "destroy",
            "close",
            "__enter__",
            "__exit__",
        }
    )


def glue_methods(
    Component: str, *, close_name: str = "destroy", finalizes: bool = False
) -> dict[str, GlueMethod]:
    """Every generated glue method for *Component*, keyed by Python name.

    Parameters
    ----------
    Component : str
        The wrapped type's Python class name, used in the prose.
    close_name : str, optional
        Name of the explicit-release method (``close`` for reader-shaped
        objects, ``destroy`` otherwise).
    finalizes : bool, optional
        gh-805 §H. ``True`` when ``__exit__`` calls a finalizer that leaves
        the object alive, so the context-manager prose promises finalization
        rather than release. Affects ``__enter__``/``__exit__`` only.

    Returns
    -------
    dict
        ``{method_name: GlueMethod}``. Callers take the entries their object
        actually generates — the serialization triplet only exists under
        ``--serializable``.

    Examples
    --------
    >>> gm = glue_methods("Fir")["set_state"]
    >>> gm.block.brief
    'Restore mutable state from a `get_state()` blob.'
    >>> gm.py_params
    [('blob', 'bytes')]
    """
    out: dict[str, GlueMethod] = {}
    for gm in _serialization(Component) + _lifecycle(
        Component, close_name, finalizes
    ):
        out[gm.name] = gm
    return out


def max_out_method(
    name: str, count_param: str = "", max_out_const: int = 0
) -> GlueMethod:
    """jm's fallback documentation for a ``<name>_max_out`` accessor (gh-684).

    A **fallback**, not the answer. Unlike the glue above -- where jm owns the
    semantics outright and ``state_bytes()`` means the identical thing on every
    object -- ``max_out`` is uniform in *shape* and object-specific in *value*,
    and unless the manifest declared the constant its C body is an ``IMPLEMENT``
    stub the author writes. ``n`` for a FIR, ``ceil(n/R)`` for a decimator,
    ``n*L + taps - 1`` for an interpolator: that relationship is the most useful
    thing this docstring can carry and jm cannot know it. So a header block on
    the declaration always wins; this is what gets used when there is none.

    When ``max_out_const`` **is** declared, jm does know the answer and says it
    rather than a generic sentence.

    Parameters
    ----------
    name : str
        The owning method's name, e.g. ``execute``.
    count_param : str, optional
        The input-count parameter, when this shape takes one.
    max_out_const : int, optional
        The manifest's declared ``max_out``, when there is one.

    Examples
    --------
    >>> max_out_method("execute", "n_in").block.brief
    'Largest number of samples execute() can return for n_in inputs.'
    >>> max_out_method("execute", "n_in", 4).block.returns
    'Always 4 -- the declared worst case.'
    """
    if count_param:
        brief = (
            f"Largest number of samples {name}() can return for "
            f"{count_param} inputs."
        )
        params = [
            (count_param, f"Number of input samples {name}() will be given.")
        ]
    else:
        brief = (
            f"Largest number of samples {name}() can return in the "
            f"current state."
        )
        params = []
    if max_out_const:
        returns = f"Always {max_out_const} -- the declared worst case."
    else:
        returns = (
            "Upper bound on the output length; the actual call may return "
            "fewer."
        )
    return GlueMethod(
        name=f"{name}_max_out",
        py_params=[(count_param, "int")] if count_param else [],
        ret_ann="int",
        block=DoxyBlock(
            brief=brief,
            body=[
                f"Size an `out=` buffer with this before calling {name}(), "
                f"or use it to allocate one up front. The bound is this "
                f"object's own: "
                f"what it depends on is a property of the algorithm, so a "
                f"header block on {name}_max_out() replaces this text."
            ],
            params=params,
            returns=returns,
        ),
    )


# ── binding parameters (gh-1042) ────────────────────────────────────────────
#
# `count` and `out=` are jm's, not the algorithm's: the C kernel never sees
# either. They were therefore excluded from `Parameters` outright — the rule
# `_stub_params` still states, that the section "documents what the algorithm
# takes" — while the signature listed them anyway. Two consequences, and the
# second is the worse one:
#
# * a `variable_output` method with `arg_type = "void"` (the generator shape)
#   has NO parameter but these two, so it rendered a two-parameter signature
#   above no `Parameters` section at all;
# * an author who wrote `@param count` on the C declaration had it **silently
#   discarded**, because the header's params are filtered through the
#   Python-facing list before being read. So there was no authoring move that
#   could fix the first point either.
#
# Documented on every shape rather than only where the section would otherwise
# be empty: the same parameter appearing or vanishing depending on whether a
# sibling exists is the caveat-shaped rule, and "every parameter in the
# signature has an entry" is the one with no exceptions.
#
# Unlike the glue *methods* above, these CAN be documented downstream — there
# is a declaration to attach `@param count` to. So this text is a default the
# header outranks, not jm's last word; `_docstring` consults it only where the
# header said nothing.

#: Default description for the synthesized `count` argument.
COUNT_PARAM_DOC = (
    "How many output samples to ask for. The call may return fewer; "
    "size an `out=` buffer with the matching `_max_out()` when you need "
    "the worst case."
)

#: Default description for the optional `out=` buffer.
OUT_PARAM_DOC = (
    "Optional pre-allocated output buffer. When given, the result is "
    "written into it and the returned array is a view of exactly the "
    "samples produced; when omitted, a fresh array is allocated."
)


#: What the synthesized leading count argument is called when the manifest
#: does not say. Kept as the default rather than derived from the C parameter
#: name: `count` is the published Python name of every generator jm has ever
#: emitted, and moving it would rename a working keyword argument in every
#: existing project for no functional gain. gh-1074 asks for the knob, and
#: names this as the default it wants.
COUNT_KWARG_DEFAULT = "count"


def count_kwarg_name(count_name: str = "") -> str:
    """What this method's synthesized leading count argument is called.

    gh-1074. The name was hard-coded ``"count"`` in **seven** places — two
    `_kwlist` arrays, the runtime doc's parameter list, the doc's worked
    call example, both `.pyi` generators, and the `param_defaults` map above
    — which is the shape this repo keeps finding a drifted copy of. One
    accessor, so "what is the count argument called" cannot come to mean two
    things in one project.

    Why the knob exists. A ``variable_output`` method with ``arg_type =
    "void"`` and no params gets a synthesized leading count kwarg, and
    ``count_default`` (gh-1051) already made its *value* settable. Its
    **name** was not, so a project whose C API calls that quantity something
    else could not say so — while jm's own `_max_out_count_param` (gh-607)
    derives the sibling ``<m>_max_out(self, n)`` **from the C signature**,
    "rather than inventing a fourth name for the same concept". The method's
    own kwarg then got a different name from the sibling jm had just aligned:
    ``ptr(count=...)`` beside ``ptr_max_out(n=...)``, for the same number.

    The declared-param workaround is not one. Declaring ``params = [{name =
    "n", type = "size_t"}]`` gives both faces the name and leaves the C
    prototype byte-identical — and silently drops the ``out=`` buffer and the
    default, because that shape is no longer the generator shape. Measured on
    0.63.3: ``def ptr2(self, n: int) -> NDArray[...]``.

    Parameters
    ----------
    count_name : str
        The manifest's ``count_name``. Empty (the usual case) means
        `COUNT_KWARG_DEFAULT`.

    Returns
    -------
    str
        The Python keyword the binding parses and both stubs publish.

    Examples
    --------
    >>> count_kwarg_name()
    'count'
    >>> count_kwarg_name("")
    'count'
    >>> count_kwarg_name("  n  ")
    'n'
    """
    return (count_name or "").strip() or COUNT_KWARG_DEFAULT


def binding_param_docs(count_name: str = "") -> dict[str, str]:
    """jm's default description for each synthesized binding argument.

    Keyed by the Python name, for :func:`_docstring.render_numpy_doc`'s
    ``param_defaults``. A header ``@param`` of the same name outranks these.

    gh-1074: the count key follows `count_kwarg_name`, because this map is
    looked up **by the name in the signature**. Left hard-coded, a renamed
    count would silently lose its description — and gh-1042 established that
    "every parameter in the signature has an entry" is the rule with no
    exceptions.

    Examples
    --------
    >>> sorted(binding_param_docs())
    ['count', 'out']
    >>> sorted(binding_param_docs("n"))
    ['n', 'out']
    """
    return {
        count_kwarg_name(count_name): COUNT_PARAM_DOC,
        "out": OUT_PARAM_DOC,
    }


def count_stub_default(count_default: str) -> str:
    """The `.pyi` default for the synthesized ``count``, as source text.

    gh-1051. The binding seeds ``count`` from ``count_default`` — a C
    expression evaluated before ``PyArg_ParseTupleAndKeywords`` — so a method
    declaring one has a zero-arg behaviour that is *not* ``count=1``. The two
    stub generators disagreed about this for the same manifest and the same
    method: the standalone one rendered ``...``, and the module-aggregated one
    hard-coded ``1``. So doppler's ``ReedSolomon.generator``, whose real
    default is ``nroots + 1``, advertised a length its own kernel refuses.

    gh-657 fixed the stub *omitting* ``count`` and did not carry the value
    through, taking it from "missing" to "present and wrong" — which type
    checkers, IDE tooltips and ``help()`` all repeat.

    Answered here, once, because this is the second time these two generators
    were found disagreeing about jm's own binding arguments (gh-1042 was the
    first, over whether they are documented at all).

    An integer literal renders as itself — truthful, and better than the
    ellipsis both faces would otherwise show. Anything else is a C expression
    with no Python spelling, so it renders as ``...``: the stub's way of
    saying "there is a default and it is not written here", which is honest
    where a literal would be a lie.

    Parameters
    ----------
    count_default : str
        The manifest's ``count_default``. Empty means the historical ``1``,
        which is genuinely the value the binding uses.

    Returns
    -------
    str
        Source text for the right-hand side of ``count: int = ...``.

    Examples
    --------
    >>> count_stub_default("")
    '1'
    >>> count_stub_default("4")
    '4'
    >>> count_stub_default("  16  ")
    '16'
    >>> count_stub_default("state->rs.code.nroots + 1")
    '...'
    >>> count_stub_default("NROOTS")
    '...'
    """
    expr = count_default.strip()
    if not expr:
        return "1"
    return expr if expr.isdigit() else "..."
