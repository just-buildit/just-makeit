"""The documented exception must be the one the binding actually raises.

Comparing the two doc faces to *each other* — which doppler's
`check_doc_face_parity.py` does, and `test_face_parity_gate.py` does here —
cannot catch the case where both faces agree and both are wrong. gh-864
produced that twice:

- ``__exit__`` said "releasing the X" on **both** faces over a body that
  finalizes and leaves the object alive;
- ``close`` and ``__exit__`` carry **no** ``Raises`` on either face over a
  body that raises ``ValueError`` (gh-869), so a parity gate reports parity.

The only reference that cannot be wrong about what a binding raises is the
**binding**. So this gate reads ``PyExc_<Class>`` out of the generated C and
requires **both** doc faces to name the same class.

Both faces, because gh-869's report is that both were silent. A gate that
checked only the ``.pyi`` would have gone green on a fix that wired the stub
and left the runtime ``PyMethodDef`` literal — the ``help()`` text — still
documenting no exception. That is this repository's most-repeated mistake
(see ``test_face_parity_gate.py``'s table), and a gate reproducing it would
certify the half-fix.

Scope is deliberate. Not every ``PyExc_`` is a *declared* error: ``steps``
emits ``TypeError``/``ValueError`` for argument validation, and every wrapper
opens with the ``RuntimeError "destroyed"`` guard. Those are jm's own
plumbing, not the author's contract. The lifecycle surface — ``destroy`` and
its aliases, ``__exit__``, and the finalizer named by ``exit`` — is where
"the declared exception" is unambiguous, and it is exactly the surface gh-805
§H broke in four consecutive releases.

Both trees are checked, because "standalone works" is what gh-860 taught.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._method import run as method_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run

_DECLARED = "ValueError"

# Members that raise the author's declared exception. `close` is the
# finalizer named by `exit`; `destroy` is the teardown that inherits it;
# `__exit__` calls the finalizer and inherits its raise semantics.
_LIFECYCLE = ("close", "destroy", "__exit__")

# gh-869's own entries are gone: `close` and `__exit__` sat here documenting
# no `Raises` at all, on every producer, and now document it on all of them.
#
# What is left is a DIFFERENT defect, which this gate found by covering the
# runtime face: a module object's `<mod>_ext_<obj>.c` is a sacred fragment,
# and `_docsync._is_reclaimable_glue` will only reclaim a glue docstring that
# is at most one logical line (gh-707's bound, pinned by
# `test_gh703_stale_fragment_doc_refresh.py::test_a_rich_hand_written_glue_
# doc_is_preserved`). Every gh-647-era glue doc is multi-paragraph, so once a
# project has one, no later jm can revise it — gh-805 §H's "finalizing" and
# gh-869's `Raises` both reach a FRESH fragment and neither reaches an
# existing one. Verified: deleting the fragment and re-running `apply`
# renders both correctly.
#
# So the generation is right and the refresh is stale. Tracked as gh-871;
# lifting the bound is a policy change (it trades a hand-edited glue
# docstring for a revisable one) rather than a wiring fix. Ratchet — it may
# only shrink, and `test_the_ratchet_only_holds_real_gaps` fails if an entry
# stops being a gap.
_KNOWN_STALE = {
    ("module", "help()", "destroy"),
    ("module", "help()", "__exit__"),
}

# Every wrapper opens with this; it is jm's plumbing, not a declared error.
_GUARD = re.compile(r'PyExc_RuntimeError,\s*"destroyed"')
_RAISE = re.compile(r"PyErr_(?:SetString|Format)\s*\(\s*PyExc_(\w+)")


def _raised_classes(ext_c: str) -> dict[str, set[str]]:
    """``{c_function_name: {declared exception classes}}`` from generated C.

    The ``"destroyed"`` guard is stripped first: it fires on a torn-down
    object regardless of what the author declared, so counting it would make
    every member look like it raises ``RuntimeError``.
    """
    out: dict[str, set[str]] = {}
    current = ""
    for line in ext_c.splitlines():
        m = re.match(r"^([A-Za-z_]\w*)\s*\(", line)
        if m:
            current = m.group(1)
        if _GUARD.search(line):
            continue
        hit = _RAISE.search(line)
        if hit and current:
            out.setdefault(current, set()).add(hit.group(1))
    return out


_RAISES_HEAD = re.compile(r"^Raises\n-+\n", re.M)


def _raises_in(doc: str) -> set[str]:
    """Exception classes named by *doc*'s numpy ``Raises`` section.

    Anchored on the heading **and its underline**, not on the word. Every
    teardown docstring jm writes carries the sentence "Every other method
    raises ``RuntimeError`` once it has run", and the serializable triplet's
    prose opens entire paragraphs with "Raises ``TypeError`` if ..." — a
    substring search finds those first and reads the surrounding prose as
    entries. Generated code contains prose *about* itself, which is exactly
    what an unanchored detector cannot tell apart from the thing itself.

    One reader for both faces on purpose: they are the same numpy section,
    and two parsers is one more place for the two faces to be compared by
    different rules.
    """
    m = _RAISES_HEAD.search(doc)
    if not m:
        return set()
    out: set[str] = set()
    prev = ""
    for ln in doc[m.end() :].splitlines():
        entry = ln.strip()
        # Indented lines are an entry's description; blanks separate entries.
        if not entry or ln.startswith((" ", "\t")):
            continue
        if set(entry) == {"-"}:
            # An underline: the line above it was the NEXT section's heading,
            # not an exception class. Take it back and stop.
            out.discard(prev)
            break
        out.add(entry)
        prev = entry
    return out


def _documented_raises(pyi: str) -> dict[str, set[str]]:
    """``{member: {classes named in its numpy Raises section}}``."""
    out: dict[str, set[str]] = {}
    tree = ast.parse(pyi)
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        for fn in cls.body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            out[fn.name] = _raises_in(ast.get_docstring(fn) or "")
    return out


# One `PyMethodDef` row: `{"name", (PyCFunction)..., METH_..., "doc" "doc"},`
# The doc is a run of adjacent C string literals, which may start on the row's
# own line and continues until the row closes.
_ROW = re.compile(r'^\s*\{"([A-Za-z_]\w*)",')
_LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')
_ESCAPE = re.compile(r"\\(.)")
_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}


def _unescape(parts: list[str]) -> str:
    """Concatenated C string literals, back to the text they encode.

    Hand-rolled rather than ``codecs.decode(..., "unicode_escape")``: the
    generated prose carries raw UTF-8 (``gh-805 §H``), which that codec
    mojibakes into Latin-1 — and this gate would then be comparing a
    corrupted docstring against a correct one.
    """
    joined = "".join(parts)
    return _ESCAPE.sub(lambda m: _ESCAPES.get(m.group(1), m.group(1)), joined)


def _runtime_docs(ext_c: str) -> dict[str, str]:
    """``{member: PyMethodDef docstring}`` — the ``help()`` face.

    The second doc face, and the one gh-869 shows a ``.pyi``-only gate would
    have let stay wrong: it lives in generated C as a string literal, so no
    Python tool sees it and nothing else in the suite reads it.
    """
    out: dict[str, str] = {}
    current = ""
    buf: list[str] = []
    for line in ext_c.splitlines():
        m = _ROW.match(line)
        if m:
            if current:  # a row that never closed; keep what it had
                out[current] = _unescape(buf)
            current, buf = m.group(1), []
            rest = line[m.end() :]
        elif current:
            rest = line
        else:
            continue
        buf += _LITERAL.findall(rest)
        if rest.rstrip().endswith("},"):
            out[current] = _unescape(buf)
            current, buf = "", []
    if current:
        out[current] = _unescape(buf)
    return out


def _build(root: Path, module: str | None) -> tuple[str, str]:
    """Scaffold one object; return ``(ext_c_text, pyi_text)``."""
    new_run("p", root, [], [])
    if module:
        module_run(root, module)
    object_run(root, "w", module, arg_type="float", return_type="float")
    method_run(
        root,
        "w",
        "close",
        module,
        "void",
        "int",
        False,
        [],
        status_return=True,
        error=_DECLARED,
        error_message="the capture has a hole",
    )
    cfg = C.load(root)
    C.set_destroy_spec(cfg, "w", {"returns": "int", "exit": "close"})
    C.save(root, cfg)
    apply_run(root)
    if module:
        ext = root / "native" / "src" / module / f"{module}_ext_w.c"
        pyi = root / "src" / "p" / module / f"{module}.pyi"
    else:
        ext = root / "native" / "src" / "w" / "w_ext.c"
        pyi = root / "src" / "p" / "w.pyi"
    return ext.read_text(encoding="utf-8"), pyi.read_text(encoding="utf-8")


@pytest.fixture(scope="module", params=["standalone", "module"])
def tree(request, tmp_path_factory):
    base = tmp_path_factory.mktemp(f"bodydoc_{request.param}")
    ext, pyi = _build(base, None if request.param == "standalone" else "m")
    faces = {
        ".pyi": _documented_raises(pyi),
        "help()": {
            name: _raises_in(doc) for name, doc in _runtime_docs(ext).items()
        },
    }
    return _raised_classes(ext), faces, request.param


class TestTheGateIsArmed:
    """It must be able to see both sides before its silence means anything."""

    def test_the_binding_actually_raises_the_declared_class(self, tree):
        raised, _, _ = tree
        emitting = {fn for fn, cs in raised.items() if _DECLARED in cs}
        assert emitting, (
            "no generated function raises the declared exception — the "
            "fixture stopped exercising the feature, so every assertion "
            "below would pass vacuously"
        )

    @pytest.mark.parametrize("face", [".pyi", "help()"])
    def test_the_face_parsed(self, tree, face):
        _, faces, _ = tree
        assert faces[face], f"no members parsed out of the {face} face"

    @pytest.mark.parametrize("face", [".pyi", "help()"])
    def test_the_face_covers_the_lifecycle_surface(self, tree, face):
        # A face missing a member entirely reads as "documents nothing" to
        # the comparison below, which is the direction that fails toward
        # looking correct — the member would simply not be checked.
        _, faces, _ = tree
        assert set(_LIFECYCLE) <= set(faces[face]), sorted(faces[face])

    def test_the_two_faces_are_read_from_different_artifacts(self, tree):
        # The `help()` face is a C string literal and the `.pyi` face is
        # Python source. If a refactor ever pointed both readers at the same
        # text, the gate would still pass while covering one face.
        _, faces, _ = tree
        assert faces[".pyi"] is not faces["help()"]
        assert "steps" in faces["help()"], (
            "the PyMethodDef reader found no `steps` row — it is parsing "
            "something other than the method table"
        )

    def test_the_guard_is_excluded(self, tree):
        # If the "destroyed" guard leaked in, every member would look like it
        # raises RuntimeError and the comparison below would be meaningless.
        raised, _, _ = tree
        assert not any("RuntimeError" in cs for cs in raised.values())


class TestDocumentedMatchesRaised:
    """For the lifecycle surface, each face names what the C raises."""

    @pytest.mark.parametrize("face", [".pyi", "help()"])
    @pytest.mark.parametrize("member", _LIFECYCLE)
    def test_documented_class_is_the_raised_class(self, tree, member, face):
        raised, faces, kind = tree
        if (kind, face, member) in _KNOWN_STALE:
            pytest.skip(f"stale sacred fragment: {kind}/{face}/{member}")
        c_fn = next(
            fn for fn in raised if fn.endswith(f"_{member.strip('_')}")
        )
        assert _DECLARED in raised[c_fn]
        documented = faces[face]
        assert documented[member] == {_DECLARED}, (
            f"{member}: the binding raises {sorted(raised[c_fn])} but the "
            f"{face} face documents {sorted(documented[member]) or 'nothing'}"
            ". Comparing the two doc faces to each other cannot catch this — "
            "only the body can."
        )

    @pytest.mark.parametrize("entry", sorted(_KNOWN_STALE))
    def test_the_ratchet_only_holds_real_gaps(self, tree, entry):
        # An entry that no longer diverges must be DELETED, or the ratchet
        # rusts into a permanent allowlist that hides the next regression.
        _, faces, kind = tree
        e_kind, e_face, e_member = entry
        if kind != e_kind:
            pytest.skip(f"{entry} is not about the {kind} tree")
        assert faces[e_face].get(e_member) != {_DECLARED}, (
            f"{entry} now documents the raised class — remove it from "
            "_KNOWN_STALE so the gate covers it."
        )
