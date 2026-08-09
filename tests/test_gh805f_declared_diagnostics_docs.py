"""The failure surface jm declares, jm documents (gh-805 §F, was gh-668).

jm already knows what a construction can do to you: `create_error` /
`create_error_message` (gh-482) name the exception a NULL `create()` becomes,
and `[[<obj>.warnings]]` (gh-481) name the category emitted after a successful
one. jm is also the **only** thing that knows — no header `@throws` can name
an exception class jm picked, and no manifest author should have to restate a
declaration that already implies it.

gh-869 did this for a *method*'s `status_return`/`error_negative`. The
constructor's half stayed unwired, so an object could declare a `ValueError`
and document nothing.

Two things this pins beyond "the section appears":

**Both faces, or neither.** The `.pyi` and the runtime `tp_doc` are separate
producers, and gh-446/gh-642/gh-871 are all the same bug — one face wired, its
sibling not. `help(Obj)` and `Obj.pyi` must state the same failure surface.

**Existing projects, not just fresh ones.** `_docsync` reclaims a `tp_doc`
only when it still matches today's scaffold form, and declaring a
`create_error` *moves* that form — so an already-materialised fragment matched
neither derived nor fallback and was classified hand-written. That is the
gh-871 shape exactly one slot over: correct in every fresh render and in the
`.pyi`, permanently stale in the runtime face of every project that already
existed.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._docstring import _SECTION_ORDER
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run

_ERR = {
    "create_error": "ValueError",
    "create_error_message": "rate must be positive",
}
_WARN = [
    {
        "after": "__init__",
        "condition": "underpowered",
        "category": "UserWarning",
        "message": "under-powered",
    }
]


def _project(tmp_path, *, module=None, declare=True, name="p"):
    """Scaffold one object, optionally declaring the failure surface."""
    root = tmp_path / name
    with contextlib.redirect_stdout(io.StringIO()):
        new_run(name, root, [], [])
        if module:
            module_run(root, module)
        object_run(
            root,
            "w",
            module,
            arg_type="float",
            return_type="float",
            state_vars=[("gain", "double", "1.0")],
        )
    if declare:
        cfg = C.load(root)
        cfg["w"].update(_ERR)
        cfg["w"]["warnings"] = list(_WARN)
        C.save(root, cfg)
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(io.StringIO()):
            apply_run(root)
    return root


def _pyi(root, module=None):
    return (
        root / "src" / "p" / (f"{module}.pyi" if module else "w.pyi")
    ).read_text()


def _fragment(root, module):
    return (root / "native" / "src" / module / f"{module}_ext_w.c").read_text()


def test_the_stub_documents_the_declared_constructor_failure(tmp_path):
    """`create_error` reaches the class docstring's Raises section."""
    text = _pyi(_project(tmp_path))
    assert "Raises" in text, (
        f"a declared create_error is documented nowhere:\n{text[:900]}"
    )
    assert "ValueError" in text
    # Whitespace-collapsed: the description wraps to the column target like
    # every other section, so the quoted message legitimately spans lines.
    assert "rate must be positive" in " ".join(text.split()), (
        "the declared message is what the caller sees at the REPL, so the "
        "docstring must quote it rather than paraphrasing"
    )


def test_the_stub_documents_the_declared_warning(tmp_path):
    """`[[obj.warnings]]` reaches a Warns section, not a Warnings one.

    numpydoc has both, and they are different sections: `Warns` is the
    structured list of categories a call may emit, `Warnings` is free
    cautionary prose. A declared category belongs in the former.
    """
    text = _pyi(_project(tmp_path))
    assert "\n    Warns\n    -----\n" in text, (
        f"no Warns section for a declared warning:\n{text[:900]}"
    )
    assert "UserWarning" in text and "under-powered" in text
    assert "underpowered" in text, (
        "the gating state field is the one part of the contract visible in "
        "the author's C and nowhere in the Python signature"
    )


def test_the_two_faces_agree(tmp_path):
    """`help(Obj)` and `Obj.pyi` state the same failure surface.

    The recurring bug (gh-446, gh-642, gh-871) is one face wired and its
    sibling not, so this asserts the runtime `tp_doc` carries what the stub
    carries rather than merely carrying *something*.
    """
    root = _project(tmp_path, module="m")
    frag = _fragment(root, "m")
    for probe in ("Raises", "ValueError", "Warns", "UserWarning"):
        assert probe in frag, (
            f"the runtime tp_doc omits {probe!r} while the .pyi documents "
            f"it — the two faces of one object disagree"
        )


def test_the_sections_are_in_numpydoc_order(tmp_path):
    """Order is load-bearing: tooling parses by section.

    griffe and numpydoc's own validator associate a body with the preceding
    heading, so `Warns` above `Raises` silently re-attributes the text.
    """
    text = _pyi(_project(tmp_path))
    order = [
        text.index(f"\n    {h}\n") for h in ("Parameters", "Raises", "Warns")
    ]
    assert order == sorted(order), (
        f"class sections are out of numpydoc order: {order}"
    )
    assert _SECTION_ORDER.index("Warns") == _SECTION_ORDER.index("Raises") + 1


def test_an_object_declaring_neither_is_unchanged(tmp_path):
    """No churn for the common case.

    Every existing project declares neither, and a docstring that grew an
    empty section — or moved a line — would report drift in all of them.
    """
    text = _pyi(_project(tmp_path, declare=False))
    assert "Raises" not in text and "Warns" not in text


def test_the_undeclared_memory_error_stays_undocumented(tmp_path):
    """jm's plumbing is not the author's contract.

    Every object raises `MemoryError` when `create()` returns NULL, exactly
    as every wrapper carries the `RuntimeError "destroyed"` liveness guard.
    `declared_raise` leaves that one out deliberately; documenting the other
    everywhere would bury the entry that is actually about this component.
    """
    text = _pyi(_project(tmp_path, declare=False))
    assert "MemoryError" not in text


def test_an_existing_fragment_picks_it_up(tmp_path):
    """The gh-871 shape, one slot over — and the reason this test exists.

    Declaring `create_error` moves the scaffold form `_docsync` compares
    against, so the already-materialised fragment matched neither the derived
    nor the fallback render and was read as hand-written. Every fresh render
    and the `.pyi` were correct, so nothing failed; the runtime face of every
    existing project simply stayed wrong, and `jm status` said up to date.
    """
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        module_run(root, "m")
        object_run(root, "w", "m", arg_type="float", return_type="float")
        apply_run(root)
    before = _fragment(root, "m")
    assert "Raises" not in before, "setup: the fragment already documents it"

    cfg = C.load(root)
    cfg["w"].update(_ERR)
    C.save(root, cfg)
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(io.StringIO()):
            apply_run(root)

    after = _fragment(root, "m")
    assert "Raises" in after and "ValueError" in after, (
        "an existing fragment never picked up the declared exception, so "
        "help(Obj) and the .pyi beside it disagree permanently"
    )


def test_a_hand_written_class_docstring_survives(tmp_path):
    """The reclaim is licensed by `"<Component> type."` being jm's own text.

    Anything else in that slot was written by a human and stays. Without this
    the fix above would be a licence to overwrite prose.
    """
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        module_run(root, "m")
        object_run(root, "w", "m", arg_type="float", return_type="float")
        apply_run(root)
    frag = root / "native" / "src" / "m" / "m_ext_w.c"
    frag.write_text(
        frag.read_text().replace(
            '.tp_doc       = "W type.\\n",',
            '.tp_doc       = "Hand-written summary.\\n",',
        )
    )
    cfg = C.load(root)
    cfg["w"].update(_ERR)
    C.save(root, cfg)
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(io.StringIO()):
            apply_run(root)
    assert "Hand-written summary." in frag.read_text(), (
        "the tp_doc reclaim overwrote prose a human wrote"
    )
