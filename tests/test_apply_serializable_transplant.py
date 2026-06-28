"""gh-404: ``jm apply`` transplants the serializable state triplet into a
sacred per-object binding fragment.

A `serializable` object keeps its binding in a hand-owned `<mod>_ext_<obj>.c`
fragment that `_sync_aggregates` never regenerates.  When the flag is set after
the fragment already exists (or the fragment was hand-written), the
`state_bytes`/`get_state`/`set_state` wrappers + `PyMethodDef` rows are missing.
The post-sync `_docsync` pass injects them — idempotently, leaving every
hand-written binding untouched.  No C compiler needed — assert on the tree.
"""

import contextlib
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _docsync as D  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _scaffold(dest: Path, module="sig"):
    """A module with one stateful object, fragment created WITHOUT the flag."""
    _silent(new_run, "dsp", dest)
    _silent(module_run, dest, module)
    _silent(
        object_run,
        dest,
        "mix",
        module=module,
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )


def _frag(dest: Path, module="sig", obj="mix") -> Path:
    return dest / "native" / "src" / module / f"{module}_ext_{obj}.c"


def _make_serializable(dest: Path, obj="mix"):
    cfg = C.load(dest)
    cfg[obj]["serializable"] = "true"
    C.save(dest, cfg)


def test_apply_injects_triplet_into_sacred_fragment(tmp_path):
    _scaffold(tmp_path)
    _silent(apply_run, tmp_path)
    frag = _frag(tmp_path)
    assert frag.exists()
    assert "state_bytes" not in frag.read_text()  # sacred, no triplet yet

    _make_serializable(tmp_path)
    _silent(apply_run, tmp_path)

    text = frag.read_text()
    # C wrappers calling the hand-written core triplet.
    assert "mix_state_bytes(self->handle)" in text
    assert "mix_get_state(self->handle, PyBytes_AS_STRING(_b))" in text
    assert "mix_set_state(self->handle, PyBytes_AS_STRING(arg))" in text
    # PyMethodDef rows, inside the methods array (before the sentinel).
    assert '"state_bytes"' in text
    assert '"get_state"' in text
    assert '"set_state"' in text
    idx = text.index("PyMethodDef")
    sentinel = re.search(r"\{\s*NULL", text[idx:]).start() + idx
    assert text.index('"state_bytes"', idx) < sentinel


def test_transplant_is_idempotent(tmp_path):
    _scaffold(tmp_path)
    _silent(apply_run, tmp_path)
    _make_serializable(tmp_path)
    _silent(apply_run, tmp_path)
    once = _frag(tmp_path).read_text()
    _silent(apply_run, tmp_path)
    twice = _frag(tmp_path).read_text()
    assert once == twice
    # exactly one triplet, not stacked copies (count the wrapper definition).
    assert once.count("Mix_state_bytes(MixObject") == 1


def test_transplant_preserves_hand_binding(tmp_path):
    _scaffold(tmp_path)
    _silent(apply_run, tmp_path)
    frag = _frag(tmp_path)
    # A hand-written, non-manifest method row + stub the manifest can't express.
    orig = frag.read_text()
    t = orig.replace(
        "{NULL}",
        '{"secret", (PyCFunction)Mix_secret, METH_NOARGS, "hand"},\n  {NULL}',
        1,
    )
    assert t != orig  # the replace landed
    frag.write_text(t)
    _make_serializable(tmp_path)
    _silent(apply_run, tmp_path)
    out = frag.read_text()
    assert '"secret"' in out  # hand binding survives
    assert '"state_bytes"' in out  # triplet injected alongside


def test_unit_transplant_skips_when_present(tmp_path):
    # Direct unit check of the splicer: a fragment already carrying the triplet
    # is returned unchanged.
    existing = (
        "static PyObject *\nMix_state_bytes(MixObject *self, PyObject *a)\n"
        "{ return NULL; }\n\n"
        "static PyMethodDef MixObj_methods[] = {\n"
        '  { "state_bytes", (PyCFunction)Mix_state_bytes, METH_NOARGS, "x" },\n'
        "  { NULL, NULL, 0, NULL }\n};\n"
    )
    out = D.transplant_state_triplet(existing, ["JUNK_FUNC"], "JUNK_ROW\n")
    assert out == existing
    assert "JUNK" not in out
