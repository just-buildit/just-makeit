"""A signature override's .pyi documents the symbol it BINDS.

gh-1012 gave a view method its own signature by giving it its own C symbol
(``fn``). Both faces then have to describe *that* symbol — and only one of
them did.

The runtime face reads the override's block, because ``_make_view_ctx``
renders from the method entry. The stub face looked its block up as
``<component>_<member>``, a key derived from the member NAME, which for a view
resolves through ``_view_doc_blocks``'s re-keying to the **parent's** block
(gh-685, correct for every inherited member and wrong for exactly this one).

So a view whose ``block`` takes ``float`` was documented in the ``.pyi`` with
the parent's ``float _Complex`` example: a doctest constructing the parent
class and passing it a complex array, sitting under the view's class. No
manifest or header configuration could make both faces right — removing the
override's ``@code`` only swapped the runtime face onto jm's synthesized
example while the stub kept the parent's authored one — which is why a
downstream doc-face-parity gate could not be satisfied at all.

A second, quieter half came with it. jm scaffolds its skeleton ``@brief`` from
the Python member name (``@brief block.``) while ``parse_doxygen_block``
recognises a scaffold by the name it derives from the C *symbol* — the same
string for every method until ``fn`` made them differ, since ``rx_block_real``
derives ``block_real`` and ``@brief block.`` does not match it. An
UNDOCUMENTED override therefore rendered its own skeleton as prose in one path
and the name-based fallback in the other, so ``jm method`` and ``jm apply``
disagreed about a file neither author had touched. Both halves are pinned
below.

Found in doppler, collapsing two M-PSK receivers into one object whose
real-input face is a view with ``f32`` ``steps``/``bits`` — the exact shape
gh-1012 was filed for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from just_makeit import _view
from just_makeit._apply import run as apply_run
from just_makeit._method import run as method_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run

HDR = Path("native/inc/rx/rx_core.h")
SCAFFOLD_BRIEF = " * @brief block."


def _base(tmp_path, *, override: bool):
    """A module object with a complex `block`, and a view over it."""
    dest = tmp_path / "demo"
    new_run("demo", dest, [], [], build_system="cmake")
    module_run(dest, "dsp")
    object_run(
        dest,
        "rx",
        module="dsp",
        state_vars=[("sps", "double", "8.0")],
        arg_type="float _Complex",
        return_type="float _Complex",
    )
    method_run(
        dest,
        "rx",
        "block",
        "dsp",
        "float _Complex",
        "float _Complex",
        True,
        [],
    )
    _view.run(dest, "rx", "RxReal", "dsp", "rx_create_real")
    if override:
        method_run(
            dest,
            "rx",
            "block",  # the PARENT's name, deliberately
            "dsp",
            "float",  # ...with its own input dtype
            "float _Complex",
            True,
            [],
            fn="rx_block_real",  # ...carried by its own C symbol
            view="RxReal",
        )
    return dest


def _author(dest, symbol, marker):
    """Replace *symbol*'s scaffold brief with an authored one, in place.

    Located by walking back from the DECLARATION rather than by replacing the
    first ``@brief block.`` in the file: the parent's skeleton and the
    override's are byte-identical, so a positional replace would pin nothing
    about which block reached which face — the very question this file asks.
    """
    hdr = dest / HDR
    text = hdr.read_text(encoding="utf-8")
    at = text.index(f"size_t {symbol}(")
    start = text.rindex("/**", 0, at)
    block = text[start:at]
    assert SCAFFOLD_BRIEF in block, f"no scaffold brief above {symbol}"
    authored = block.replace(
        SCAFFOLD_BRIEF,
        f" * @brief Authored for {symbol}.\n"
        " *\n"
        " * @code\n"
        f" * >>> {marker}\n"
        " *\n"
        " * @endcode",
        1,
    )
    hdr.write_text(text[:start] + authored + text[at:], encoding="utf-8")


def _pyi(dest):
    return (dest / "src" / "demo" / "dsp" / "dsp.pyi").read_text(
        encoding="utf-8"
    )


def _class_body(pyi, name):
    marker = f"\nclass {name}:"
    assert marker in pyi, f"no `class {name}:` in the stub"
    rest = pyi[pyi.index(marker) + 1 :]
    nxt = rest.find("\nclass ")
    return rest if nxt < 0 else rest[:nxt]


@pytest.fixture()
def documented_override(tmp_path):
    """A view overriding `block`, with a DIFFERENT example on each symbol."""
    dest = _base(tmp_path, override=True)
    _author(dest, "rx_block", "PARENT_EXAMPLE_MARKER")
    _author(dest, "rx_block_real", "VIEW_EXAMPLE_MARKER")
    apply_run(dest)
    return dest


class TestOverrideStubDocs:
    def test_the_stub_documents_the_symbol_the_view_binds(
        self, documented_override
    ):
        view = _class_body(_pyi(documented_override), "RxReal")
        assert "VIEW_EXAMPLE_MARKER" in view
        assert "PARENT_EXAMPLE_MARKER" not in view

    def test_the_runtime_face_documents_the_same_symbol(
        self, documented_override
    ):
        """The half that was already right — pinned so a fix cannot trade one
        face for the other, which is the failure mode being repaired."""
        frag = (
            documented_override / "native/src/dsp/dsp_ext_rxreal.c"
        ).read_text(encoding="utf-8")
        assert "VIEW_EXAMPLE_MARKER" in frag
        assert "PARENT_EXAMPLE_MARKER" not in frag

    def test_the_parent_keeps_its_own(self, documented_override):
        parent = _class_body(_pyi(documented_override), "Rx")
        assert "PARENT_EXAMPLE_MARKER" in parent
        assert "VIEW_EXAMPLE_MARKER" not in parent


class TestInheritedMembersAreUnchanged:
    """gh-685 is the reason the name-derived key existed, and it stays.

    A view member the parent owns has no ``fn``, so its key is still the
    re-keyed synthetic name and it still resolves to the parent's block —
    which for an inherited member is the RIGHT block, since it calls the
    parent's C function.
    """

    def test_an_inherited_method_still_reads_the_parents_block(self, tmp_path):
        dest = _base(tmp_path, override=False)
        _author(dest, "rx_block", "INHERITED_EXAMPLE_MARKER")
        apply_run(dest)
        assert "INHERITED_EXAMPLE_MARKER" in _class_body(_pyi(dest), "RxReal")


class TestUndocumentedOverrideIsStillAScaffold:
    """The quieter half: an override with no authored doc renders the
    name-based fallback on BOTH paths, so `jm method` and `jm apply` agree.

    jm writes its skeleton brief from the member name and recognises a
    scaffold by the name derived from the C symbol; `fn` is the first thing
    that made those two differ. Without the member-name sentinel the override
    renders its own skeleton (`block.` plus generated @param prose) as if an
    author had written it — richer than the fallback, and different from what
    a manifest-only rebuild produces, which is what `jm status --check` calls
    stale.
    """

    def test_the_skeleton_is_not_mistaken_for_authored_prose(self, tmp_path):
        dest = _base(tmp_path, override=True)
        before = _pyi(dest)
        apply_run(dest)
        assert _pyi(dest) == before

    def test_it_renders_the_name_based_fallback(self, tmp_path):
        dest = _base(tmp_path, override=True)
        apply_run(dest)
        view = _class_body(_pyi(dest), "RxReal")
        assert '"""Block."""' in view
