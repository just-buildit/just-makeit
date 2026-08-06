"""One shape for a single-record result, shared by every face (gh-646).

A method declared ``single = true`` with ``result_fields`` returns one named
record. The C binding builds it as a ``PyStructSequence`` -- a tuple subclass
whose fields are also reachable by name -- and the ``.pyi`` describes it to the
type checker.

Before gh-646 the two faces described that record differently and neither
described it fully: the C descriptor passed ``NULL`` for the type doc and
``NULL`` for every field doc, so ``help(ToneMetrics)`` was empty and
``ToneMetrics.enob`` carried no text; the ``.pyi`` annotated the return as a
bare ``tuple[float, float]``, which types unpacking but leaves ``r.enob``
unknown to the checker and undocumented to the reader. Three writers needed the
same four answers -- the record's public name, its qualified name, its fields
with their docs, and its own doc -- so they live here once rather than as three
derivations that agree until one of them is edited.

Doc sources, in precedence order, for both the record and each field:

1. the manifest -- ``record_doc`` on the method, ``doc =`` on a result field;
2. the sacred header's trailing ``///<`` / ``/**<`` member doc for a field of
   that name (gh-671), which is where a C author has usually already written
   it;
3. for the record only, a CPython-style synopsis (``ToneMetrics(enob, sfdr)``)
   -- factual rather than invented prose, and the same shape CPython's own
   struct sequences carry.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from . import _types as T
from ._docstring import member_doc


class RecordField(NamedTuple):
    """One field of a single-record result: its name, C type, and doc."""

    name: str
    ctype: str
    doc: str


def parse_result_field(val: str) -> dict:
    """Parse one ``--result-field name:type[:doc]`` argument.

    ``jm method`` and ``jm function`` both take this flag and each had its own
    copy of the split-and-validate, so gh-646's optional third component would
    have had to be added twice.

    Raises
    ------
    ValueError
        With the message the CLI prints, when the spelling is wrong or the type
        is not a scalar jm can convert.

    Examples
    --------
    >>> parse_result_field("enob:double")
    {'name': 'enob', 'type': 'double'}
    >>> parse_result_field("enob:double:Effective bits.")
    {'name': 'enob', 'type': 'double', 'doc': 'Effective bits.'}
    """
    if ":" not in val:
        raise ValueError(f"--result-field '{val}' must be name:type[:doc]")
    # At most two splits, so the doc itself may contain colons.
    name, ctype, *rest = val.split(":", 2)
    if ctype not in T._CTYPE_META:
        raise ValueError(
            f"--result-field type '{ctype}' is not a scalar.\n"
            f"Supported: {', '.join(sorted(T._CTYPE_META))}"
        )
    out = {"name": name, "type": ctype}
    doc = rest[0].strip() if rest else ""
    if doc:
        out["doc"] = doc
    return out


def is_record(m: dict) -> bool:
    """True when *m* returns ONE named record (a ``PyStructSequence``)."""
    return bool(m.get("single")) and bool(m.get("result_fields"))


def public_name(m: dict) -> str:
    """The record's public Python type name.

    A manifest ``record_name`` (gh-257) wins outright; otherwise the name is
    derived from the C return type by dropping a ``_t`` suffix and camel-casing
    what is left, so ``tone_metrics_t`` becomes ``ToneMetrics``.

    Examples
    --------
    >>> public_name({"record_name": "ToneMetrics"})
    'ToneMetrics'
    >>> public_name({"return_type": "tone_metrics_t"})
    'ToneMetrics'
    >>> public_name({"return_type": ""})
    'Record'
    """
    if m.get("record_name"):
        return str(m["record_name"])
    rt = str(m.get("return_type", ""))
    base = rt[:-2] if rt.endswith("_t") else rt
    return "".join(w.capitalize() for w in base.split("_") if w) or "Record"


def qualified_name(m: dict, component: str) -> str:
    """The dotted name the structseq reports as ``type(r).__name__``.

    A manifest ``record_module`` (gh-261) qualifies the record with the
    project's import path (``doppler.measure.ToneMetrics``); otherwise the C
    component name stands in.
    """
    return f"{m.get('record_module') or component}.{public_name(m)}"


def fields(m: dict, doc_blocks: dict | None = None) -> list[RecordField]:
    """The record's fields, each carrying whatever documentation exists."""
    return [
        RecordField(
            f["name"],
            f["type"],
            str(f.get("doc") or "") or member_doc(doc_blocks, f["name"]),
        )
        for f in m.get("result_fields", [])
    ]


def type_doc(m: dict, flds: list[RecordField]) -> str:
    """The record type's own documentation.

    Falls back to the CPython-style synopsis rather than to nothing: an empty
    ``help(ToneMetrics)`` tells a reader at the REPL less than the field order
    does, and the synopsis is derived, not invented.

    Examples
    --------
    >>> f = [RecordField("enob", "double", ""), RecordField("sfdr", "double", "")]
    >>> type_doc({"record_name": "ToneMetrics"}, f)
    'ToneMetrics(enob, sfdr)'
    >>> type_doc({"record_name": "T", "record_doc": "Results."}, f)
    'Results.'
    """
    declared = str(m.get("record_doc") or "").strip()
    if declared:
        return declared
    return f"{public_name(m)}({', '.join(f.name for f in flds)})"


# ── the C face ──────────────────────────────────────────────────────────────


def _c_literal(text: str) -> str:
    """*text* as a C string literal, or the bare token ``NULL`` when empty."""
    if not text:
        return "NULL"
    body = text.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + body.replace("\n", "\\n") + '"'


def descriptor_c(
    sid: str, qualname: str, doc: str, flds: list[RecordField]
) -> str:
    """The static ``PyStructSequence`` descriptor for one record method.

    *sid* is the translation-unit-unique prefix the wrapper already uses, so
    the emitted type stays file-local and module-init wiring is untouched.
    """
    rows = "".join(f'    {{"{f.name}", {_c_literal(f.doc)}}},\n' for f in flds)
    return (
        f"static PyStructSequence_Field {sid}_fields[] = {{\n"
        f"{rows}"
        f"    {{NULL, NULL}},\n"
        f"}};\n"
        f"static PyStructSequence_Desc {sid}_desc = {{\n"
        f'    "{qualname}", {_c_literal(doc)},'
        f" {sid}_fields, {len(flds)}\n"
        f"}};\n"
        f"static PyTypeObject *{sid}_type = NULL;\n\n"
    )


def dtype_c(sid: str, record_t: str, flds: list[RecordField]) -> str:
    """A cached ``PyArray_Descr *`` matching the C record's layout exactly.

    gh-788 gap 1. doppler's ``Telemetry.read()`` returns one row per 16-byte C
    record and the drain is a single ``memcpy``, because the numpy dtype *is*
    the struct layout::

        dtype([("n", "<u8"), ("value", "<f4"), ("probe", "<u2"),
               ("flags", "<u2")])

    jm had no ``PyArray_Descr`` concept at all — ``variable_output`` returns a
    plain typed array of one element type — so the module's primary read path
    could not be declared and the whole thing stayed ``no_generate``.

    **The offsets come from ``offsetof`` and the itemsize from ``sizeof``**,
    rather than letting numpy pack the field list itself. That is the whole
    correctness argument. numpy's default for a list of ``(name, format)``
    pairs is *packed*; C inserts padding to satisfy alignment. The two agree
    for doppler's record by luck — ``uint64, float, uint16, uint16`` is 16
    bytes either way — and disagree the moment a field ordering needs padding,
    at which point every row after the first is read from the wrong bytes.
    Deriving the layout from the compiler cannot drift from what the compiler
    actually did, so the ``memcpy`` this exists to enable is safe by
    construction rather than by review.

    The per-field type comes from ``_NP_ENUM``, the same table the plain array
    paths use, so a newly registered ctype reaches this without a second
    mapping to forget (the gh-450 lesson: a parallel table drifts).

    Cached in a file-scope static because a descr is immutable and building
    one per call would allocate four Python objects per read on the hot path.
    """
    n = len(flds)
    name_args = ", ".join(f'"{f.name}"' for f in flds)
    off_args = ",\n        ".join(
        f"(Py_ssize_t)offsetof({record_t}, {f.name})" for f in flds
    )
    set_fmts = "".join(
        f"    PyList_SET_ITEM(formats, {i},\n"
        f"        (PyObject *)PyArray_DescrFromType("
        f"{T._NP_ENUM[T._CTYPE_META[f.ctype]['py_type']]}));\n"
        for i, f in enumerate(flds)
    )
    return (
        f"static PyArray_Descr *{sid}_dtype = NULL;\n\n"
        f"/* The record's numpy dtype, built from the compiler's own layout:\n"
        f"   offsetof/sizeof, never numpy's packing rules, so a padded\n"
        f"   struct cannot silently read every row after the first from the\n"
        f"   wrong bytes. */\n"
        f"static PyArray_Descr *\n"
        f"{sid}_get_dtype(void)\n"
        f"{{\n"
        f"    PyObject *names = NULL, *formats = NULL;\n"
        f"    PyObject *offsets = NULL, *spec = NULL;\n"
        f"    PyArray_Descr *out = NULL;\n"
        f"    if ({sid}_dtype) {{\n"
        f"        Py_INCREF({sid}_dtype);\n"
        f"        return {sid}_dtype;\n"
        f"    }}\n"
        f'    names = Py_BuildValue("[{"s" * n}]", {name_args});\n'
        f"    if (!names) goto done;\n"
        f"    formats = PyList_New({n});\n"
        f"    if (!formats) goto done;\n"
        f"{set_fmts}"
        f'    offsets = Py_BuildValue("[{"n" * n}]",\n'
        f"        {off_args});\n"
        f"    if (!offsets) goto done;\n"
        f'    spec = Py_BuildValue("{{s:O,s:O,s:O,s:n}}",\n'
        f'        "names", names, "formats", formats,\n'
        f'        "offsets", offsets,\n'
        f'        "itemsize", (Py_ssize_t)sizeof({record_t}));\n'
        f"    if (!spec) goto done;\n"
        f"    if (!PyArray_DescrConverter(spec, &out)) out = NULL;\n"
        f"done:\n"
        f"    Py_XDECREF(names);\n"
        f"    Py_XDECREF(formats);\n"
        f"    Py_XDECREF(offsets);\n"
        f"    Py_XDECREF(spec);\n"
        f"    if (out) {{\n"
        f"        {sid}_dtype = out;\n"
        f"        Py_INCREF({sid}_dtype);\n"
        f"    }}\n"
        f"    return out;\n"
        f"}}\n\n"
    )


def find_descriptor(text: str, sid: str) -> str:
    """The file-scope structseq statics for wrapper *sid* in *text*, or ``""``.

    gh-729. :func:`descriptor_c` emits three file-scope declarations that the
    wrapper function then references. A **full** render prepends them to the
    function, so they always travel together — but the incremental path that
    adds a member to a sacred ``_ext_<obj>.c`` fragment splices *functions*
    (located by name) and *PyMethodDef rows*, and a static is neither. The
    fragment gained a body referencing ``<sid>_type`` with nothing declaring
    it, and did not compile.

    Kept next to the emitter on purpose: whatever ``descriptor_c`` writes is
    what this has to find, and a copy of that shape living in the splicer is a
    copy that goes stale the first time the descriptor changes.
    """
    m = re.search(
        rf"static PyStructSequence_Field {re.escape(sid)}_fields\[\]"
        rf".*?static PyTypeObject \*{re.escape(sid)}_type = NULL;\n",
        text,
        re.DOTALL,
    )
    return m.group(0) if m else ""


def find_dtype(text: str, sid: str) -> str:
    """The file-scope dtype cache and its builder for *sid* in *text*, or ``""``.

    gh-788, and the exact peer of :func:`find_descriptor` — for the same
    reason and with the same failure mode. :func:`dtype_c` emits two coupled
    file-scope things: the cached ``PyArray_Descr *`` and the function that
    fills it. The incremental splice path (gh-729/gh-779) carries *functions*
    the new wrapper calls and *file-scope declarations with an initialiser*
    it references by name, and this block is neither in the shape that path
    recognises: the wrapper references only ``<sid>_get_dtype()``, which is a
    definition rather than an initialised declaration, and the cache it reads
    is referenced by the builder rather than by the wrapper. Splicing the
    wrapper alone would leave a call to an undeclared function — the gh-729
    symptom, one costume along.

    Kept beside the emitter deliberately: what ``dtype_c`` writes is what this
    has to find, and a copy of that shape living in the splicer is a copy that
    goes stale the first time the emitter changes.

    Examples
    --------
    >>> c = dtype_c("W_read", "rec_t", [RecordField("n", "uint64_t", "")])
    >>> found = find_dtype("/* head */\\n" + c + "\\nstatic int other = 0;", "W_read")
    >>> found.startswith("static PyArray_Descr *W_read_dtype = NULL;")
    True
    >>> "W_read_get_dtype(void)" in found and found.rstrip().endswith("}")
    True
    >>> "other" in found
    False
    >>> find_dtype("nothing here", "W_read")
    ''
    """
    m = re.search(
        rf"static PyArray_Descr \*{re.escape(sid)}_dtype = NULL;"
        # ...through the builder's closing brace, which clang-format keeps
        # at column 0 exactly as it does for every other function.
        rf".*?\n{re.escape(sid)}_get_dtype\(void\).*?\n\}}\n",
        text,
        re.DOTALL,
    )
    return m.group(0) if m else ""


# ── the Python face ─────────────────────────────────────────────────────────


def annotation(flds: list[RecordField]) -> str:
    """The tuple annotation a record unpacks as (``tuple[float, float]``)."""
    types = ", ".join(T.scalar_py_annotation(f.ctype) for f in flds)
    return f"tuple[{types}]"


def pyi_class(name: str, doc: str, flds: list[RecordField]) -> str:
    """The ``.pyi`` class for one record type.

    Subclassing the fixed-length tuple keeps everything the bare ``tuple[...]``
    annotation already gave -- unpacking and indexing both type-check -- and
    adds the two things it could not express: ``r.enob`` is typed, and both the
    record and each field carry their documentation.
    """
    lines = ["@final", f"class {name}({annotation(flds)}):", '    """' + doc]
    documented = [f for f in flds if f.doc]
    if documented:
        lines += ["", "    Attributes", "    ----------"]
        for f in documented:
            lines.append(f"    {f.name} : {T.scalar_py_annotation(f.ctype)}")
            lines.append(f"        {f.doc}")
    lines.append('    """')
    for f in flds:
        ann = T.scalar_py_annotation(f.ctype)
        lines.append("")
        lines.append("    @property")
        # An undocumented field gets the plain stub body rather than prose
        # synthesised from its own name: "sfdr dbc" reads as documentation and
        # says nothing, which is worse for the reader than an honest gap.
        if f.doc:
            lines.append(f"    def {f.name}(self) -> {ann}:")
            lines.append(f'        """{f.doc}"""')
        else:
            lines.append(f"    def {f.name}(self) -> {ann}: ...")
    return "\n".join(lines) + "\n"


def pyi_classes(methods: list[dict], doc_blocks: dict | None = None) -> str:
    """Every record class a method table needs, deduplicated by type name.

    Two methods returning the same record declare it once; the first wins, the
    way a C header's first declaration does.
    """
    seen: set[str] = set()
    out: list[str] = []
    for m in methods:
        if not is_record(m):
            continue
        nm = public_name(m)
        if nm in seen:
            continue
        seen.add(nm)
        flds = fields(m, doc_blocks)
        out.append(pyi_class(nm, type_doc(m, flds), flds))
    return "\n".join(out)
