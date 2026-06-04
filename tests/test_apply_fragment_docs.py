"""`jm apply` refreshes per-object binding-fragment docstrings (runtime
__doc__) while preserving hand-written C bodies.

Module objects keep their PyMethodDef / tp_doc / PyGetSetDef docs in
<mod>_ext_<obj>.c fragments, which _sync_aggregates does NOT reconcile. The
post-sync _refresh_module_fragments pass re-renders them on the real tree:
docstrings refresh, hand bodies survive, *_extra.c is untouched, idempotent.
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


# ── (b2) hand-edited _init/constructor body survives (doppler regression) ────
def test_apply_preserves_hand_init_body(tmp_path):
    # Objects with bespoke constructor logic hand-written into the fragment's
    # _init must NOT be regenerated to a template by the doc refresh (this
    # broke doppler's HalfbandDecimator/corr2d/detector2d/hbdecim_q15 builds).
    dest = tmp_path / "dsp"
    _scaffold(dest)
    frag = _frag(dest, "sig", "mix")
    _mark_body(frag, "Mix_init", "/* HAND-INIT */")
    _annotate(dest, "mix", "mix_scale", "Scale the input sample.")
    _silent(apply_run, dest)
    out = frag.read_text(encoding="utf-8")
    assert "/* HAND-INIT */" in out  # constructor body preserved
    assert "Scale the input sample." in out  # doc still refreshed


# ── (c) multi-object, multi-method: all bodies preserved, docs land right ────
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
    # docs don't bleed across objects
    assert "Pan spread brief." not in mix


# ── (d) idempotent ───────────────────────────────────────────────────────────
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


# ── (e) no_generate module untouched ─────────────────────────────────────────
def test_apply_skips_no_generate_module(tmp_path):
    dest = tmp_path / "dsp"
    _scaffold(dest)
    # A real no_generate module has no manifest objects — it is hand-written.
    # Declare one and drop a hand-written binding-like file in it.
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
    assert hand_file.read_text(encoding="utf-8") == sentinel  # untouched


# ── (f) standalone object generation not regressed by the module pass ────────
def test_apply_standalone_object_not_regressed(tmp_path):
    # The fix targets module objects; a standalone object's <comp>_ext.c must
    # still generate and survive apply unchanged. (Deriving docstrings into a
    # standalone binding is a separate, pre-existing gap — out of scope here.)
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


# ── (g) *_extra.c hand files never modified ──────────────────────────────────
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
    # and the aggregator includes it
    agg = (ext_dir / "sig_ext.c").read_text(encoding="utf-8")
    assert "sig_ext_extra.c" in agg
