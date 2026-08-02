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

    def _spaced(self) -> DoxyBlock:
        """This method's block with its paragraphs blank-line separated.

        ``DoxyBlock.body`` holds *lines*, not paragraphs -- that is what the
        parser produces, and ``group_paragraphs`` joins each run of consecutive
        non-blank lines. Authoring one paragraph per list entry here would
        therefore render them merged into a single blob. The blank entries are
        inserted at render time so the definitions above stay readable as
        paragraphs.
        """
        body: list[str] = []
        for para in self.block.body:
            if body:
                body.append("")
            body.append(para)
        return replace(self.block, body=body)

    def pyi_doc(self, indent: int = 8) -> list[str]:
        """Rendered numpy docstring lines for the ``.pyi`` face."""
        return render_numpy_doc(
            self._spaced(),
            self.name,
            self.py_params,
            self.ret_ann,
            indent=indent,
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

    def c_doc_lines(self) -> list[str]:
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
            self._spaced(), self.name, self.py_params, self.ret_ann
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
    Component: str, close_name: str = "destroy"
) -> list[GlueMethod]:
    """``destroy``/``close`` plus the context-manager protocol."""
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
                    f"C resources are released deterministically on exit "
                    f"rather than at collection time.",
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
                brief=f"Exit a context manager, releasing the {Component}.",
                body=[
                    f"Equivalent to calling `{close_name}()`. Returns "
                    "``None``, so an exception raised inside the `with` body "
                    "propagates normally; this never suppresses one.",
                ],
                params=[
                    ("exc_type", "Exception class, or None. Ignored."),
                    ("exc", "Exception instance, or None. Ignored."),
                    ("tb", "Traceback object, or None. Ignored."),
                ],
            ),
        ),
    ]


def glue_methods(
    Component: str, *, close_name: str = "destroy"
) -> dict[str, GlueMethod]:
    """Every generated glue method for *Component*, keyed by Python name.

    Parameters
    ----------
    Component : str
        The wrapped type's Python class name, used in the prose.
    close_name : str, optional
        Name of the explicit-release method (``close`` for reader-shaped
        objects, ``destroy`` otherwise).

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
    for gm in _serialization(Component) + _lifecycle(Component, close_name):
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
