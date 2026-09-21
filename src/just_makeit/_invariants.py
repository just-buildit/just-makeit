"""The contract between two members that speak one element (gh-1404).

A width family states its contract as a table::

    +-----------+-------------------------------+-------------------------------+
    |           |        write(x) takes         |        wait(n) returns        |
    +-----------+-------------------------------+-------------------------------+
    | F32Buffer | 1-D complex64                 | 1-D complex64                 |
    | F64Buffer | 1-D complex128                | 1-D complex128                |
    | I16Buffer | 1-D [('i','<i2'),('q','<i2')] | 1-D [('i','<i2'),('q','<i2')] |
    +-----------+-------------------------------+-------------------------------+

**What you read is exactly what you can write.** ``[[<obj>.records]]``
makes the two faces read one declaration, so a divergence is no longer
*representable* -- but that is a property of the generator, and a property
nothing exercises is one nobody notices losing. Writing the check by hand
once per width is how the three rows drift apart, which is the whole reason
this is generated: the invariant is the same sentence for every instance of
a family, and only the generator knows every instance.

Why this is a separate file
---------------------------
``src/<pkg>/tests/test_<comp>.py`` is the AUTHOR's (`_createonly.py`), and
the classification is enforced structurally -- ``_apply._sync_missing``
skips any path that already exists. jm renders its *starting* content and
never writes it again, so an invariant appended there would reach new
projects only, never an existing one, and never the adopter this was built
for. gh-1361 met the same wall on the C side and answered it the same way
(`_linkcheck`): a separate, jm-owned file, so ``apply`` never edits a file
the author owns and an existing project gets it with nothing to migrate.
Cheaper here than there -- pytest collects by filename, so there is no
build wiring to add.

What is asserted, and when
--------------------------
A freshly scaffolded kernel does not work yet: ``jm`` writes ``return
NULL;`` for a borrowing reader, which the binding turns into a raise. So a
round trip generated unconditionally would be **red on every new project**,
and jm's standing rule is that every valid command sequence produces a
scaffold that passes.

The split is therefore by what is observable, not by a flag:

- **Always** -- the input face. That the declared dtype is accepted and a
  foreign one is REFUSED is a property of the binding (gh-1405's
  ``PyArray_EquivTypes`` guard), true whatever the kernel does.
- **Once the kernel is real** -- the round trip, which needs a working
  kernel to say anything at all. It is not generated with a ``skipif``: a
  test that reports success while covering nothing is the failure mode
  `_hollow.py` exists to catch, and a permanent skip is that with extra
  steps. The file is DERIVED and rewritten on every ``apply``, so the round
  trip simply appears when the author implements the pair.

"Is the kernel real" is answered from the CODE, not from a marker jm left
behind: a body that suppresses its own ``state`` parameter cannot have
moved a sample anywhere. See :func:`kernel_is_stub`, which is deliberately
wrong only in the safe direction.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import _config as C
from . import _record
from ._docstring import class_import_line as _class_import_line
from ._context._state import _unseedable_required
from . import _textio
from . import _types as T


class Pair:
    """One element, the member that writes it and the one that reads it."""

    def __init__(self, element: str, ctype: str, writer: str, reader: str):
        self.element = element
        self.ctype = ctype
        self.writer = writer
        self.reader = reader

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"Pair({self.element!r}, {self.ctype!r}, "
            f"{self.writer!r}, {self.reader!r})"
        )


def _writes(m: dict, element: str) -> bool:
    """True when *m* takes rows of *element* in.

    Examples
    --------
    >>> _writes({"arg_type": "sample[]"}, "sample")
    True
    >>> _writes({"arg_type": "sample"}, "sample")
    False
    >>> _writes({"arg_type": "double[]"}, "sample")
    False
    """
    return str(m.get("arg_type") or "") == f"{element}[]"


def _reads(m: dict, element: str) -> bool:
    """True when *m* hands rows of *element* back.

    Three spellings reach one meaning, which is why this is a predicate and
    not an ``==``: a borrow and a ``variable_output`` name the element in
    ``return_type``, and a record array names it in ``record_dtype``. A
    plain scalar return is NOT a read of the element -- it hands back one
    value, not the rows.

    Examples
    --------
    >>> _reads({"return_type": "sample", "borrow": True}, "sample")
    True
    >>> _reads({"return_type": "sample", "variable_output": True}, "sample")
    True
    >>> _reads({"record_dtype": "iq16_t", "variable_output": True}, "iq16_t")
    True
    >>> _reads({"return_type": "sample"}, "sample")
    False
    """
    if str(m.get("record_dtype") or "") == element:
        return bool(m.get("variable_output") or m.get("borrow"))
    if str(m.get("return_type") or "") == element:
        return bool(m.get("variable_output") or m.get("borrow"))
    return False


def pairs(cfg: dict, comp: str) -> "list[Pair]":
    """Every (element, writer, reader) this component declares.

    One per combination, deliberately: two readers of one element are two
    contracts, and the family's sentence is true of both. Nothing is
    inferred about which pairing was *meant* -- a declaration jm cannot
    read is a declaration jm does not act on.
    """
    out: list[Pair] = []
    members = C.methods(cfg, comp)
    for rec in C.records(cfg, comp):
        name = str(rec.get("name") or "")
        if not name:
            continue
        ctype = _record.element_ctype(rec)
        writers = [m["name"] for m in members if _writes(m, name)]
        readers = [m["name"] for m in members if _reads(m, name)]
        for w in writers:
            for r in readers:
                out.append(Pair(name, ctype, w, r))
    return out


def _dtype_expr(rec: dict) -> str:
    """The numpy dtype a declared element describes, as Python source.

    A struct element's dtype is built by the generated C from ``offsetof``
    and ``sizeof``, so the test reads it back off the binding rather than
    restating the layout -- restating it here would be a second description
    of the same bytes, which is the drift `[[<obj>.records]]` removes.
    """
    if _record.is_scalar_element(rec):
        meta = T._CTYPE_META.get(str(rec.get("type") or ""))
        return str(meta["py_type"]) if meta else "None"
    return ""


def declared_dtype_expr(rec: dict) -> str:
    """The DECLARED layout of a struct element, as numpy source (gh-1432).

    The binding builds the real dtype in C from ``offsetof``/``sizeof``, so
    this is a second description of the same bytes -- and here that is the
    point rather than the hazard: the generated test hands it to the
    writer, and the binding **refuses** a dtype that is not its own. If the
    declared layout and the compiler's disagree (padding, reordering), the
    write is rejected and the test goes red, which is exactly the drift
    `[[<obj>.records]]` exists to surface.

    Reading it off the reader instead was tried and does not work: the
    reader needs data, seeding data needs the dtype, and on a fresh ring
    the read returns NULL. A file that parses and cannot run is the defect
    this whole issue is about.

    Examples
    --------
    >>> declared_dtype_expr({"fields": [{"name": "i", "type": "int16_t"},
    ...                                 {"name": "q", "type": "int16_t"}]})
    'np.dtype([("i", np.int16), ("q", np.int16)])'
    """
    cols = []
    for f in _record.declared_fields(rec):
        meta = T._CTYPE_META.get(str(f.ctype or ""), {})
        py = str(meta.get("py_type") or "")
        if not py:
            return ""
        cols.append(f'("{f.name}", {py})')
    return f"np.dtype([{', '.join(cols)}])" if cols else ""


def _foreign_dtype(rec: dict) -> str:
    """A dtype the element is NOT, for the refusal check.

    Chosen to differ in *itemsize* as well as kind wherever possible: numpy
    accepts a same-itemsize structured dtype with its fields in the other
    order and hands C the bytes unchanged (gh-1405 measured exactly that),
    so a near-miss is the interesting negative, not a wild one.
    """
    if _record.is_scalar_element(rec):
        ct = str(rec.get("type") or "")
        return "np.float64" if ct != "double" else "np.int8"
    return "np.float64"


def file_for(root: Path, pkg: str, comp: str, module: str = "") -> Path:
    """Where the generated invariants live for *comp*.

    Beside the user-owned ``test_<comp>.py``, which for an object in a
    module is the MODULE's tests directory -- not the package's. Written
    to the package root the file also imported from the wrong place, and
    the two were the same mistake: an object in a module lives in the
    module, on both faces (gh-1432).
    """
    pypath = C.module_paths(module).pypath if module else ""
    base = root / "src" / pkg
    if pypath:
        for part in pypath.split("/"):
            base = base / part
    return base / "tests" / f"test_{comp}_invariants.py"


def kernel_is_stub(core_c: str, fn: str) -> bool:
    """True when *fn*'s body in ``_core.c`` demonstrably does nothing.

    Conservative by construction, and deliberately a property of the CODE
    rather than a marker jm left behind: a body carrying ``(void)state;``
    suppresses its own state parameter, so it cannot have moved a single
    sample anywhere. Every jm scaffold stub says exactly that; an author
    who implements the member uses ``state`` and the suppression goes.

    Being wrong in the safe direction is the point. A body jm reads as a
    stub only means the round trip is not generated YET -- it never makes
    an assertion that is false. The reverse would be a test that is red on
    a new project, which is the one outcome this file must not produce.

    Examples
    --------
    >>> body = lambda inner: chr(10).join(["int f(s *st)", "{", inner, "}"])
    >>> kernel_is_stub(body("    (void)state;"), "f")
    True
    >>> kernel_is_stub(body("    state->n += 1;"), "f")
    False
    >>> kernel_is_stub("", "f")
    True
    """
    m = re.search(
        r"\b" + re.escape(fn) + r"\s*\([^)]*\)\s*\{(.*?)\n\}",
        core_c,
        re.S,
    )
    if m is None:
        # No body to read is not evidence of an implementation.
        return True
    return "(void)state;" in m.group(1)


def render(
    cfg: dict,
    comp: str,
    pkg: str,
    found: "list[Pair]",
    core_c: str = "",
    root: Path = Path("."),
) -> str:
    """The generated pytest module, or ``""`` when there is nothing to say.

    Returning ``""`` is the single predicate deciding whether the file
    exists; `_apply` is gated on the same call, because a caller that
    decides for itself is how gh-942's enumerated shapes went missing one
    at a time.
    """
    if not found:
        return ""
    cls = C.class_name(cfg, comp) or C.default_class_name(comp)
    recs = {str(r.get("name") or ""): r for r in C.records(cfg, comp)}
    create = _create_args(cfg, comp, pkg, root)
    # gh-1432: a required init-param with no default is seeded `0`, and a
    # validating create() refuses it -- so every test here fails on a
    # project that is perfectly correct. The USER-OWNED scaffold already
    # skips for exactly this, from this predicate; asking it here rather
    # than re-deriving is what keeps the two files agreeing about which
    # constructors are callable.
    _unseeded = _unseedable_required(C.init_params(cfg, comp))

    lines = [
        f'"""Generated by just-makeit -- the element contract for {comp}.',
        "",
        "DO NOT EDIT: `just-makeit apply` rewrites this file. It is jm's,",
        "not yours -- your own tests belong beside it in",
        f"test_{comp}.py, which jm writes once and never touches again.",
        "",
        "What you read is exactly what you can write. Both faces take their",
        f"element from ONE `[[{comp}.records]]` declaration, so this asserts",
        "the generator wired them from the same source.",
        '"""',
        "",
        "import numpy as np",
        "import pytest",
        "",
        # gh-1432: the same emitter the `.pyi` and the runtime docstrings
        # use, so this file cannot disagree with them about where the class
        # is importable from. `from <pkg> import <cls>` is simply wrong for
        # an object in a module, and doppler met it as an ImportError.
        _class_import_line(pkg, cls, C.module_of(cfg, comp) or ""),
        "",
        "",
    ]
    _struct = [
        p
        for p in found
        if not _record.is_scalar_element(recs.get(p.element, {}))
    ]
    for p in _struct:
        expr = declared_dtype_expr(recs.get(p.element, {}))
        if expr:
            # ONE blank line after the import block, not two: isort (ruff
            # `I001`) wants the two-line gap only when a `def`/`class`
            # follows, and this is an assignment. `ruff format` accepts
            # either spelling, so the gh-1432 formatter pass cannot see
            # this -- but `ruff check --fix` rewrites the file, and jm
            # then calls its own file STALE. The `pytestmark` branch
            # below replaces the same trailing blank and already gets
            # this right; this one carried a leading "" it did not need.
            lines[-1:] = [f"_ELEM_DTYPE = {expr}", "", ""]
        break
    if _unseeded:
        names = ", ".join(_unseeded)
        lines[-1:] = [
            "pytestmark = pytest.mark.skip(",
            f'    "required constructor parameter(s) {names} have no "',
            '    "default; seed valid arguments to enable these checks"',
            ")",
            "",
            "",
        ]
    # One input-face test per WRITER, not per pair: it exercises the
    # writer alone, so emitting it once per reader would be the same
    # assertion repeated under different names.
    seen: set[str] = set()
    for p in found:
        if p.writer in seen:
            continue
        seen.add(p.writer)
        rec = recs.get(p.element, {})
        lines += _input_face(
            p,
            cls,
            create,
            _dtype_expr(rec),
            _foreign_dtype(rec),
            _record.is_scalar_element(rec),
        )
    for p in found:
        rec = recs.get(p.element, {})
        if not _record.is_scalar_element(rec):
            # A struct element's round trip needs the binding's own dtype;
            # gh-1414 covers it. The input face above already runs.
            continue
        if kernel_is_stub(core_c, f"{comp}_{p.reader}"):
            continue
        lines += round_trip_block(p, cls, create, _dtype_expr(rec))
    # gh-1432: every pair may be skipped -- a struct element whose reader is
    # still a stub emits neither face -- and a file with a header and no
    # test is the hollow shape `_hollow.py` exists to catch. `""` is the
    # single predicate deciding whether the file exists, so it has to
    # answer for "nothing to say" as well as "nothing declared".
    if not any(ln.startswith("def test_") for ln in lines):
        return ""
    # gh-1432: `rstrip`, because each block ends with the two blank lines
    # PEP 8 puts BETWEEN top-level functions -- a separator used as a
    # terminator, so the last block left the file ending `)\n\n\n`. Every
    # formatter and `end-of-file-fixer` trims it, the committed bytes stop
    # matching the render, and `jm status --check` calls jm's OWN file
    # STALE: a drift gate red on a file nobody edited.
    return "\n".join(lines).rstrip("\n") + "\n"


def _create_args(cfg: dict, comp: str, pkg: str, root: Path) -> str:
    """The constructor call, taken from jm's OWN render slot.

    ``py_create_args`` is what every other generated caller of this
    constructor already uses -- the scaffolded pytest, the doctests, the
    demo app. Deriving a second answer here is how two callers of one
    constructor end up disagreeing about its signature, so this asks the
    context builder rather than re-reading `init_params`.
    """
    from . import _glue

    try:
        ctx = _glue.component_ctx(cfg, comp, pkg, root)
    except Exception:
        # The contract file must never be the reason `apply` dies; without
        # a context there is simply nothing to generate.
        return ""
    return str(ctx.get("py_create_args", ""))


def _py_literal(c_literal: str) -> str:
    """A C default rendered as the Python the generated test can pass.

    Examples
    --------
    >>> _py_literal("0.0f"), _py_literal("1.0"), _py_literal("16384")
    ('0.0', '1.0', '16384')
    >>> _py_literal("NULL")
    'None'
    """
    v = c_literal.strip()
    if v == "NULL":
        return "None"
    if re.fullmatch(r"-?\d*\.?\d+[fF]", v):
        return v[:-1]
    return v


def _input_face(
    p: "Pair", cls: str, create: str, dt: str, foreign: str, scalar: bool
) -> "list[str]":
    """The assertions that hold whatever the kernel does.

    Which dtype the writer accepts is decided by the BINDING, so these are
    true on a freshly scaffolded kernel -- that is what lets this file
    exist from day one without turning a new project red.
    """
    if scalar:
        seed = [f"    x = np.zeros(4, dtype={dt})"]
    else:
        # gh-1432: a struct element's dtype is the BINDING's -- built by the
        # generated C from `offsetof`/`sizeof`, never restated here, which
        # is the whole point of declaring the element once. This used a
        # name (`_ELEM_DTYPE`) that nothing ever assigned: the file read
        # fine and raised `NameError` on the first line that ran, and
        # doppler's ruff refused to commit it at all (F821).
        #
        # Reading it off the reader is the spelling `_dtype_expr`'s own
        # docstring describes, and it is why this shape is emitted only
        # when the reader's kernel is real -- exactly as the round trip is.
        seed = ["    x = np.zeros(4, dtype=_ELEM_DTYPE)"]
    return [
        f"def test_{p.writer}_speaks_{p.element}():",
        f'    """{p.writer}() accepts the declared element, and only it."""',
        f"    obj = {cls}({create})",
        *seed,
        f"    obj.{p.writer}(x)",
        "",
        f"    wrong = np.zeros(4, dtype={foreign})",
        "    with pytest.raises((TypeError, ValueError)):",
        f"        obj.{p.writer}(wrong)",
        "",
        "",
    ]


def round_trip_block(p: "Pair", cls: str, create: str, dt: str) -> "list[str]":
    """The full invariant -- generated only once the kernel is real.

    Same dtype, same rank, and the bytes that come back are the bytes that
    went in. Not generated against a scaffold stub: a borrowing reader's
    stub returns ``NULL``, so the assertion would be red on every new
    project rather than telling anyone anything.
    """
    return [
        f"def test_{p.element}_round_trip():",
        '    """What you read is exactly what you can write."""',
        f"    obj = {cls}({create})",
        f"    x = np.arange(8, dtype={dt})",
        f"    obj.{p.writer}(x)",
        f"    y = obj.{p.reader}(len(x))",
        "",
        "    assert y.dtype == x.dtype",
        "    assert y.ndim == x.ndim",
        "    np.testing.assert_array_equal(y, x)",
        "",
        "",
    ]


def write(root: Path, cfg: dict, comp: str, pkg: str) -> bool:
    """Write the invariants file for *comp*; True when it changed.

    Removes a stale file when the component no longer declares a pair, so
    a renamed or deleted member cannot leave a test behind asserting a
    contract nothing has any more -- the orphan shape `_hollow.py` catches
    for C targets.
    """
    found = pairs(cfg, comp)
    # gh-1432: a header-only component has NO `_core.c` -- its kernels are
    # `static inline` in the header. Reading only the `.c` meant the stub
    # check saw an empty file, every kernel read as a stub, and the shape
    # doppler actually ships (a header-only ring) got no invariants at all.
    # Where the body lives follows `header_only`, exactly as the core
    # library's kind does.
    core = (
        root / "native" / "inc" / comp / f"{comp}_core.h"
        if C.is_header_only(cfg, comp)
        else root / "native" / "src" / comp / f"{comp}_core.c"
    )
    core_c = core.read_text(encoding="utf-8") if core.exists() else ""
    text = render(cfg, comp, pkg, found, core_c, root)
    out = file_for(root, pkg, comp, C.module_of(cfg, comp) or "")
    if not text:
        if out.exists():
            out.unlink()
            return True
        return False
    if out.exists() and out.read_text(encoding="utf-8") == text:
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    _textio.write_text(out, text)
    return True
