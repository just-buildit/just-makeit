"""`jm apply` refreshes per-object binding-fragment docstrings (runtime
__doc__) while preserving hand-written C bodies AND hand-written non-manifest
bindings.

Module objects keep their PyMethodDef / tp_doc / PyGetSetDef docs in
<mod>_ext_<obj>.c fragments, which _sync_aggregates does NOT reconcile. The
post-sync _docsync pass transplants *only* the doc-string slots into the
existing fragment: docstrings refresh, hand bodies survive, hand-written
bindings the manifest can't express survive, *_extra.c is untouched,
idempotent.
"""

import io
import re
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit import _docsync as D  # noqa: E402


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _annotate(dest: Path, obj: str, c_func: str, brief: str):
    """Put `@brief <brief>` on the *c_func* decl in <obj>_core.h."""
    header = dest / "native" / "inc" / obj / f"{obj}_core.h"
    text = header.read_text(encoding="utf-8")
    block = f"  /**\n   * @brief {brief}\n   */\n"
    decl_re = re.compile(
        r"(?:^[ \t]*/\*\*(?:(?!\*/)[\s\S])*?\*/[ \t]*\r?\n)?"
        r"(^(?![ \t]*[*/])[^\n=]*\b" + c_func + r"\s*\([^;]*\);)",
        re.MULTILINE,
    )
    text2 = decl_re.sub(block + r"\1", text, count=1)
    assert text2 != text, f"could not locate {c_func}"
    header.write_text(text2, encoding="utf-8")


def _mark_body(frag: Path, wrapper: str, marker: str):
    """Insert *marker* as the first line of the *wrapper* C function body."""
    t = frag.read_text(encoding="utf-8")
    t2 = re.sub(
        r"(\b" + wrapper + r"\([^\)]*\)\s*\n\{\n)",
        r"\1    " + marker + "\n",
        t,
        count=1,
    )
    assert marker in t2, f"could not mark {wrapper} body"
    frag.write_text(t2, encoding="utf-8")


def _scaffold(dest: Path, module="sig"):
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
    _silent(
        method_run, dest, "mix", "scale", module, "float", "float", False, []
    )


def _frag(dest: Path, module: str, obj: str) -> Path:
    return dest / "native" / "src" / module / f"{module}_ext_{obj}.c"


# ── (a) docstrings reach the runtime binding fragment ────────────────────────
def test_apply_refreshes_method_class_and_property_docs(tmp_path):
    dest = tmp_path / "dsp"
    _scaffold(dest)
    _silent(property_run, dest, "mix", "level", "sig", "float", False)
    _annotate(dest, "mix", "mix_scale", "Scale the input sample.")
    _annotate(dest, "mix", "mix_create", "A unity-gain scaler.")
    _annotate(dest, "mix", "mix_get_level", "Output level in dBFS.")
    _silent(apply_run, dest)
    out = _frag(dest, "sig", "mix").read_text(encoding="utf-8")
    assert "Scale the input sample." in out  # PyMethodDef
    assert "A unity-gain scaler." in out  # tp_doc
    assert "Output level in dBFS." in out  # PyGetSetDef doc


# ── (b) hand-edited wrapper body survives the refresh ────────────────────────
def test_apply_preserves_hand_body_while_refreshing_doc(tmp_path):
    dest = tmp_path / "dsp"
    _scaffold(dest)
    _mark_body(_frag(dest, "sig", "mix"), "Mix_scale", "/* HAND-BODY */")
    _annotate(dest, "mix", "mix_scale", "Scale the input sample.")
    _silent(apply_run, dest)
    out = _frag(dest, "sig", "mix").read_text(encoding="utf-8")
    assert "/* HAND-BODY */" in out
    assert "Scale the input sample." in out


# ── (c) THE regression: hand-written non-manifest bindings survive ───────────
def test_apply_preserves_nonmanifest_hand_bindings(tmp_path):
    """A getset entry + PyMethodDef entry + C function whose names are NOT in
    the manifest must pass through apply byte-for-byte. This is exactly what
    the reverted 0.14.8/0.14.9 whole-fragment re-render silently dropped
    (doppler's cvt `clipped` getters, RateConverter `stages` accessor)."""
    dest = tmp_path / "dsp"
    _scaffold(dest)
    # A manifest property gives us a real PyGetSetDef array to live alongside.
    _silent(property_run, dest, "mix", "level", "sig", "float", False)
    frag = _frag(dest, "sig", "mix")
    text = frag.read_text(encoding="utf-8")

    # Hand C function + a non-manifest getset entry + a non-manifest method.
    hand_fn = (
        "\nstatic PyObject *\n"
        "Mix_getprop_clipped(MixObject *self, void *closure)\n"
        "{\n    /* HAND CLIPPED GETTER */\n    Py_RETURN_FALSE;\n}\n"
    )
    text = text.replace(
        "static PyGetSetDef Mix_getset[] = {",
        hand_fn + "\nstatic PyGetSetDef Mix_getset[] = {\n"
        '    { "clipped", (getter)Mix_getprop_clipped, NULL,'
        ' "HAND CLIPPED DOC", NULL },',
        1,
    )
    text = re.sub(
        r"(static PyMethodDef \w+_methods\[\] = \{)",
        r'\1\n    {"peek", (PyCFunction)Mix_scale, METH_NOARGS,'
        ' "HAND PEEK DOC"},',
        text,
        count=1,
    )
    frag.write_text(text, encoding="utf-8")

    _annotate(dest, "mix", "mix_scale", "Scale the input sample.")
    _annotate(dest, "mix", "mix_get_level", "Output level in dBFS.")
    _silent(apply_run, dest)
    out = frag.read_text(encoding="utf-8")

    # Manifest docs refreshed.
    assert "Scale the input sample." in out
    assert "Output level in dBFS." in out
    # Non-manifest hand bindings survive verbatim.
    assert "/* HAND CLIPPED GETTER */" in out
    assert "Mix_getprop_clipped" in out
    assert '"clipped"' in out
    assert "HAND CLIPPED DOC" in out  # hand doc NOT overwritten
    assert '"peek"' in out
    assert "HAND PEEK DOC" in out


# ── (d) multi-object: docs land right, never bleed across objects ────────────
def test_apply_multi_object_multi_method(tmp_path):
    dest = tmp_path / "dsp"
    _scaffold(dest)
    _silent(
        method_run, dest, "mix", "bias", "sig", "float", "float", False, []
    )
    _silent(
        object_run,
        dest,
        "pan",
        module="sig",
        state_vars=[("pos", "float", "0.0f")],
        arg_type="float",
        return_type="float",
    )
    _silent(
        method_run, dest, "pan", "spread", "sig", "float", "float", False, []
    )
    _mark_body(_frag(dest, "sig", "mix"), "Mix_scale", "/* MIX-BODY */")
    _mark_body(_frag(dest, "sig", "pan"), "Pan_spread", "/* PAN-BODY */")
    _annotate(dest, "mix", "mix_scale", "Mix scale brief.")
    _annotate(dest, "pan", "pan_spread", "Pan spread brief.")
    _silent(apply_run, dest)
    mix = _frag(dest, "sig", "mix").read_text(encoding="utf-8")
    pan = _frag(dest, "sig", "pan").read_text(encoding="utf-8")
    assert "/* MIX-BODY */" in mix and "Mix scale brief." in mix
    assert "/* PAN-BODY */" in pan and "Pan spread brief." in pan
    assert "Pan spread brief." not in mix  # no cross-object bleed


# ── (e) idempotent ───────────────────────────────────────────────────────────
def test_apply_fragment_refresh_idempotent(tmp_path):
    dest = tmp_path / "dsp"
    _scaffold(dest)
    _annotate(dest, "mix", "mix_scale", "Scale the input sample.")
    _silent(apply_run, dest)
    frag = _frag(dest, "sig", "mix")
    first = frag.read_bytes()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        apply_run(dest)
    assert frag.read_bytes() == first
    assert "sig_ext_mix.c" not in buf.getvalue()  # not reported as changed


# ── (f) no_generate module untouched ─────────────────────────────────────────
def test_apply_skips_no_generate_module(tmp_path):
    dest = tmp_path / "dsp"
    _scaffold(dest)
    man = dest / "just-makeit.toml"
    man.write_text(
        man.read_text(encoding="utf-8")
        + '\n[module.hand]\nno_generate = "true"\n',
        encoding="utf-8",
    )
    hand_dir = dest / "native" / "src" / "hand"
    hand_dir.mkdir(parents=True, exist_ok=True)
    hand_file = hand_dir / "hand_ext_thing.c"
    sentinel = "/* fully hand-written, no_generate */\n"
    hand_file.write_text(sentinel, encoding="utf-8")
    _silent(apply_run, dest)
    assert hand_file.read_text(encoding="utf-8") == sentinel


# ── (g) standalone object generation not regressed by the module pass ────────
def test_apply_standalone_object_not_regressed(tmp_path):
    dest = tmp_path / "dsp"
    _silent(new_run, "dsp", dest)
    _silent(
        object_run,
        dest,
        "gain",
        None,
        state_vars=[("g", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    ext = dest / "native" / "src" / "gain" / "gain_ext.c"
    before = ext.read_bytes()
    _silent(apply_run, dest)
    assert ext.exists() and ext.read_bytes() == before


# ── (h) *_extra.c hand files never modified ──────────────────────────────────
def test_apply_never_touches_ext_extra(tmp_path):
    dest = tmp_path / "dsp"
    _scaffold(dest)
    ext_dir = dest / "native" / "src" / "sig"
    extra = ext_dir / "sig_ext_extra.c"
    sentinel = '/* HAND EXTRA */\n#include "sig/sig_core.h"\n'
    extra.write_text(sentinel, encoding="utf-8")
    _annotate(dest, "mix", "mix_scale", "Scale the input sample.")
    _silent(apply_run, dest)
    assert extra.read_text(encoding="utf-8") == sentinel
    agg = (ext_dir / "sig_ext.c").read_text(encoding="utf-8")
    assert "sig_ext_extra.c" in agg


# ── unit tests for the splicer itself ────────────────────────────────────────
_EXISTING = (
    "static PyGetSetDef X_getset[] = {\n"
    '    { "rate", (getter)X_get_rate, (setter)X_set_rate, NULL, NULL },\n'
    '    { "clipped", (getter)X_get_clipped, NULL, NULL, NULL },\n'
    "    { NULL }\n};\n\n"
    "static PyMethodDef XObj_methods[] = {\n"
    '    {"execute", (PyCFunction)XObj_execute, METH_VARARGS,\n'
    '     "execute(x) -> ndarray\\n"\n'
    '     "\\n"\n'
    '     "Old fallback, with a brace {x} and comma, inside.\\n"\n'
    '     "    >>> y.dtype\\n"\n'
    "     \"    dtype('complex64')\\n\"},\n"
    '    {"stages", (PyCFunction)XObj_stages, METH_NOARGS, "HAND STAGES"},\n'
    '    {"__enter__", (PyCFunction)XObj_enter, METH_NOARGS, NULL},\n'
    "    {NULL}\n};\n\n"
    "static PyTypeObject XType = {\n"
    "    PyVarObject_HEAD_INIT(NULL, 0)\n"
    '    .tp_name = "x.X",\n'
    '    .tp_doc  = "X type.",\n'
    "};\n"
)
_REFERENCE = (
    "static PyGetSetDef X_getset[] = {\n"
    '    { "rate", (getter)X_get_rate, (setter)X_set_rate,'
    ' "Sample rate.\\n", NULL },\n'
    "    { NULL }\n};\n\n"
    "static PyMethodDef XObj_methods[] = {\n"
    '    {"execute", (PyCFunction)XObj_execute, METH_VARARGS,\n'
    '     "execute(x) -> ndarray\\n"\n'
    '     "\\n"\n'
    '     "Derived summary.\\n"},\n'
    '    {"__enter__", (PyCFunction)XObj_enter, METH_NOARGS, NULL},\n'
    "    {NULL}\n};\n\n"
    "static PyTypeObject XType = {\n"
    "    PyVarObject_HEAD_INIT(NULL, 0)\n"
    '    .tp_name = "x.X",\n'
    '    .tp_doc  = "A derived class doc.",\n'
    "};\n"
)
# Scaffold form: no Doxygen. execute keeps its name-fallback summary (matching
# _EXISTING), rate carries the bare name fallback, tp_doc is "<Class> type.".
_FALLBACK = (
    "static PyGetSetDef X_getset[] = {\n"
    '    { "rate", (getter)X_get_rate, (setter)X_set_rate,'
    ' "Rate.\\n", NULL },\n'
    "    { NULL }\n};\n\n"
    "static PyMethodDef XObj_methods[] = {\n"
    '    {"execute", (PyCFunction)XObj_execute, METH_VARARGS,\n'
    '     "execute(x) -> ndarray\\n"\n'
    '     "\\n"\n'
    '     "Old fallback, with a brace {x} and comma, inside.\\n"\n'
    '     "    >>> y.dtype\\n"\n'
    "     \"    dtype('complex64')\\n\"},\n"
    '    {"__enter__", (PyCFunction)XObj_enter, METH_NOARGS, NULL},\n'
    "    {NULL}\n};\n\n"
    "static PyTypeObject XType = {\n"
    "    PyVarObject_HEAD_INIT(NULL, 0)\n"
    '    .tp_name = "x.X",\n'
    '    .tp_doc  = "X type.",\n'
    "};\n"
)


def test_transplant_updates_scaffold_and_empty_slots():
    out = D.transplant_docs(_EXISTING, _REFERENCE, _FALLBACK)
    # execute held the scaffold summary -> refreshed to derived.
    assert "Derived summary." in out and "Old fallback" not in out
    # rate doc was NULL and the derived form is real Doxygen -> filled.
    assert "Sample rate." in out
    # tp_doc held "X type." (scaffold) -> refreshed.
    assert "A derived class doc." in out and "X type." not in out


def test_transplant_preserves_nonmanifest():
    out = D.transplant_docs(_EXISTING, _REFERENCE, _FALLBACK)
    assert "X_get_clipped" in out and '"clipped"' in out  # hand getter
    assert "HAND STAGES" in out  # hand method (not in reference)


def test_transplant_preserves_handwritten_doc():
    # A manifest method whose existing doc is NOT the scaffold form (someone
    # hand-wrote a richer docstring into the sacred fragment) must be left
    # alone — this is the RateConverter / cvt case that motivated the gating.
    hand = _EXISTING.replace(
        "Old fallback, with a brace {x} and comma, inside.",
        "Hand-written rich summary that must survive.",
    )
    out = D.transplant_docs(hand, _REFERENCE, _FALLBACK)
    assert "Hand-written rich summary that must survive." in out
    assert "Derived summary." not in out  # not clobbered


def test_transplant_idempotent():
    once = D.transplant_docs(_EXISTING, _REFERENCE, _FALLBACK)
    assert D.transplant_docs(once, _REFERENCE, _FALLBACK) == once


def test_transplant_no_churn_on_name_fallback_tp_doc():
    # tp_doc whose derived form is just the name fallback (no create @brief)
    # must not churn (e.g. "Acc type." -> "Acc type.\\n").
    ex = '.tp_doc  = "Acc type.",\n'
    ref = '.tp_doc  = "Acc type.\\n",\n'
    fb = '.tp_doc  = "Acc type.\\n",\n'
    assert D.transplant_docs(ex, ref, fb) == ex


def test_transplant_handles_missing_arrays():
    # A fragment with neither array nor tp_doc is returned unchanged.
    assert (
        D.transplant_docs("int x = 0;\n", _REFERENCE, _FALLBACK)
        == "int x = 0;\n"
    )
