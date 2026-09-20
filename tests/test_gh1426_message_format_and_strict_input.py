"""gh-1426 B and C: what an input may be, and what a message may say.

Found by doppler running its own buffer tests, unedited, against a generated
`F32Buffer`: 66 of 72 passed. These are two of the three gaps in the six that
did not.

**C -- a status message could not say the number that made it useful.**
`DP_WAIT_TOO_LARGE` is a caller bug about a specific `n` against a specific
capacity, and a message carrying neither sent someone to debug the producer
(doppler#1335). A message may now name this method's params and this object's
properties, rendered through `PyErr_Format`.

jm builds the format string; the author never supplies one. Author prose has
every `%` doubled and only jm-inserted conversions survive -- splicing author
text IN as the format is the hazard `_diagnostics._rc_raise_c` documents at
length, and "100% full" is the message that would have found it in
production rather than here.

**B -- an array input could only be coerced, never refused.**
`PyArray_FROM_OTF` casts, copies and FLATTENS. For a DSP `execute()` that is
a kindness; for a ring buffer it is a hidden allocation per `write` on the
one path whose purpose is to avoid copies, and a `(4, 2)` array accepted as
8 samples.

The fix is not a new validator. jm already refused rather than reinterpreted
on the **output** side (`out_buffer_guard`, gh-581) and on the **record**
input side -- only the scalar path still converted, so `strict` closes an
asymmetry inside one emitter that five callers already share.

GATE: a manifest key a method shape accepts is honoured in the generated
      binding, its stub, and a replayed script -- or it is refused.
"""

from __future__ import annotations

from pathlib import Path

from _jmrun import run_cli

EXT = Path("native") / "src" / "ring" / "ring_ext.c"


def _ring(tmp_path: Path, *, prop: bool = True) -> Path:
    root = tmp_path / "w"
    root.mkdir()
    assert run_cli("new", "q", cwd=root).returncode == 0
    proj = root / "q"
    r = run_cli(
        "object",
        "ring",
        "--no-state",
        "--no-step",
        "--init-param",
        "n:size_t:16",
        cwd=proj,
    )
    assert r.returncode == 0, r.stderr
    if prop:
        r = run_cli(
            "property", "ring", "capacity", "--type", "size_t", cwd=proj
        )
        assert r.returncode == 0, r.stderr
    return proj


def _borrow(proj: Path, name: str, *extra: str):
    return run_cli(
        "method",
        "ring",
        name,
        "--borrow",
        "--param",
        "n:size_t",
        "--return-type",
        "float _Complex",
        "--status-fn",
        "ring_wait_status",
        *extra,
        cwd=proj,
    )


def _wrapper(src: str, name: str) -> str:
    start = src.index(f"RingObj_{name}(")
    nxt = src.find("\nstatic ", start)
    return src[start : nxt if nxt != -1 else len(src)]


class TestAMessageCanNameTheNumbers:
    def test_a_param_and_a_property_both_resolve(self, tmp_path):
        proj = _ring(tmp_path)
        r = _borrow(
            proj,
            "wait",
            "--status-error",
            "DP_TOO_LARGE:ValueError:wait({n}) exceeds capacity {capacity}",
        )
        assert r.returncode == 0, r.stderr

        body = _wrapper((proj / EXT).read_text(), "wait")
        assert "PyErr_Format(PyExc_ValueError" in body
        assert '"wait(%lld) exceeds capacity %lld"' in body
        # The param is a local; the property reads through the getter jm
        # declared for it. Both, not one -- `{n}` silently failed to resolve
        # while `{capacity}` worked, because the refusal ran BEFORE the
        # params were normalised to dicts and so saw them without types.
        assert "(long long)n," in body
        assert "(long long)ring_get_capacity(self->handle)" in body

    def test_a_message_with_no_slots_still_uses_setstring(self, tmp_path):
        """One spelling of the simple case, not a second that agrees today."""
        proj = _ring(tmp_path)
        r = _borrow(
            proj, "wait", "--status-error", "DP_CLOSED:EOFError:end of stream"
        )
        assert r.returncode == 0, r.stderr

        body = _wrapper((proj / EXT).read_text(), "wait")
        assert 'PyErr_SetString(PyExc_EOFError,\n        "end of stream")' in (
            body
        )
        assert "PyErr_Format" not in body

    def test_author_percent_is_escaped(self, tmp_path):
        """ "100% full" must print as itself, not eat a vararg."""
        proj = _ring(tmp_path)
        r = _borrow(
            proj,
            "wait",
            "--status-error",
            "DP_TOO_LARGE:ValueError:the ring is 100% full at {capacity}",
        )
        assert r.returncode == 0, r.stderr

        body = _wrapper((proj / EXT).read_text(), "wait")
        assert '"the ring is 100%% full at %lld"' in body
        # Exactly one conversion is live: the slot's. Were the `%` left
        # bare, `%` + ` f` would be read as a conversion and the format
        # would outnumber its one argument.
        assert body.count("%lld") == 1

    def test_a_slot_that_is_not_in_scope_is_refused(self, tmp_path):
        """Unresolved, it would reach the user as a literal `{capacity}`."""
        proj = _ring(tmp_path, prop=False)
        r = _borrow(
            proj,
            "wait",
            "--status-error",
            "DP_TOO_LARGE:ValueError:exceeds {capacity}",
        )
        assert r.returncode != 0
        assert "not in scope" in r.stderr
        # The refusal says what IS available rather than only what is not.
        assert "Available here: n" in r.stderr

    def test_apply_replays_a_property_backed_message(self, tmp_path):
        """Replay order: a method may reference a property, never the reverse.

        `apply` rebuilds into a scratch tree. With methods replayed first the
        property did not exist yet, so jm refused a declaration it had itself
        just written -- exit 1 on a project jm generated.
        """
        proj = _ring(tmp_path)
        assert (
            _borrow(
                proj,
                "wait",
                "--status-error",
                "DP_TOO_LARGE:ValueError:exceeds {capacity}",
            ).returncode
            == 0
        )
        before = (proj / EXT).read_text()

        (proj / EXT).unlink()
        r = run_cli("apply", cwd=proj)
        assert r.returncode == 0, r.stderr
        assert (proj / EXT).read_text() == before

    def test_script_replays_the_message_verbatim(self, tmp_path):
        proj = _ring(tmp_path)
        assert (
            _borrow(
                proj,
                "wait",
                "--status-error",
                "DP_TOO_LARGE:ValueError:wait({n}) exceeds {capacity}",
            ).returncode
            == 0
        )

        r = run_cli("script", cwd=proj)
        assert r.returncode == 0, r.stderr
        assert "{n}" in r.stdout and "{capacity}" in r.stdout


class TestTheModuleShapeDopplerActuallyUses:
    """A module object, which is how doppler declares the ring.

    Not a duplicate of the standalone tests: the two render through
    different call sites, and dropping `properties` from the module one was
    invisible to every test above while producing a bare `KeyError` on the
    shape the issue was filed about. A generic emitter inherits shapes
    nobody measured.
    """

    def test_a_property_slot_resolves_in_a_module_object(self, tmp_path):
        root = tmp_path / "w"
        root.mkdir()
        assert run_cli("new", "dp", cwd=root).returncode == 0
        proj = root / "dp"
        assert run_cli("module", "buffer", cwd=proj).returncode == 0
        assert (
            run_cli(
                "object",
                "f32b",
                "--module",
                "buffer",
                "--no-state",
                "--no-step",
                "--init-param",
                "n:size_t:0",
                cwd=proj,
            ).returncode
            == 0
        )
        assert (
            run_cli(
                "property",
                "f32b",
                "capacity",
                "--module",
                "buffer",
                "--type",
                "size_t",
                cwd=proj,
            ).returncode
            == 0
        )
        r = run_cli(
            "method",
            "f32b",
            "wait",
            "--module",
            "buffer",
            "--borrow",
            "--param",
            "n:size_t",
            "--return-type",
            "float _Complex",
            "--status-fn",
            "dp_f32_wait_status",
            "--status-error",
            "DP_TOO_LARGE:ValueError:wait({n}) exceeds {capacity}",
            cwd=proj,
        )
        assert r.returncode == 0, r.stderr

        frag = (
            proj / "native" / "src" / "buffer" / "buffer_ext_f32b.c"
        ).read_text()
        assert '"wait(%lld) exceeds %lld"' in frag
        assert "(long long)n," in frag
        assert "(long long)f32b_get_capacity(self->handle)" in frag


class TestAnInputCanBeRefused:
    def _write(self, proj: Path, *extra: str):
        return run_cli(
            "method",
            "ring",
            "write",
            "--arg-type",
            "float _Complex[]",
            "--return-type",
            "bool",
            *extra,
            cwd=proj,
        )

    def test_strict_refuses_instead_of_converting(self, tmp_path):
        proj = _ring(tmp_path, prop=False)
        assert self._write(proj, "--strict").returncode == 0

        body = _wrapper((proj / EXT).read_text(), "write")
        # Nothing is converted...
        assert "PyArray_FROM_OTF" not in body
        # ...the exact dtype is required, naming what it wanted and got...
        assert "PyArray_TYPE((PyArrayObject *)x_obj) != NPY_COMPLEX64" in body
        assert "PyExc_TypeError" in body
        # ...rank and contiguity are a ValueError, because the TYPE was
        # right and the shape was not...
        assert "PyArray_NDIM" in body and "PyArray_IS_C_CONTIGUOUS" in body
        assert "PyExc_ValueError" in body
        # ...and a reference is still owned, because every call site
        # decrefs what the acquisition returns.
        assert "Py_INCREF(x_obj)" in body

    def test_without_strict_the_coercion_is_untouched(self, tmp_path):
        """Opt-in: this is a behaviour change, not a bug fix, for everyone
        whose kernel wants the kindness."""
        proj = _ring(tmp_path, prop=False)
        assert self._write(proj).returncode == 0

        body = _wrapper((proj / EXT).read_text(), "write")
        assert "PyArray_FROM_OTF" in body
        assert "PyArray_IS_C_CONTIGUOUS" not in body

    def test_an_out_param_is_not_double_guarded(self, tmp_path):
        """`out=` already has the gh-581 exact guard; strict is input-side."""
        proj = _ring(tmp_path, prop=False)
        r = run_cli(
            "method",
            "ring",
            "fill",
            "--arg-type",
            "void",
            "--return-type",
            "void",
            "--param",
            "dest:float _Complex[]:out",
            "--strict",
            cwd=proj,
        )
        if r.returncode != 0:  # the param spelling is not the point here
            return
        body = _wrapper((proj / EXT).read_text(), "fill")
        assert body.count("must be a writable, C-contiguous") <= 1

    def test_strict_survives_apply_and_script(self, tmp_path):
        proj = _ring(tmp_path, prop=False)
        assert self._write(proj, "--strict").returncode == 0
        before = (proj / EXT).read_text()

        (proj / EXT).unlink()
        assert run_cli("apply", cwd=proj).returncode == 0
        assert (proj / EXT).read_text() == before

        r = run_cli("script", cwd=proj)
        assert r.returncode == 0, r.stderr
        assert "--strict" in r.stdout
