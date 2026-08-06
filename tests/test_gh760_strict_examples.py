"""gh-760 — an overlong authored `@code` line can fail the gate, opt-in.

gh-752 gave `jm status --check` a burn-down count of authored example lines
too wide for their generated stub. Printing a count is right while a project
is sweeping; it is not enough afterwards. doppler will have spent ~15
header-editing PRs getting theirs to zero, and nothing stopped the 116th line
from being added — so the sweep has to be re-run periodically forever.

The enforcement can only live in jm. A formatter cannot do it: fed a real
doctest at a 79-column limit, clang-format reflows the block as prose,
orphaning a trailing comment onto its own line where doctest reads it as
expected output, and swallowing the real expected value onto a continuation.
It reports success while destroying both examples. So this has to be a
*checker*, never a fixer. A downstream `.pyi` lint fires one transform too
late, points at a generated file the author must not edit, and cannot state
the budget — which is per-destination-indent and jm-internal.

Off by default, so no consumer goes red mid-sweep.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _status  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

# A doctest line wide enough to overflow once indented into the stub. The
# figure is measured, not guessed: this renders at 102 columns against a
# budget of 71.
_WIDE = ">>> value = thing.get_gain()" + " " * 44 + "# note pushing it over"


def _project(tmp_path) -> Path:
    root = tmp_path / "proj"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("proj", root)
        object_run(root, "thing", None, state_vars=[("gain", "double", "1.0")])
    return root


def _widen(root: Path) -> None:
    """Put an overlong authored @code line into the sacred header's docs.

    Two things this has to get right, both of which a first draft got wrong
    and so measured zero overflows while asserting on one:

    * the block must attach to a **documented symbol** whose doc actually
      reaches the stub, not sit loose at the top of the file;
    * `apply` must run afterwards. `_codecheck.scan` reads the **real**
      tree's `.pyi`, and `jm status` regenerates only into its scratch — so
      without this the stub never contains the line at all.
    """
    from just_makeit._apply import run as apply_run

    h = root / "native" / "inc" / "thing" / "thing_core.h"
    text = h.read_text()
    anchor = (
        " * @brief Get current gain.\n * @param state  Must be non-NULL.\n"
    )
    assert anchor in text, "fixture needs the accessor's doc block"
    h.write_text(
        text.replace(
            anchor, anchor + " *\n * @code\n * " + _WIDE + "\n * @endcode\n", 1
        )
    )
    with contextlib.redirect_stdout(io.StringIO()):
        apply_run(root)


def _status_out(root: Path, **kw) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = _status.run(root, check=True, **kw)
    return rc, buf.getvalue()


def _set(root: Path, value: str) -> None:
    p = root / C.FILENAME
    p.write_text(
        p.read_text().replace(
            "[project]", f'[project]\nstrict_examples = "{value}"', 1
        )
    )


class TestThePredicate:
    def test_off_by_default(self):
        assert C.strict_examples({"project": {}}) is False
        assert C.strict_examples({}) is False

    def test_both_spellings_are_accepted(self):
        """tomllib gives True for `key = true`; jm writes the string form."""
        assert C.strict_examples({"project": {"strict_examples": True}})
        assert C.strict_examples({"project": {"strict_examples": "true"}})

    def test_false_is_false(self):
        assert not C.strict_examples({"project": {"strict_examples": "false"}})
        assert not C.strict_examples({"project": {"strict_examples": False}})


class TestTheGate:
    def test_the_count_alone_does_not_fail(self, tmp_path):
        """Today's behaviour, and it must survive: a project mid-sweep keeps
        a green gate while it burns the number down."""
        root = _project(tmp_path)
        _widen(root)
        rc, out = _status_out(root)
        assert "exceed 79 columns" in out, "fixture must produce an overflow"
        assert rc == 0, "the count is informational unless strict is on"
        assert "Not drift" in out

    def test_strict_config_fails(self, tmp_path):
        root = _project(tmp_path)
        _widen(root)
        _set(root, "true")
        rc, out = _status_out(root)
        assert rc != 0, "strict_examples must make the count a gate"
        assert "strict_examples is on" in out

    def test_the_flag_fails_without_the_config(self, tmp_path):
        """The one-off form, for a project that has not committed to it."""
        root = _project(tmp_path)
        _widen(root)
        assert _status_out(root, strict_examples=True)[0] != 0

    def test_a_clean_project_passes_under_strict(self, tmp_path):
        """Guard: strict must gate on overflow, not on being enabled."""
        root = _project(tmp_path)
        _set(root, "true")
        rc, out = _status_out(root)
        assert rc == 0, out
        assert "exceed 79 columns" not in out


class TestTheJsonPathAgrees:
    """A gate that fires for a human and not for `--json` is the shape a CI
    consumer discovers the hard way — `--json` returns before the text
    rendering, so the count has to be folded in above both."""

    def test_json_returns_the_same_code(self, tmp_path):
        root = _project(tmp_path)
        _widen(root)
        _set(root, "true")
        rc_text, _ = _status_out(root)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_json = _status.run(root, check=True, as_json=True)
        assert rc_json == rc_text != 0

    def test_json_is_clean_when_strict_is_off(self, tmp_path):
        root = _project(tmp_path)
        _widen(root)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert _status.run(root, check=True, as_json=True) == 0


class TestApplyStillApplies:
    """The point is to gate the commit, not to block regeneration — a
    half-swept repo still needs to be able to run `apply`."""

    def test_apply_succeeds_with_an_overlong_line(self, tmp_path):
        from just_makeit._apply import run as apply_run

        root = _project(tmp_path)
        _widen(root)
        _set(root, "true")
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(root)  # must not raise or exit
