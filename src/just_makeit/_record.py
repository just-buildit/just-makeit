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
