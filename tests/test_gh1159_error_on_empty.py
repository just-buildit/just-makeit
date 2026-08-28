"""gh-1159: a `variable_output` method can declare that empty means REFUSED.

A `variable_output` method returns `size_t` -- the count it wrote -- and jm
turns that into an array of that length. Its one return value IS the length,
so unlike every other shape it has no status left to carry: `status_return`
and `error_negative` both need a code, and there is none.

So a kernel that validated its input and returned 0 produced a well-formed
`array([], dtype=...)` and the caller carried on. For a framing object that is
the worst available answer -- doppler's block interleaver refuses a partial
block, because padding changes the length and a receiver de-interleaving the
padded block recovers different bits. The frame came back short, nothing
raised, and it surfaced far away as a bad decode.

`error_on_empty = true` is the sibling of `none_on_empty`, which reads the
same zero the opposite way ("nothing to report" -> `None`). Declaring both is
refused: they are contradictory readings of one value, and both compile.

Two properties carry this file:

1. **Every call path.** A `variable_output` method has two -- the `out=`
   buffer and the allocate path -- and they do not even name the output array
   the same way (`out_arr` / `arr0`). That is precisely why this is generated:
   hand-written, doppler needed six insertions, and copying one path's block
   into the other is a compile error if you are lucky and a leak if you are
   not.
2. **The docs say what the binding does.** The raise renders from
   `declared_raise`, the same pair both doc faces read, so a `.pyi` cannot
   document an exception the C does not raise (gh-869). The prose is
   `error_on_empty`'s own: `status_return`'s sentence -- "returns a non-zero
   status ... with the return code appended" -- is wrong twice here, since the
   value checked is a length and the code is zero by construction.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"

_NO_TOOLCHAIN = shutil.which("cmake") is None or (
    shutil.which("cc") is None and shutil.which("gcc") is None
)

MESSAGE = "length is not a whole number of blocks"


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from just_makeit._cli import main; main()",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"},
    )


def _scaffold(tmp_path: Path, *, extra: str) -> Path:
    """A project with one `variable_output` method, plus *extra* manifest."""
    assert _cli("new", "d", cwd=tmp_path).returncode == 0
    root = tmp_path / "d"
    assert (
        _cli(
            "object",
            "o",
            "--arg-type",
            "uint8_t[]",
            "--return-type",
            "uint8_t[]",
            cwd=root,
        ).returncode
        == 0
    )
    assert (
        _cli(
            "method",
            "o",
            "interleave",
            "--arg-type",
            "uint8_t[]",
            "--return-type",
            "size_t",
            "--variable-output",
            cwd=root,
        ).returncode
        == 0
    )
    p = root / "objects" / "o.toml"
    body = p.read_text(encoding="utf-8")
    assert "variable_output = true" in body, body
    p.write_text(
        body.replace(
            "variable_output = true", "variable_output = true\n" + extra, 1
        ),
        encoding="utf-8",
    )
    assert _cli("apply", cwd=root).returncode == 0
    return root


@pytest.fixture
def refusing(tmp_path: Path) -> Path:
    return _scaffold(
        tmp_path,
        extra=(
            "error_on_empty = true\n"
            'error = "ValueError"\n'
            f'error_message = "{MESSAGE}"\n'
        ),
    )


def _ext(root: Path) -> str:
    return (root / "native" / "src" / "o" / "o_ext.c").read_text("utf-8")


def _pyi(root: Path) -> str:
    return (root / "src" / "d" / "o.pyi").read_text(encoding="utf-8")


class TestEveryCallPath:
    def test_both_paths_raise(self, refusing: Path) -> None:
        """One insertion per path is the whole point. Counting them is what
        catches a fix that lands on the path the author happened to test."""
        ext = _ext(refusing)
        assert ext.count("PyErr_SetString(PyExc_ValueError,") == 2, ext
        assert ext.count(MESSAGE) >= 2

    def test_each_path_releases_its_own_array(self, refusing: Path) -> None:
        """The two paths hold DIFFERENT objects, which is exactly what makes
        the hand-written version leak: `out_arr` in the `out=` path, `arr0` in
        the allocate path. Copying one block into the other is a compile error
        if you are lucky and a leak if you are not."""
        ext = _ext(refusing)
        assert "Py_DECREF(out_arr); PyErr_SetString" in ext, ext
        assert "Py_DECREF(arr0); PyErr_SetString" in ext, ext

    def test_no_empty_array_is_returned(self, refusing: Path) -> None:
        """The bug: a zero count became a well-formed empty result."""
        ext = _ext(refusing)
        assert "if (!n_out) { " not in ext or "Py_RETURN_NONE" not in ext


class TestBothDocFaces:
    def test_the_exception_is_documented_on_both(self, refusing: Path) -> None:
        for face in (_ext(refusing), _pyi(refusing)):
            assert "Raises" in face
            assert "ValueError" in face
            assert MESSAGE in face

    def test_the_prose_is_a_refusal_not_a_status(self, refusing: Path) -> None:
        """`status_return`'s sentence is wrong twice here: the value checked
        is a LENGTH, and there is no return code to append."""
        for face in (_ext(refusing), _pyi(refusing)):
            assert "REFUSAL rather than an empty answer" in face
            assert "non-zero status" not in face
            assert "return code appended" not in face


class TestItRefusesContradictions:
    """Both spellings compile, so both are refused at declaration time."""

    @staticmethod
    def _run(**kw):
        from just_makeit import _method

        base = dict(
            root=Path("."),
            object_name="o",
            method_name="m",
            module=None,
            arg_type="uint8_t[]",
            return_type="size_t",
            variable_output=True,
            multi_output=[],
        )
        return _method.run(**{**base, **kw})

    def test_none_on_empty_and_error_on_empty_are_contradictory(self) -> None:
        """One says an empty result is a normal answer and returns None; the
        other says the kernel refused and raises. Reading the same zero two
        opposite ways is not a configuration, it is a mistake."""
        with pytest.raises(SystemExit):
            self._run(none_on_empty=True, error_on_empty=True)

    def test_it_refuses_a_non_variable_output_method(self) -> None:
        """Such a method HAS a status to carry, and two better keys for it --
        `status_return` and `error_negative`. The message says so."""
        with pytest.raises(SystemExit):
            self._run(variable_output=False, error_on_empty=True)

    def test_error_and_error_message_are_licensed_by_it(self) -> None:
        """`error` / `error_message` needed `status_return` or
        `error_negative`. Left unwidened, they would be refused on the one
        shape whose whole purpose is explaining a refusal."""
        import io
        from contextlib import redirect_stderr

        buf = io.StringIO()
        with redirect_stderr(buf), pytest.raises(SystemExit):
            self._run(error="ValueError", error_on_empty=False)
        assert "error_on_empty" in buf.getvalue()


class TestPlumbing:
    def test_the_key_survives_a_manifest_round_trip(
        self, refusing: Path
    ) -> None:
        """`_apply._replay_method` enumerates method keys one by one, so an
        unforwarded key is silently absent — the shape that dropped
        `record_dtype` and made apply rewrite a sacred header."""
        from just_makeit import _config as C

        cfg = C.load(refusing)
        m = next(m for m in cfg["o"]["methods"] if m["name"] == "interleave")
        assert m.get("error_on_empty") is True

        assert _cli("apply", cwd=refusing).returncode == 0
        assert "REFUSAL rather than an empty answer" in _ext(refusing)

    def test_status_check_is_clean(self, refusing: Path) -> None:
        out = _cli("status", "--check", cwd=refusing)
        assert out.returncode == 0, out.stdout

    def test_an_undeclared_method_is_unchanged(self, tmp_path: Path) -> None:
        """Zero churn: without the key, not a word of the binding moves."""
        plain = _scaffold(tmp_path, extra="")
        ext = _ext(plain)
        assert "PyErr_SetString(PyExc_ValueError," not in ext
        assert "REFUSAL" not in ext


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
class TestItCompiles:
    """Text assertions cannot answer this one.

    The generated block sits between a kernel call and a return, holds a
    borrowed reference, and is spliced into two differently-shaped wrappers.
    Every way of getting it wrong produces C -- and one of them produces C
    that compiles and leaks.
    """

    def test_the_generated_binding_builds(self, refusing: Path) -> None:
        out = _cli("test", cwd=refusing)
        assert out.returncode == 0, out.stdout
