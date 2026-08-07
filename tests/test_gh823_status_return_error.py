"""gh-823 Ask D: `status_return` can name its exception and carry a message.

Three mechanisms turn a failing C int into a Python exception — `[<obj>.destroy]`
`returns`+`error` (gh-541), `error_negative`+`error` (gh-805 §B), and
`status_return` (gh-432). The first two accept a category and a message; the
third always raised `ValueError: <name> failed (rc=%d)`.

That asymmetry was not a missing key. `error` and `error_message` were already
read from the method dict, generically, for every method — only
`error_negative`'s emitter looked at them, and a validation gate turned the
combination into a refusal rather than a silent no-op. So the reporter's
`Capture.close()`, whose entire purpose is to explain *which* contract the
caller broke, could say nothing, while the same verdict reached through the
destructor — which can carry a message — explained itself.

The fix is a single raise emitter both paths call, not a second copy of the
good one: two implementations of "turn a bad rc into an exception" is how the
asymmetry arose in the first place.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._method import run as method_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._script import run as script_run


def _project(tmp_path: Path, **method_kw) -> Path:
    root = tmp_path / "demo"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("demo", root)
        object_run(
            root,
            "cap",
            None,
            state_vars=[("n", "size_t", "0")],
            arg_type="float",
            return_type="float",
        )
        method_run(
            root, "cap", "close", None, "void", "int", False, [], **method_kw
        )
    return root


def _wrapper(root: Path) -> str:
    text = (root / "native" / "src" / "cap" / "cap_ext.c").read_text()
    i = text.index("Cap_close(")
    return text[i : text.index("\n}", i)]


class TestItCanNameTheException:
    def test_the_declared_category_reaches_the_c(self, tmp_path):
        root = _project(tmp_path, status_return=True, error="RuntimeError")
        assert "PyExc_RuntimeError" in _wrapper(root)

    def test_the_declared_message_reaches_the_c(self, tmp_path):
        root = _project(
            tmp_path,
            status_return=True,
            error_message="records were dropped",
        )
        assert "records were dropped" in _wrapper(root)

    def test_a_message_needs_no_category(self, tmp_path):
        """Additive on both axes — either key alone is legal."""
        root = _project(
            tmp_path, status_return=True, error_message="be specific"
        )
        w = _wrapper(root)
        assert "be specific" in w
        assert "PyExc_ValueError" in w, "the category default still applies"


class TestZeroChurn:
    def test_an_undeclared_method_still_says_the_same_thing(self, tmp_path):
        """The canned string stays the default. `"<name> failed (rc=%d)"` and
        `"%s (rc=%lld)"` + `"<name> failed"` render the same text at runtime,
        so no existing project's message moves."""
        w = _wrapper(_project(tmp_path, status_return=True))
        assert "PyExc_ValueError" in w
        assert "close failed" in w
        assert "(rc=%lld)" in w


class TestTheMessageIsAnArgumentNotTheFormat:
    """The property that makes author prose safe to accept at all."""

    def test_a_percent_in_prose_is_not_a_conversion(self, tmp_path):
        root = _project(
            tmp_path,
            status_return=True,
            error_message="100% of the block bound was exceeded",
        )
        w = _wrapper(root)
        # The fixed format carries exactly one %s and one %lld; the author's
        # `%` sits inside a separate string literal argument.
        assert '"%s (rc=%lld)"' in w
        assert "100% of the block bound" in w

    def test_the_format_string_is_never_interpolated(self, tmp_path):
        """A method name spliced into the format was the old shape."""
        w = _wrapper(_project(tmp_path, status_return=True))
        assert '"close failed (rc=' not in w


class TestOneEmitterNotTwo:
    """Both int-to-exception paths must render the same raise."""

    def test_status_return_and_error_negative_agree(self, tmp_path):
        a = _wrapper(
            _project(tmp_path / "a", status_return=True, error="KeyError")
        )
        b = _wrapper(
            _project(tmp_path / "b", error_negative=True, error="KeyError")
        )
        for frag in ('PyExc_KeyError, "%s (rc=%lld)"', "(long long)_rc"):
            assert frag in a, frag
            assert frag in b, frag


class TestTheGateStillRejectsAnOrphanedKey:
    def test_error_alone_is_refused(self, tmp_path):
        with pytest.raises(SystemExit):
            _project(tmp_path, error="RuntimeError")

    def test_error_message_alone_is_refused(self, tmp_path):
        """Previously unchecked: `error_message` without a raising mechanism
        was accepted and silently ignored."""
        with pytest.raises(SystemExit):
            _project(tmp_path, error_message="nowhere to go")


class TestScriptReplaysIt:
    def test_status_return_is_emitted(self, tmp_path, capsys):
        """It was forwarded by `_apply` and emitted by nobody, so a replayed
        project lost it — gh-808's shape. Load-bearing now that `--error` may
        accompany it."""
        root = _project(
            tmp_path,
            status_return=True,
            error="RuntimeError",
            error_message="dropped",
        )
        with contextlib.redirect_stdout(io.StringIO()) as out:
            script_run(root)
        script = out.getvalue()
        assert "--status-return" in script
        assert "--error RuntimeError" in script or "--error" in script
        assert "dropped" in script
