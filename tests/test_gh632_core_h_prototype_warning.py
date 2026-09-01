"""gh-632 — a rewritten prototype in the sacred `_core.h` says so.

`_apply._refresh_core_h_decls` documented that apply "only *adds* decls".
`_init._inject_decls_into_core_h`, the function it calls, documented that a
prototype sharing a function *name* with an existing decl **replaces** it.
Both docstrings were internally coherent, they described opposite behaviours,
and the code did the second — so a hand-adjusted prototype in a sacred file
was overwritten on the next `jm apply` with nothing said.

The reporter's framing is the one taken here: `_core.h` is a hybrid — the
struct and the inline `step()` are sacred, the declarations are glue — so the
rewrite is defensible *as a policy*, and a purely additive refresh would
freeze a changed signature out of the header permanently while the generated
`_ext.c` called the new one. What is not defensible is that the policy was
documented as its opposite and applied silently: the definition in `_core.c`
and every call site still use the old prototype, so the next build fails
somewhere else entirely.

So the decision is: replace-by-name stays, both docstrings now say so, and
the rewrite warns.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._init import _inject_decls_into_core_h  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

HEADER = """\
#ifndef GAIN_CORE_H
#define GAIN_CORE_H

#ifdef __cplusplus
extern "C" {
#endif

float _Complex gain_scale(gain_state_t *state, float _Complex x);

#ifdef __cplusplus
}
#endif
#endif /* GAIN_CORE_H */
"""


def _header(tmp_path: Path, text: str = HEADER) -> Path:
    p = tmp_path / "gain_core.h"
    p.write_text(text, encoding="utf-8")
    return p


class TestTheRewriteIsAnnounced:
    def test_a_changed_prototype_warns(self, tmp_path, capsys):
        path = _header(tmp_path)
        changed = _inject_decls_into_core_h(
            path,
            "gain",
            ["float _Complex gain_scale(gain_state_t *state, double x);"],
        )
        assert changed
        err = capsys.readouterr().err
        assert "replacing the declaration of gain_scale()" in err
        assert "float _Complex x);" in err, "the old prototype must be shown"
        assert "double x);" in err, "and the new one"

    def test_the_warning_says_where_the_build_will_break(
        self, tmp_path, capsys
    ):
        """The failure is one step removed from the edit — naming that is the
        whole point, since the header now compiles fine on its own."""
        path = _header(tmp_path)
        _inject_decls_into_core_h(
            path,
            "gain",
            ["float _Complex gain_scale(gain_state_t *state, double x);"],
        )
        err = capsys.readouterr().err
        assert "_core.c" in err
        assert "call sites" in err

    def test_the_replacement_still_happens(self, tmp_path, capsys):
        """Warning is not refusing. The policy is unchanged — a stale
        prototype is glue and must reach the manifest's signature."""
        path = _header(tmp_path)
        _inject_decls_into_core_h(
            path,
            "gain",
            ["float _Complex gain_scale(gain_state_t *state, double x);"],
        )
        text = path.read_text()
        assert "double x);" in text
        assert "float _Complex x);" not in text
        assert text.count("gain_scale") == 1, "must not duplicate"


class TestItStaysQuietWhenNothingChanged:
    def test_an_identical_decl_is_silent(self, tmp_path, capsys):
        path = _header(tmp_path)
        decl = (
            "float _Complex gain_scale(gain_state_t *state, float _Complex x);"
        )
        assert _inject_decls_into_core_h(path, "gain", [decl]) is False
        assert capsys.readouterr().err == ""

    def test_a_decoratively_different_decl_is_silent(self, tmp_path, capsys):
        """gh-169: a decl equal modulo JM_RESTRICT / a dropped const is the
        same declaration. It never reached the replace branch and must not
        start warning now — that would fire on every apply for every project
        that hand-tunes a qualifier."""
        path = _header(
            tmp_path,
            HEADER.replace(
                "float _Complex gain_scale(gain_state_t *state, "
                "float _Complex x);",
                "float _Complex gain_scale(gain_state_t *const state, "
                "float _Complex x);",
            ),
        )
        before = path.read_text()
        _inject_decls_into_core_h(
            path,
            "gain",
            [
                "float _Complex gain_scale(gain_state_t *state, "
                "float _Complex x);"
            ],
        )
        assert path.read_text() == before
        assert capsys.readouterr().err == ""

    def test_a_brand_new_decl_is_silent(self, tmp_path, capsys):
        """Insertion is not replacement — nothing was overwritten."""
        path = _header(tmp_path)
        assert _inject_decls_into_core_h(
            path, "gain", ["void gain_reset(gain_state_t *state);"]
        )
        assert "replacing" not in capsys.readouterr().err

    def test_a_skipped_name_is_silent(self, tmp_path, capsys):
        """gh-761's carve-out preserves the author's decl, so there is no
        rewrite to announce."""
        path = _header(tmp_path)
        before = path.read_text()
        _inject_decls_into_core_h(
            path,
            "gain",
            ["float _Complex gain_scale(gain_state_t *state, double x);"],
            skip_names=frozenset({"gain_scale"}),
        )
        assert path.read_text() == before
        assert capsys.readouterr().err == ""


class TestTheReportedReproduction:
    """The issue's own repro, end to end: a standalone object with a declared
    method, a hand edit to its prototype, then `jm apply`."""

    def test_a_hand_edited_prototype_is_rewritten_loudly(
        self, tmp_path, capsys
    ):
        root = tmp_path / "proj"
        new_run("proj", root)
        object_run(root, "gain", None, state_vars=[("g", "double", "1.0")])
        method_run(
            root,
            "gain",
            "scale",
            None,
            "float _Complex",
            "float _Complex",
            False,
            [],
        )
        header = root / "native" / "inc" / "gain" / "gain_core.h"
        original = header.read_text()

        # Only the *declaration* line — the inline step() definition in the
        # same header is sacred and must not be touched by the fixture, or
        # the assertion below would be reading the wrong edit. (A first draft
        # did exactly that and failed for that reason, not the real one.)
        proto = next(
            ln
            for ln in original.splitlines()
            if "gain_scale" in ln and ln.rstrip().endswith(");")
        )
        hand = proto.replace("float _Complex x)", "const float _Complex x_in)")
        assert hand != proto, "the hand edit must change the prototype"
        header.write_text(original.replace(proto, hand, 1))

        capsys.readouterr()
        apply_run(root)
        out = capsys.readouterr()

        after = header.read_text()
        assert hand not in after, (
            "policy unchanged: the prototype is glue and is refreshed"
        )
        assert proto in after, "and it is refreshed back to the manifest's"
        assert "replacing the declaration of gain_scale()" in out.err, (
            "and it is no longer silent — this is the whole issue"
        )
