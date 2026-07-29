"""gh-602: two silent papercuts in property/class doc rendering.

1. An object that sets ``create_fn`` to a non-default constructor lost its
   class docstring to the scaffold placeholder — the docstring transplant
   looked up the *derived* ``<comp>_create`` Doxygen block rather than the
   *configured* ``create_fn``, found nothing there (because the derived name
   often does not even exist as a real function once ``create_fn`` is in
   play), and silently fell back to the generic ``"<Component> type."``. This
   is a module-object bug specifically: a standalone object's C-level
   ``tp_doc`` is a fixed template literal that was never Doxygen-derived, but
   both the ``.pyi`` class docstring (any object shape) and a module object's
   C ``tp_doc`` go through the shared lookup this issue is about.

2. A property ``expr`` was wrapped in a cast with no parentheses around the
   expression itself (``(cast)<expr>``), so a ternary or any expression with
   lower precedence than a cast bound wrong — the cast landed on just the
   first operand. Fixed by always parenthesizing the expression before
   casting it (``(cast)(<expr>)``), which is a no-op for a bare field access
   and correct for everything else.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._property import run as property_run
from just_makeit._stubs import class_docstring_block


def _scaffold_module_object(tmp_path, create_fn="acq_create_continuous"):
    root = tmp_path / "dsp"
    new_run("dsp", root)
    module_run(root, "mod")
    object_run(
        root,
        "acq",
        module="mod",
        init_params=[("carrier", "double", "0.05")],
        create_fn=create_fn,
    )
    return root


def _hand_author_create_fn_doc(root, create_fn="acq_create_continuous"):
    """Add a real, hand-written ``create_fn`` function with non-boilerplate
    Doxygen — the derived ``acq_create`` stays as jm's own scaffold text,
    exactly like the doppler `acq`/`CorrDetector2D`-style report."""
    header = root / "native" / "inc" / "acq" / "acq_core.h"
    text = header.read_text(encoding="utf-8")
    text = text.replace(
        "acq_state_t *acq_create(double carrier);",
        "acq_state_t *acq_create(double carrier);\n\n"
        "/**\n"
        " * @brief Create a continuous-mode acquisition engine: always\n"
        " *        wideband window-tiling, never coherent multi-epoch\n"
        " *        combining.\n"
        " * @param carrier  carrier (default: 0.05).\n"
        " * @return Heap-allocated state, or NULL on allocation failure.\n"
        " */\n"
        f"acq_state_t *{create_fn}(double carrier);",
    )
    header.write_text(text, encoding="utf-8")


class TestDocstringTransplantFollowsCreateFn:
    def test_module_ext_c_tp_doc_uses_create_fn_brief(self, tmp_path):
        root = _scaffold_module_object(tmp_path)
        _hand_author_create_fn_doc(root)
        apply_run(root)
        ext = (root / "native" / "src" / "mod" / "mod_ext_acq.c").read_text(
            encoding="utf-8"
        )
        assert "continuous-mode acquisition engine" in ext
        assert '.tp_doc       = "Acq type.\\n"' not in ext

    def test_module_pyi_class_docstring_uses_create_fn_brief(self, tmp_path):
        root = _scaffold_module_object(tmp_path)
        _hand_author_create_fn_doc(root)
        apply_run(root)
        pyi = (root / "src" / "dsp" / "mod" / "mod.pyi").read_text(
            encoding="utf-8"
        )
        assert "continuous-mode acquisition engine" in pyi

    def test_derived_create_still_used_when_create_fn_unset(self, tmp_path):
        # Regression guard: an object with NO create_fn override still keys
        # off the derived <obj>_create Doxygen, exactly as before this fix.
        root = tmp_path / "dsp"
        new_run("dsp", root)
        module_run(root, "mod")
        object_run(
            root,
            "burst",
            module="mod",
            init_params=[("carrier", "double", "0.05")],
        )
        header = root / "native" / "inc" / "burst" / "burst_core.h"
        text = header.read_text(encoding="utf-8")
        text = text.replace(
            "@brief Create a burst instance.",
            "@brief Detect a burst in the incoming stream.",
        )
        header.write_text(text, encoding="utf-8")
        apply_run(root)
        ext = (root / "native" / "src" / "mod" / "mod_ext_burst.c").read_text(
            encoding="utf-8"
        )
        assert "Detect a burst in the incoming stream" in ext

    def test_class_docstring_block_keys_off_create_fn_directly(self):
        # Unit test on the shared builder itself: doc_blocks keyed by the
        # create_fn name, not the derived <obj>_create name.
        class _Block:
            brief = "Create a continuous-mode acquisition engine."
            params = []

            def param_desc(self, _name):
                return None

        doc = class_docstring_block(
            "acq",
            "Acq",
            [],
            True,
            [("carrier", "double", "0.05")],
            "from dsp.mod import Acq",
            "carrier=0.05",
            doc_blocks={"acq_create_continuous": _Block()},
            create_fn="acq_create_continuous",
        )
        assert "continuous-mode acquisition engine" in doc

    def test_class_docstring_block_falls_back_without_create_fn_match(self):
        # doc_blocks has no entry under the derived name and no create_fn
        # override is passed -> generic fallback, unchanged from before.
        doc = class_docstring_block(
            "acq",
            "Acq",
            [],
            True,
            [("carrier", "double", "0.05")],
            "from dsp.mod import Acq",
            "carrier=0.05",
            doc_blocks={},
        )
        assert "Acq type." in doc or "Acq component" in doc


class TestPropertyExprCastIsParenthesized(object):
    def _scaffold(self, tmp_path):
        root = tmp_path / "dsp"
        new_run(
            "dsp",
            root,
            ["agc"],
            [
                ("window_bins", "size_t", "0"),
                ("coherent_bins", "size_t", "0"),
            ],
        )
        return root

    def test_ternary_expr_is_parenthesized_before_cast(self, tmp_path):
        root = self._scaffold(tmp_path)
        property_run(
            root,
            "agc",
            "effective_bins",
            None,
            "size_t",
            False,
            expr=(
                "(self->handle->window_bins > 1) ? "
                "self->handle->window_bins : self->handle->coherent_bins"
            ),
        )
        ext = (root / "native" / "src" / "agc" / "agc_ext.c").read_text(
            encoding="utf-8"
        )
        assert (
            "(unsigned long long)((self->handle->window_bins > 1) ? "
            "self->handle->window_bins : self->handle->coherent_bins)" in ext
        )
        # The buggy, unparenthesized form must not appear.
        assert (
            "(unsigned long long)(self->handle->window_bins > 1) ?" not in ext
        )

    def test_simple_field_expr_still_compiles_the_same(self, tmp_path):
        # A bare member-access expr is unaffected in meaning: adding parens
        # around a single primary expression changes nothing.
        root = self._scaffold(tmp_path)
        property_run(
            root,
            "agc",
            "bins_alias",
            None,
            "size_t",
            False,
            expr="self->handle->window_bins",
        )
        ext = (root / "native" / "src" / "agc" / "agc_ext.c").read_text(
            encoding="utf-8"
        )
        assert "(unsigned long long)(self->handle->window_bins)" in ext
