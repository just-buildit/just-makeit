"""The same object must document itself the same way on every producer.

**This gate exists because notes did not work.** gh-805 §H took four releases
to become correct, and every one of the four was the same habit: a value wired
to one surface and not its sibling.

===========  ======================  ==================================
release      wired                   not wired
===========  ======================  ==================================
0.54.0       the context builder     the ``jm apply`` replay
0.54.1       the standalone replay   the module replay
0.54.2       the emitted body        the ``.pyi`` ``Raises``
0.54.3       one doc-face producer   the module-aggregated ``.pyi``
===========  ======================  ==================================

Each time the tests passed, because each test exercised the surface that had
just been wired. And the rule was already written down -- *"count the
BUILDERS, not just the producers; jm has FIVE .pyi producers, and fixing one
is how gh-747 happened"* -- in a note, where it did nothing.

So the invariant is gated rather than described: **scaffold one object twice,
standalone and inside a module, and require both stubs to document every
shared member identically.** It is registration-free by construction -- no
list to extend, no new feature to remember -- so the next producer wired in
one place and not its sibling fails here rather than downstream.

`_KNOWN_DIVERGENT` is a ratchet, not an excuse: it holds what was already
broken when the gate landed, each entry pointing at its issue, and it may
only shrink.
"""

from __future__ import annotations

import ast
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

# Members whose two producers already disagreed when this gate landed.
# Each entry is a BUG, not a waiver: the module-aggregated stub renders a
# brief-only docstring where the standalone renders the full numpy block --
# the same object documented less because it lives in a module. This is
# gh-642's defect (brief-only on one face) surviving between the two *stub*
# producers rather than between runtime and stub.
#
# Tracked as gh-867. The set may only shrink; deleting an entry is the fix
# landing, and `test_the_ratchet_only_holds_real_divergence` fails if an entry
# stops diverging, so this cannot rust into a permanent allowlist.
_KNOWN_DIVERGENT: set[str] = set()


def _docstrings(pyi: Path) -> dict[str, str]:
    """``{member_name: docstring}`` for every method of every class in *pyi*.

    Parsed with `ast` rather than grepped because a docstring's *content* is
    the thing under test, and a regex over stub source cannot tell a section
    header from prose that happens to look like one.
    """
    tree = ast.parse(pyi.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        for fn in cls.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            doc = ast.get_docstring(fn)
            if doc is not None:
                out[fn.name] = doc
    return out


def _scaffold(root: Path, module: str | None) -> Path:
    """One object, built standalone or into *module*; returns its ``.pyi``."""
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
        error="ValueError",
        error_message="the capture has a hole",
    )
    cfg = C.load(root)
    C.set_destroy_spec(cfg, "w", {"returns": "int", "exit": "close"})
    C.save(root, cfg)
    apply_run(root)
    return (
        root / "src" / "p" / module / f"{module}.pyi"
        if module
        else root / "src" / "p" / "w.pyi"
    )


@pytest.fixture(scope="module")
def faces(tmp_path_factory) -> tuple[dict[str, str], dict[str, str]]:
    base = tmp_path_factory.mktemp("parity")
    standalone = _docstrings(_scaffold(base / "sa", None))
    module = _docstrings(_scaffold(base / "mo", "m"))
    return standalone, module


class TestBothStubProducersAgree:
    """The standalone and module-aggregated `.pyi` document members alike."""

    def test_the_two_trees_actually_built(self, faces):
        # A gate that silently compared two empty dicts would pass forever.
        standalone, module = faces
        assert standalone, "standalone stub produced no documented members"
        assert module, "module stub produced no documented members"

    def test_they_share_the_members_under_test(self, faces):
        standalone, module = faces
        shared = set(standalone) & set(module)
        assert {"__exit__", "__enter__", "destroy"} <= shared, sorted(shared)

    def test_every_shared_member_documents_identically(self, faces):
        standalone, module = faces
        divergent = sorted(
            name
            for name in set(standalone) & set(module)
            if standalone[name] != module[name]
            and name not in _KNOWN_DIVERGENT
        )
        assert not divergent, (
            "these members document differently depending on whether the "
            f"object is standalone or in a module: {divergent}. A second "
            "producer was wired in one place and not the other — see this "
            "file's docstring for why that keeps happening."
        )

    def test_the_ratchet_only_holds_real_divergence(self, faces):
        # An entry that no longer diverges must be DELETED, or the ratchet
        # rusts into a permanent allowlist that hides the next regression.
        standalone, module = faces
        stale = sorted(
            name
            for name in _KNOWN_DIVERGENT
            if name in standalone
            and name in module
            and standalone[name] == module[name]
        )
        assert not stale, (
            f"{stale} no longer diverge — remove them from "
            "_KNOWN_DIVERGENT so the gate keeps its teeth."
        )


class TestLifecycleGlueSpecifically:
    """The surface §H broke four times, pinned on both producers at once."""

    @pytest.mark.parametrize("member", ["__enter__", "__exit__", "destroy"])
    def test_glue_docs_match_across_producers(self, faces, member):
        standalone, module = faces
        assert standalone[member] == module[member]

    def test_exit_describes_finalizing_on_both(self, faces):
        # gh-864 defect 3: the module stub said "releasing" over a body that
        # finalizes, and agreed with nothing.
        for face in faces:
            assert "finalizing" in face["__exit__"]
            assert "releasing" not in face["__exit__"]

    def test_destroy_raises_the_inherited_class_on_both(self, faces):
        # gh-864 defect 1: the stub named RuntimeError over a ValueError body.
        for face in faces:
            assert "ValueError" in face["destroy"]
            assert "RuntimeError\n" not in face["destroy"]
