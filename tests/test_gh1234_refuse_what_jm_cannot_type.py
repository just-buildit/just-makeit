"""gh-1234: two places gh-1224's `object` gave an answer it could not stand by.

Reported against released 0.73.0 by a downstream that adopted the pin the same
day. Both halves are the same defect in different clothes -- jm producing a
*confident* result for a case it has no information about -- and both are fixed
here by refusing rather than by answering, because the feature that would let
jm answer is a key a producer does not have yet (gh-1235).

1. `object` on a composer `source.fields` row came out of the renderer as
   ``KeyError: 'type'``. A composer field is a member of the source struct, not
   a constructor argument, so `object` is a real key one table over -- and
   gh-1227 already settled that jm should say what a name IS before saying what
   it is not. Nothing validates a composer sub-table row at all, which is why
   the key got as far as the renderer; that is gh-1236 and is deliberately not
   fixed here.

2. `resolve_object_ref` read the capsule NAME from the producer and then
   *derived* the C type it points at, from the component id. That is right for
   the default producer and only for it: a `type = "capsule"` property
   publishes ``expr or "self->handle"``, so an `expr` reaching a member
   publishes something else while the resolver went on saying
   ``<comp>_state_t *``. The consumer's `create()` was generated taking a type
   the capsule does not carry -- a silent confusion across exactly the ABI
   boundary the capsule triangle exists to protect, and the worst possible
   failure for a feature whose whole argument was "the name is read, not
   derived".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _composer  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from test_composer_codegen import _cfg  # noqa: E402


def _producer(**prop_extra: object) -> dict:
    """A one-component project whose `frame` publishes a capsule."""
    prop = {
        "name": "_capsule",
        "type": "capsule",
        "capsule": "p.frame.desc",
    }
    prop.update(prop_extra)
    return {
        "project": {"name": "p", "version": "0.1.0"},
        "frame": {
            "arg_type": "double",
            "return_type": "double",
            "properties": [prop],
        },
    }


class TestAComposerFieldSaysWhichKeyIsWrong:
    """Finding 1: a refusal that names the key, not a KeyError from inside."""

    def _render(self, field: dict) -> None:
        cfg = _cfg()
        cfg["module"]["wfm_compose"]["source"]["fields"].append(field)
        _composer.render_source_type(cfg, "wfm_compose")

    def test_object_on_a_field_is_refused_not_a_keyerror(self) -> None:
        with pytest.raises(ValueError) as exc:
            self._render({"name": "frame", "object": "frame.FrameDesc"})
        msg = str(exc.value)
        assert "composer source field 'frame'" in msg
        # It names the key the author wrote, and where that key IS valid --
        # the gh-1227 principle. A message that only said "no `type`" would
        # send them to add one to a field that should not exist.
        assert "object" in msg
        assert "init_param key" in msg
        assert "gh-1235" in msg

    def test_it_is_not_a_keyerror(self) -> None:
        """The literal regression. `KeyError: 'type'` is not a ValueError, so
        a caller that catches jm's own error class saw the crash escape."""
        with pytest.raises(ValueError):
            self._render({"name": "frame", "object": "frame.FrameDesc"})

    def test_a_typeless_field_with_no_stray_key_still_says_what_to_do(
        self,
    ) -> None:
        """The plain omission, without the `object` hint: it must still name
        every shape that stands in for a `type`, or the author of a `bytes`
        field is told to add a scalar type they must not add."""
        with pytest.raises(ValueError) as exc:
            self._render({"name": "frame"})
        msg = str(exc.value)
        assert "no `type`" in msg
        for shape in ("enum", "bytes", "complex"):
            assert shape in msg
        assert "init_param key" not in msg

    def test_a_type_jm_cannot_marshal_is_refused_by_name(self) -> None:
        """The neighbouring case, refused in the same voice rather than
        `KeyError` one line later."""
        with pytest.raises(ValueError) as exc:
            self._render({"name": "frame", "type": "wfm_frame_desc_t *"})
        assert "not a type a field can cross as" in str(exc.value)

    def test_a_well_formed_composer_still_renders(self) -> None:
        """The guard is a refusal, not a narrowing -- every shape the fixture
        already declares (scalar, enum, bytes) must survive it."""
        out = _composer.render_source_type(_cfg(), "wfm_compose")
        assert "Synth_init" in out


class TestTheResolverRefusesAPointerItCannotType:
    """Finding 2: no confident answer without the information to back it."""

    def test_an_expr_publishing_producer_is_refused(self) -> None:
        with pytest.raises(ValueError) as exc:
            C.resolve_object_ref(_producer(expr="&self->handle->d"), "frame")
        msg = str(exc.value)
        # The message quotes what the producer actually publishes: the reader
        # has to see WHICH declaration made the reference unresolvable.
        assert "&self->handle->d" in msg
        assert "frame_state_t *" in msg
        assert "gh-1235" in msg
        # ...and the escape hatch that still works today, with the capsule
        # name filled in so it can be copied rather than reconstructed.
        assert "p.frame.desc" in msg

    def test_the_default_producer_still_resolves(self) -> None:
        """`self->handle` IS `<comp>_state_t *`, so the derivation is sound
        for the default and must not be collateral damage."""
        ctype, capsule, header, cls = C.resolve_object_ref(
            _producer(), "frame"
        )
        assert ctype == "frame_state_t *"
        assert capsule == "p.frame.desc"
        assert header == "frame/frame_core.h"
        assert cls == "Frame"

    def test_spelling_the_default_expr_out_is_not_a_refusal(self) -> None:
        """An author who writes the default explicitly means the default.
        Refusing it would make the message a lie -- it says the pointer is not
        the state pointer, and here it is."""
        ctype, _, _, _ = C.resolve_object_ref(
            _producer(expr="self->handle"), "frame"
        )
        assert ctype == "frame_state_t *"

    def test_a_producer_with_no_capsule_still_says_so_first(self) -> None:
        """Ordering: `expr` without `capsule` is not a capsule property at
        all, so the older refusal has to win or the reader is told to fix a
        pointer type when what is missing is the declaration."""
        cfg = _producer()
        cfg["frame"]["properties"] = [{"name": "gain", "type": "double"}]
        with pytest.raises(ValueError) as exc:
            C.resolve_object_ref(cfg, "frame")
        assert "publishes no capsule" in str(exc.value)


def test_the_capsule_name_lookup_is_unchanged_by_the_refactor() -> None:
    """`object_ref_capsule` now reads its answer out of
    `object_ref_capsule_prop`. One walk over `properties`, two callers -- so
    they cannot disagree about which row wins when a component declares more
    than one capsule property."""
    cfg = _producer(expr="&self->handle->d")
    cfg["frame"]["properties"].append(
        {"name": "_second", "type": "capsule", "capsule": "p.frame.other"}
    )
    assert C.object_ref_capsule(cfg, "frame") == "p.frame.desc"
    assert C.object_ref_capsule_prop(cfg, "frame")["capsule"] == "p.frame.desc"
