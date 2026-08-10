"""Generated prose names the class the reader can import.

gh-915. An object may override its Python class name (`--class-name DDC`).
The class itself was always emitted correctly — `class DDC:` in the stub, `DDC`
in the module — while the **prose about it** was built from the derived
title-case name:

    class DDC:
        def __enter__(self) -> "DDC":
            \"\"\"...
            Lets a Ddc be used in a `with` statement ...

            Returns
            -------
            Ddc                      <- a class that cannot be imported
            \"\"\"

Three call sites build this prose and only one was right, which is why it
lasted: `_stubs.py` used `C.class_name(cfg, obj) or _title(obj)`,
`_context/_destroy.py` used `C.default_class_name(component)`, and
`_context/_methods.py` passed `wrapper_prefix` — a **C symbol** prefix that
gains an `Obj` suffix on a no_state object, so a serializable no_state class
was documented as a `DdcObj`.

The distinction the fix preserves: `component` and `ComponentW` name C symbols
(the struct, the static wrappers, the `PyMethodDef` rows) and must not move.
Only the words follow the Python class.

Not a regression — 0.54.3 emits the same `Ddc`. What changed is reachability:
doppler carried correct `DDC` text from an older jm, and the 0.55.1 bump made
`apply` rewrite it. That is how this was found.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run

DERIVED = "Ddc"
DECLARED = "DDC"


def _scaffold(root, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        object_run(
            root,
            "ddc",
            None,
            arg_type="float",
            return_type="float",
            class_name=DECLARED,
            **kw,
        )
    return root


def _generated(root):
    """Every file jm generates, concatenated — prose lives in several."""
    return "".join(
        p.read_text(encoding="utf-8", errors="replace")
        for pat in ("*.pyi", "*.c", "*.h", "*.py")
        for p in root.rglob(pat)
    )


@pytest.mark.parametrize("serializable", [False, True], ids=["plain", "blob"])
def test_the_derived_name_appears_nowhere(tmp_path, serializable):
    """The whole defect in one assertion, over every generated file.

    Anchored on the *absence* of `Ddc` rather than the presence of `DDC`,
    because the class name legitimately appears everywhere and a presence
    check passes with the prose still wrong. `serializable` is parametrised
    because the state-blob triplet is the site that passed `wrapper_prefix` —
    a different wrong answer from the other two, and only reachable here.
    """
    root = _scaffold(
        tmp_path / "p", **({"serializable": True} if serializable else {})
    )
    text = _generated(root)
    assert DERIVED not in text, (
        f"generated prose names a '{DERIVED}', which is not the class jm "
        f"emitted and cannot be imported. Offending lines:\n"
        + "\n".join(ln for ln in text.splitlines() if DERIVED in ln)
    )


def test_the_context_manager_prose_names_the_class(tmp_path):
    """The reader-facing sentence, and the `Returns` type beside it.

    The annotation matters more than the sentence: it is a type a checker
    resolves, naming a class that does not exist.
    """
    root = _scaffold(tmp_path / "p")
    stub = (root / "src/p/ddc.pyi").read_text(encoding="utf-8")
    assert f"Lets a {DECLARED} be used" in stub, stub
    enter = stub[stub.index("def __enter__") :]
    enter = enter[: enter.index("def __exit__")]
    assert DERIVED not in enter, enter


def test_the_class_docstring_names_the_class(tmp_path):
    """`tp_doc`, seeded before the override was known.

    Usually invisible — `_glue.component_ctx` replaces it from `create()`'s
    `@brief` once the header has one — so it is wrong exactly on the objects
    nobody has documented yet, which is why it outlived the other two.
    """
    root = _scaffold(tmp_path / "p")
    ext = (root / "native/src/ddc/ddc_ext.c").read_text(encoding="utf-8")
    tp_doc = [ln for ln in ext.splitlines() if ".tp_doc" in ln]
    assert tp_doc, ext
    assert DECLARED in tp_doc[0] and DERIVED not in tp_doc[0], tp_doc


def test_the_c_symbols_do_not_move(tmp_path):
    """The half that must NOT follow the class name.

    `component` and `ComponentW` name the struct, the static wrapper
    functions and the `PyMethodDef` rows. Renaming those to match a Python
    class would be an ABI change wearing a docs fix's clothes — and it is the
    obvious over-reach, since the fix is "use the class name here" and the
    difference between the two uses is invisible in a diff.
    """
    root = _scaffold(tmp_path / "p", serializable=True)
    ext = (root / "native/src/ddc/ddc_ext.c").read_text(encoding="utf-8")
    assert "ddc_state_t" in ext, "the C state struct was renamed"
    assert "ddc_create" in ext, "the C constructor was renamed"
    assert "static PyObject *" in ext


def test_an_object_without_an_override_is_unchanged(tmp_path):
    """Zero churn on the common path — no object declares `class_name`."""
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        object_run(root, "engine", None, arg_type="float", return_type="float")
    stub = (root / "src/p/engine.pyi").read_text(encoding="utf-8")
    assert "class Engine:" in stub
    assert "Lets a Engine be used" in stub, (
        "the derived name stopped reaching the prose for an object with no "
        "override, so the fallback was lost along with the bug"
    )
    assert C.class_name(C.load(root), "engine") is None


def test_a_no_state_object_is_not_documented_as_its_c_wrapper(tmp_path):
    """The third site, and the only shape that can tell it apart.

    `_context/_methods.py` passed `wrapper_prefix` — which EQUALS `Component`
    for an ordinary object, so every scaffold above renders identically with
    the bug present. It diverges only under `no_state`, where the prefix gains
    an `Obj` suffix (`_state.py`: `"ComponentW": f"{Component}Obj"`), and only
    the serializable triplet's prose reads it. So `no_state + serializable` is
    the one combination that distinguishes them:

        "Raises ``RuntimeError`` if the DDCObj has already been destroyed."

    `DDCObj` is a C static-function prefix. It is not the class, it is not
    importable, and it appears in a sentence written for a Python reader.

    Written after a sabotage check passed with the fix reverted — the
    parametrised test above covers the site's *code path* and could not
    exercise its defect.
    """
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        object_run(
            root,
            "ddc",
            None,
            arg_type="float",
            return_type="float",
            class_name=DECLARED,
            no_state=True,
            serializable=True,
        )
    text = _generated(root)
    assert f"{DECLARED}Obj has" not in text, (
        "the state-blob prose names the C wrapper prefix instead of the "
        "class:\n"
        + "\n".join(
            ln for ln in text.splitlines() if f"{DECLARED}Obj has" in ln
        )
    )
    # The prefix itself must still exist — it names real C functions.
    assert f"{DECLARED}Obj_" in text, (
        "the C wrapper prefix was renamed along with the prose; that is an "
        "ABI change, not a docs fix"
    )
