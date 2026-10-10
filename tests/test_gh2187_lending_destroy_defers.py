"""A lending object's destroy() defers the free to tp_dealloc (gh-2187).

Three rows hand out views into an object's own memory -- a ``borrow = true``
method, a ``buf_field`` property, an array state's ``get_<name>_view()`` --
and each pins ``self`` as the view's base. The pin keeps the *object* alive
while a view is; it never kept the *memory*. An explicit ``destroy()`` or
``__exit__`` called the C destructor at once, so every outstanding view, and
a GIL-free call still running on another thread, read freed memory. doppler's
``F32Buffer`` reported it (doppler-dsp/doppler#2016 item 4).

The fix moves the free, not the views: a lending object's teardown parks the
state in ``_jm_parked`` and nulls the handle (so every method refuses through
its existing guard), and ``tp_dealloc`` frees whichever of the two is set --
which runs only once the last view and in-flight call have let go of self.
Export counts were rejected on gh-1312 and stay rejected (see `_borrow`).

Two declarations come with it. A fallible destroy (``returns = "int"``) on a
lending object is refused: tp_dealloc has no caller to report a status to,
and the finalizer `exit` (gh-805 §H) is where one belongs. ``wake`` names a
declared method the teardown calls first, so a blocking call holding the
object returns instead of waiting on a parked state for ever.
"""

from __future__ import annotations

import ast
import contextlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _jminc import INC_ROOT  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._context._destroy import make_destroy_ctx  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_CLOSE = {
    "name": "close",
    "arg_type": "void",
    "return_type": "void",
    "fn": "ring_close",
}


def _ctx(spec=None, methods=None, lends=True):
    return make_destroy_ctx(
        "ring",
        "RingObj",
        spec or {},
        methods if methods is not None else [_CLOSE],
        csym="ring",
        lends=lends,
    )


class TestTheTeardownParks:
    def test_the_body_parks_and_never_frees(self):
        body = _ctx()["destroy_method_body"]
        assert "self->_jm_parked = self->handle;" in body
        assert "self->handle = NULL;" in body
        assert "ring_destroy" not in body, body

    def test_exit_parks_too(self):
        assert _ctx()["destroy_exit_body"] == _ctx()["destroy_method_body"]

    def test_dealloc_frees_whichever_is_set(self):
        dealloc = _ctx()["destroy_dealloc_call"]
        assert "ring_destroy(self->handle);" in dealloc
        assert "ring_destroy(self->_jm_parked);" in dealloc

    def test_the_struct_gains_the_park(self):
        assert "ring_state_t *_jm_parked;" in _ctx()["destroy_fields"]

    def test_a_non_lending_object_is_unchanged(self):
        ctx = _ctx(lends=False)
        assert ctx["destroy_fields"] == ""
        assert "_jm_parked" not in ctx["destroy_method_body"]
        assert "_jm_parked" not in ctx["destroy_dealloc_call"]

    def test_both_faces_say_when_the_memory_goes(self):
        ctx = _ctx()
        assert "gh-2187" in ctx["pyi_destroy_methods"]
        assert "immediately" not in ctx["pyi_destroy_methods"]
        assert "immediately" in _ctx(lends=False)["pyi_destroy_methods"]


class TestTheWake:
    def test_it_is_called_before_the_park(self):
        body = _ctx({"wake": "close"})["destroy_method_body"]
        assert body.index("ring_close(self->handle);") < body.index(
            "self->_jm_parked = self->handle;"
        )

    def test_an_unknown_method_is_refused(self):
        with pytest.raises(ValueError, match="not a declared method"):
            _ctx({"wake": "flush"})

    def test_a_method_taking_arguments_is_refused(self):
        m = dict(_CLOSE, params=[{"name": "n", "type": "size_t"}])
        with pytest.raises(ValueError, match="state alone"):
            _ctx({"wake": "close"}, [m])

    def test_a_wake_needs_a_deferred_free(self):
        with pytest.raises(ValueError, match="lends no views"):
            _ctx({"wake": "close"}, lends=False)


class TestTheRefusal:
    def test_a_fallible_destroy_cannot_be_deferred(self):
        with pytest.raises(ValueError, match="exit"):
            _ctx({"returns": "int"})


class TestEveryCallerAsksTheManifest:
    """gh-856's lesson, for this argument: `lends` is required, so a call
    site that forgets it fails at once. What a required argument cannot
    catch is a site that passes a literal `False` while the manifest is in
    hand -- the object would silently free under its views on that path."""

    @staticmethod
    def _sites():
        root = Path(__file__).parent.parent / "src" / "just_makeit"
        for path in sorted(root.rglob("*.py")):
            src = path.read_text(encoding="utf-8")
            if "make_destroy_ctx(" not in src:
                continue
            for node in ast.walk(ast.parse(src)):
                fn = getattr(node, "func", None)
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if isinstance(node, ast.Call) and name == "make_destroy_ctx":
                    yield path, node, ast.get_source_segment(src, node) or ""

    def test_the_sweep_is_armed(self):
        assert len(list(self._sites())) >= 6

    def test_a_site_that_reads_the_manifest_asks_it(self):
        wrong = []
        for path, node, text in self._sites():
            kw = {k.arg: k.value for k in node.keywords}
            assert "lends" in kw, f"{path.name}:{node.lineno}"
            literal = isinstance(kw["lends"], ast.Constant)
            if "cfg" in text and literal:
                wrong.append(f"{path.name}:{node.lineno}")
        assert not wrong, (
            "make_destroy_ctx passed a literal `lends` where the manifest "
            "is in hand; a lending object would free under its views on "
            "that path: " + "; ".join(wrong)
        )


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


@pytest.mark.skipif(not shutil.which("cmake"), reason="cmake not found")
class TestItActuallyDefers:
    """Built and run. A string match cannot see a use-after-free; the C
    destructor here POISONS the buffer and reports itself on stderr before
    it frees, so reading a view after destroy() shows the poison, and the
    order of the markers shows when the free really happened."""

    KERNEL = """    if (n > 8) return NULL;
    for (size_t i = 0; i < n; i++)
        state->buf[i] = (float)i + 0.0f * I;
    return state->buf;"""

    @classmethod
    def _built(cls, root: Path):
        _silent(new_run, "p", root, c_prefix=None)
        _silent(
            object_run, root, "ring", None, state_vars=[("cap", "size_t", "8")]
        )
        _silent(
            method_run,
            root,
            "ring",
            "wait",
            None,
            "void",
            "float _Complex",
            False,
            [],
            params=[("n", "size_t")],
            borrow=True,
        )
        h = root / INC_ROOT / "ring/ring_core.h"
        h.write_text(
            h.read_text().replace(
                "    size_t cap;",
                "    size_t cap;\n    float _Complex buf[8];",
                1,
            )
        )
        c = root / "native/src/ring/ring_core.c"
        text = c.read_text()
        i = text.index("ring_wait(ring_state_t *state, size_t n)")
        j = text.index("\n}\n", i)
        body = text.index("{", i) + 1
        text = text[:body] + "\n" + cls.KERNEL + text[j:]
        k = text.index("ring_destroy(ring_state_t *state)")
        body = text.index("{", k) + 1
        text = (
            text[:body] + "\n    if (state) {\n"
            "        memset(state->buf, 0x7f, sizeof state->buf);\n"
            '        fputs("FREED\\n", stderr);\n'
            "    }" + text[body:]
        )
        if "#include <string.h>" not in text:
            text = "#include <string.h>\n#include <stdio.h>\n" + text
        c.write_text(text)
        for cmd in (
            ["cmake", "-S", str(root), "-B", str(root / "build")],
            ["cmake", "--build", str(root / "build"), "--target", "ring"],
        ):
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=900
            )
            assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]

    def _probe(self, root: Path, code: str):
        probe = root / "probe.py"
        probe.write_text(code)
        return subprocess.run(
            [sys.executable, str(probe)],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(root),
            env={**os.environ, "PYTHONPATH": str(root / "src")},
        )

    def test_a_view_outlives_destroy_and_the_free_waits(self, tmp_path):
        root = tmp_path / "p"
        self._built(root)
        out = self._probe(
            root,
            "import gc, sys\n"
            "from p.ring import Ring\n"
            "r = Ring(cap=8)\n"
            "v = r.wait(4)\n"
            "r.destroy()\n"
            "sys.stderr.write('DESTROYED\\n'); sys.stderr.flush()\n"
            # the view still reads what the kernel wrote, not the poison
            "assert [complex(z) for z in v[1:4]] == [1, 2, 3], list(v)\n"
            # every method refuses through the existing guard
            "try:\n"
            "    r.wait(1)\n"
            "    raise SystemExit('a method ran after destroy()')\n"
            "except RuntimeError:\n"
            "    pass\n"
            "r.destroy()  # idempotent\n"
            "del v, r\n"
            "gc.collect()\n"
            "sys.stderr.write('COLLECTED\\n'); sys.stderr.flush()\n"
            "print('OK')\n",
        )
        assert out.returncode == 0, out.stdout + out.stderr
        assert "OK" in out.stdout, out.stdout
        order = [ln for ln in out.stderr.split() if ln.isupper()]
        assert order == ["DESTROYED", "FREED", "COLLECTED"], out.stderr

    def test_an_exception_in_a_with_block_propagates(self, tmp_path):
        """An __exit__ that refused to close while a view was alive would
        replace this exception -- the reason export counts were rejected."""
        root = tmp_path / "p"
        self._built(root)
        out = self._probe(
            root,
            "from p.ring import Ring\n"
            "try:\n"
            "    with Ring(cap=8) as r:\n"
            "        v = r.wait(2)\n"
            "        raise KeyError('mine')\n"
            "except KeyError as e:\n"
            "    assert e.args == ('mine',), e.args\n"
            "    assert [complex(z) for z in v] == [0, 1], list(v)\n"
            "    print('OK')\n",
        )
        assert out.returncode == 0, out.stdout + out.stderr
        assert "OK" in out.stdout, out.stdout
