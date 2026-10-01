"""gh-1739: every key naming one seam function must agree on its prototype.

A composer's bridge header declares each straight-C seam the manifest names
(gh-998) once per FUNCTION, however many keys name it. Two owned-pointer
fields (gh-1711) naming one ``parse_fn`` with ``parse_why`` (gh-1735) on
only one of them were accepted: the header declared the first field's
prototype, the second field's call site did not match it, and the author got
a C compiler error about conflicting types instead of a manifest error.

A prototype is a property of the function, not of the key, so the rule is
general: ``_composer._seams`` -- the one list the bridge header and
``seam_fns`` read -- refuses any two keys that name one function and would
declare it differently, naming the function, both keys and both prototypes.
That covers a shared ``copy_fn`` / ``free_fn`` / ``format_fn`` over two
``type`` s and a ``coerce_str_fn`` (gh-1709) that collides with another
seam. Keys that agree are still declared once.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _composer  # noqa: E402
from test_gh1711_composer_owned_pointer import FRAME, MOD, _with  # noqa: E402
from test_gh1736_coerce_str_fn_papercuts import (  # noqa: E402
    _fields,
    _pattern,
)


def _two(**second: object) -> dict:
    """The shared fixture with ``frame`` and a second owned-pointer field
    ``frame2`` naming the same four functions, *second* overriding it."""
    cfg = _with(**FRAME)
    cfg["module"][MOD]["source"]["fields"].append(
        {**FRAME, "name": "frame2", **second}
    )
    return cfg


def _refusal(cfg: dict) -> str:
    with pytest.raises(ValueError) as exc:
        _composer.render_bridge_h(cfg, MOD)
    return str(exc.value)


class TestDisagreementIsRefused:
    def test_parse_why_on_one_field_only(self) -> None:
        msg = _refusal(_two(parse_why=True))
        assert "source field `frame` (parse_fn)" in msg, msg
        assert "source field `frame2` (parse_fn)" in msg, msg
        assert "`frame_parse`" in msg, msg
        # What differs: both prototypes, side by side.
        assert "frame_parse(const char *);" in msg, msg
        assert "frame_parse(const char *, const char **why);" in msg, msg

    def test_every_renderer_reaching_the_header_refuses(self) -> None:
        # The binding includes the header, so it cannot render the two
        # disagreeing call sites the issue's trigger showed.
        with pytest.raises(ValueError, match="gh-1739"):
            _composer.render_ext(_two(parse_why=True), MOD)
        with pytest.raises(ValueError, match="gh-1739"):
            _composer.seam_fns(_two(parse_why=True), MOD)

    def test_a_shared_copy_fn_over_two_types(self) -> None:
        cfg = _two(
            type="other_t *",
            free_fn="other_free",
            parse_fn="other_parse",
            format_fn="other_format",
        )
        msg = _refusal(cfg)
        assert "source field `frame` (copy_fn)" in msg, msg
        assert "source field `frame2` (copy_fn)" in msg, msg
        assert "`frame_copy`" in msg, msg
        assert "wfm_frame_desc_t *frame_copy(const wfm_frame_desc_t *);" in msg
        assert "other_t *frame_copy(const other_t *);" in msg, msg

    def test_one_function_under_two_owned_pointer_keys(self) -> None:
        # The class is the FUNCTION, not the key: one field's free_fn named
        # as another's format_fn is the same conflict.
        cfg = _two(
            copy_fn="f2_copy",
            free_fn="f2_free",
            parse_fn="f2_parse",
            format_fn="frame_free",
        )
        msg = _refusal(cfg)
        assert "source field `frame` (free_fn)" in msg, msg
        assert "source field `frame2` (format_fn)" in msg, msg

    def test_a_coerce_str_fn_colliding_with_an_owned_pointer_fn(
        self,
    ) -> None:
        cfg = _fields(_pattern("bits", "frame_parse"), dict(FRAME))
        msg = _refusal(cfg)
        assert "source field `bits` (coerce_str_fn)" in msg, msg
        assert "source field `frame` (parse_fn)" in msg, msg


class TestAgreementIsStillAccepted:
    def test_identical_declarations_share_one_prototype(self) -> None:
        h = _composer.render_bridge_h(_two(), MOD)
        for fn in ("frame_copy", "frame_free", "frame_parse", "frame_format"):
            assert h.count(f"{fn}(") == 1, (fn, h)
        assert _composer.seam_fns(_two(), MOD).count("frame_parse") == 1

    def test_identical_parse_why_on_both_fields(self) -> None:
        cfg = _with(**{**FRAME, "parse_why": True})
        cfg["module"][MOD]["source"]["fields"].append(
            {**FRAME, "name": "frame2", "parse_why": True}
        )
        h = _composer.render_bridge_h(cfg, MOD)
        assert h.count("frame_parse(const char *, const char **why);") == 1

    def test_a_coerce_str_fn_shared_by_fields(self) -> None:
        cfg = _fields(
            _pattern("bits", "host_bits"), _pattern("pilot", "host_bits")
        )
        h = _composer.render_bridge_h(cfg, MOD)
        assert h.count("size_t host_bits(") == 1, h
        assert "`bits`, `pilot`" in h, h
