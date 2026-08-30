"""gh-1190: a composer gets the escape hatch every other module kind has.

A `kind = "composer"` module had no hook for a hand-written method. An object
module gets `<mod>_ext_<obj>_extra.c` and `<mod>_ext_extra.c`, both wired in by
`_render` and, per its own comment, "hand-written — jm never modifies".
`_composer.render_ext` built `<cname>_ext.c` whole and included neither.

So a project needing one bespoke method on the generated type had three
options, all bad: hand-edit a file `apply` discards, put a ~3300-line generated
file in `status_allow` and blind every future drift in it, or do without.
doppler#1086 did the third — it shipped the C half of `wfm_compose_draws()` and
left the Python face reaching the same data the long way round.

Why the hook alone was not shippable
------------------------------------
Nothing generated calls into an `_extra.c`, and unlike an object module there
is **no sacred fragment** whose `PyMethodDef` rows survive regeneration — a
composer's `_ext.c` is rewritten wholesale, which is what the report verified.
A function put there would be included and unreachable. So the hook and the
row-declaration are one feature, not two, and shipping half would have been a
papercut that reads as done.

`# jm:hand` is the wrong half here for the same reason it works for object
fragments: those are *sacred*. A composer's `.pyi` is generated wholesale too,
so a hand-written method needs a declared signature to be typed at all.

Why not `methods`
-----------------
The word already exists on this table, and it means something else: on a
capsule or a handle, `[[module.X.methods]]` means "generate the wrapper from
this signature". Here the wrapper already exists. One key with two meanings is
the trap this repo has paid for four times, so the key is `extra_methods`.

And measured while doing it: a composer `methods` table was **recognised and
inert**. `_keys.KIND_TABLE_VOCAB` registered it, `_composer.py` never read one,
so declaring it passed `unknown_keys` in silence and generated nothing — the
gh-816 shape with the registry supplying the silence. It is out of the composer
vocabulary now, so it reports and names the two kinds it is valid for.
"""

from __future__ import annotations

import contextlib
import copy
import io
import sys
import tempfile
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _composer  # noqa: E402
from just_makeit import _keys  # noqa: E402
from test_composer_codegen import _cfg  # noqa: E402

#: doppler#1086's method: no arguments, a list of dicts back.
DRAWS = {
    "name": "draws",
    "fn": "Composer_draws",
    "flags": "METH_NOARGS",
    "doc": "Per-instance draw records.",
    "returns": "list[dict[str, object]]",
}


def _with(*rows) -> dict:
    cfg = copy.deepcopy(_cfg())
    cfg["module"]["wfm_compose"]["extra_methods"] = [dict(r) for r in rows]
    return cfg


class TestTheRowReachesTheType:
    def test_the_method_table_carries_it(self) -> None:
        c = _composer.render_composer_type(_with(DRAWS), "wfm_compose")
        assert '{"draws", (PyCFunction)(void (*)(void))Composer_draws' in c, c

    def test_the_flags_and_doc_are_the_declared_ones(self) -> None:
        c = _composer.render_composer_type(_with(DRAWS), "wfm_compose")
        assert 'METH_NOARGS, "Per-instance draw records."' in c, c

    def test_a_row_with_no_doc_gets_null(self) -> None:
        c = _composer.render_composer_type(
            _with({k: v for k, v in DRAWS.items() if k != "doc"}),
            "wfm_compose",
        )
        assert "METH_NOARGS, NULL}" in c, c

    def test_a_newline_in_the_doc_is_escaped(self) -> None:
        """A multi-line docstring is the normal case for a method summary +
        detail, and an unescaped newline is a broken C literal."""
        c = _composer.render_composer_type(
            _with({**DRAWS, "doc": "draws() -> list\nThe records."}),
            "wfm_compose",
        )
        assert '"draws() -> list\\nThe records."' in c, c

    def test_it_lands_before_the_sentinel(self) -> None:
        c = _composer.render_composer_type(_with(DRAWS), "wfm_compose")
        assert c.index('"draws"') < c.index("{NULL, NULL, 0, NULL}"), c

    def test_no_rows_when_none_are_declared(self) -> None:
        c = _composer.render_composer_type(_cfg(), "wfm_compose")
        assert "draws" not in c


class TestItIsTypedToo:
    """A row with no stub is a member a type checker rejects a call to, which
    is half a feature — and the composer's `.pyi` is generated wholesale, so
    there is nowhere for a hand-written one to survive."""

    def test_the_stub_has_the_method(self) -> None:
        pyi = _composer.render_pyi(_with(DRAWS), "wfm_compose")
        assert "    def draws(self) -> list[dict[str, object]]:" in pyi, pyi

    def test_the_first_doc_line_becomes_the_docstring(self) -> None:
        pyi = _composer.render_pyi(_with(DRAWS), "wfm_compose")
        assert '"""Per-instance draw records."""' in pyi, pyi

    def test_args_are_raw_python(self) -> None:
        """jm does not know the shape — that is the whole point of the escape
        hatch — so it does not try to derive one."""
        pyi = _composer.render_pyi(
            _with({**DRAWS, "args": "n: int = 1", "returns": "bytes"}),
            "wfm_compose",
        )
        assert "def draws(self, n: int = 1) -> bytes:" in pyi, pyi

    def test_no_returns_means_none(self) -> None:
        pyi = _composer.render_pyi(
            _with({k: v for k, v in DRAWS.items() if k != "returns"}),
            "wfm_compose",
        )
        assert "def draws(self) -> None:" in pyi, pyi


class TestTheHandWrittenFileIsIncludedAndUntouched:
    """The other half. Without the include the declared row names a function
    the translation unit has never seen."""

    @staticmethod
    def _materialize(cfg: dict, write_extra: bool):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name) / "p"
        root.mkdir()
        (root / "CMakeLists.txt").write_text("# ── Modules\n")
        d = root / "native" / "src" / "wfm_compose"
        d.mkdir(parents=True)
        extra = d / "wfm_compose_ext_extra.c"
        if write_extra:
            extra.write_text("/* hand written */\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            _composer.materialize(cfg, root, "wfm_compose")
        return td, (d / "wfm_compose_ext.c").read_text(encoding="utf-8"), extra

    def test_it_is_included_when_present(self) -> None:
        td, ext, _ = self._materialize(_with(DRAWS), True)
        with td:
            assert '#include "wfm_compose_ext_extra.c"' in ext, ext
            assert "jm never modifies" in ext, ext

    def test_it_is_not_included_when_absent(self) -> None:
        """A composer that needs no hook gains nothing — including a file that
        is not there is a compile error, not a no-op."""
        td, ext, _ = self._materialize(_with(DRAWS), False)
        with td:
            assert "wfm_compose_ext_extra.c" not in ext, ext

    def test_jm_never_writes_it(self) -> None:
        td, _, extra = self._materialize(_with(DRAWS), True)
        with td:
            assert extra.read_text(encoding="utf-8") == "/* hand written */\n"

    def test_it_comes_after_the_types_it_may_call(self) -> None:
        """Ordering is the reason an object module needed a separate
        `_ext_prologue.c` for the one hook that goes BEFORE: everything else
        is included after the code it uses."""
        td, ext, _ = self._materialize(_with(DRAWS), True)
        with td:
            assert (
                ext.index("ComposerType =")
                < ext.index('#include "wfm_compose_ext_extra.c"')
                < ext.index("static PyMethodDef _methods[]")
            ), ext


class TestWhichTypeItAttachesTo:
    """A composer publishes four types, so the key exists; defaulting it keeps
    the common case a three-line declaration."""

    def test_it_defaults_to_the_composer_type(self) -> None:
        c = _composer.render_composer_type(_with(DRAWS), "wfm_compose")
        assert '"draws"' in c

    def test_naming_another_type_keeps_it_off_the_composer(self) -> None:
        c = _composer.render_composer_type(
            _with({**DRAWS, "type": "Synth"}), "wfm_compose"
        )
        assert "draws" not in c, c


class TestTheInertMethodsKey:
    """Measured while building this, and separable from it."""

    def test_a_composer_methods_table_is_reported(self) -> None:
        cfg = copy.deepcopy(_cfg())
        cfg["module"]["wfm_compose"]["methods"] = [{"name": "step"}]
        msgs = [str(u) for u in _keys.unknown_keys(cfg)]
        assert len(msgs) == 1, msgs
        assert "methods" in msgs[0], msgs[0]

    def test_and_names_the_kinds_it_is_valid_for(self) -> None:
        """`_keys` exists to name the face a key belongs to. Reporting without
        that is the half that trains a reader to ignore the channel."""
        cfg = copy.deepcopy(_cfg())
        cfg["module"]["wfm_compose"]["methods"] = [{"name": "step"}]
        u = _keys.unknown_keys(cfg)[0]
        assert "capsule module" in u.valid_for, u.valid_for
        assert "handle module" in u.valid_for, u.valid_for

    def test_extra_methods_itself_is_recognised(self) -> None:
        assert _keys.unknown_keys(_with(DRAWS)) == []

    @pytest.mark.parametrize(
        "key", ["name", "fn", "flags", "doc", "args", "returns", "type"]
    )
    def test_every_row_key_is_recognised(self, key: str) -> None:
        """A row key the registry does not know is accepted, preserved and
        never acted on — the shape `_keys` exists to end."""
        assert key in _keys.COMPOSER_EXTRA_METHOD_KEYS

    def test_a_typo_on_a_row_is_reported(self) -> None:
        cfg = _with({**DRAWS, "flgs": "METH_NOARGS"})
        msgs = [str(u) for u in _keys.unknown_keys(cfg)]
        assert any("flgs" in m for m in msgs), msgs
