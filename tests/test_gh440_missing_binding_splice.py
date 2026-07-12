"""gh-440 — `jm apply` additively splices a manifest-derived method/property
missing from an existing sacred `<mod>_ext_<obj>.c` fragment.

Field data from doppler: a fragment created before a method/property existed
in the manifest (or hand-carried across a `git pull` that added one) used to
require the destructive "delete the fragment, let `jm apply` recreate it"
cycle to adopt it -- which throws away every other hand patch the fragment
carries (doppler lost a `PyErr_WarnEx` hand-patch this way). `jm apply` now
diffs the manifest-derived binding set against what the fragment implements
by name (mirroring `_inject_decls_into_core_h` for header decls and gh-404's
serializable-triplet transplant) and splices in only what's missing --
every existing binding, hand-patched or not, is left byte-for-byte
untouched.

v1 = additive only (per the issue's scoping): a fragment that already has at
least one method (or one property) can gain more of the same kind; going
from zero properties to one still needs the old delete-and-recreate cycle,
since there is no existing `PyGetSetDef` array to splice into.
"""

import contextlib
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit import _status  # noqa: E402


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _frag(dest: Path, module: str, obj: str) -> Path:
    return dest / "native" / "src" / module / f"{module}_ext_{obj}.c"


def _scaffold(dest: Path):
    _silent(new_run, "dsp", dest)
    _silent(module_run, dest, "sig")
    _silent(
        object_run,
        dest,
        "mix",
        module="sig",
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )


def _delete_function_and_row(text: str, fn_name: str, row_name: str) -> str:
    """Remove *fn_name*'s whole definition and its `"row_name"` PyMethodDef/
    PyGetSetDef row from *text* -- simulates a fragment that predates a
    manifest-derived binding those tokens belong to. Brace-aware (a row's
    docstring field may span several lines), unlike the production splice
    engine's own row scanning only because this is deliberately a much
    simpler standalone helper for test setup.
    """
    fn_re = re.compile(
        r"static [^\n]+\n" + re.escape(fn_name) + r"\([^\n]*\n\{.*?\n\}\n\n?",
        re.DOTALL,
    )
    text, n = fn_re.subn("", text, count=1)
    assert n == 1, f"could not remove function {fn_name}"

    needle = f'"{row_name}"'
    quote_at = text.index(needle)
    open_at = text.rindex("{", 0, quote_at)
    depth = 0
    i = open_at
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    close_at = i + 1
    # Absorb a trailing comma and newline so no blank/dangling comma remains.
    if close_at < len(text) and text[close_at] == ",":
        close_at += 1
    if close_at < len(text) and text[close_at] == "\n":
        close_at += 1
    # And the row's own leading indentation on its line.
    line_start = text.rfind("\n", 0, open_at) + 1
    return text[:line_start] + text[close_at:]


class TestNewMethodSpliced:
    def test_missing_method_appended_hand_patch_survives(self, tmp_path):
        dest = tmp_path / "dsp"
        _scaffold(dest)
        _silent(
            method_run,
            dest,
            "mix",
            "scale",
            "sig",
            "float",
            "float",
            False,
            [],
        )
        frag_path = _frag(dest, "sig", "mix")
        text = frag_path.read_text(encoding="utf-8")

        # Hand patch on an unrelated, surviving function.
        text = text.replace(
            "Mix_reset(MixObject *self, PyObject *Py_UNUSED(ignored))\n{\n",
            "Mix_reset(MixObject *self, PyObject *Py_UNUSED(ignored))\n{\n"
            "    /* hand patch: reset telemetry counter too */\n",
            1,
        )
        assert "hand patch: reset telemetry counter too" in text

        # Simulate the fragment predating the "scale" method's addition.
        text = _delete_function_and_row(text, "Mix_scale", "scale")
        assert "Mix_scale" not in text
        frag_path.write_text(text, encoding="utf-8")

        _silent(apply_run, dest)

        after = frag_path.read_text(encoding="utf-8")
        assert "Mix_scale" in after
        assert '"scale"' in after
        assert "hand patch: reset telemetry counter too" in after

        # Idempotent: a second apply makes no further change.
        _silent(apply_run, dest)
        assert frag_path.read_text(encoding="utf-8") == after


class TestNewPropertySpliced:
    def test_missing_property_appended_alongside_existing(self, tmp_path):
        dest = tmp_path / "dsp"
        _scaffold(dest)
        # An object needs an existing PyGetSetDef array for v1's additive
        # splice to have somewhere to land -- add two properties, then
        # simulate the fragment predating the second one.
        _silent(property_run, dest, "mix", "level", "sig", "float", False)
        _silent(property_run, dest, "mix", "locked", "sig", "uint8_t", False)
        frag_path = _frag(dest, "sig", "mix")
        text = frag_path.read_text(encoding="utf-8")
        assert '"level"' in text and '"locked"' in text

        text = _delete_function_and_row(text, "Mix_getprop_locked", "locked")
        assert "locked" not in text
        frag_path.write_text(text, encoding="utf-8")

        _silent(apply_run, dest)
        after = frag_path.read_text(encoding="utf-8")
        assert "Mix_getprop_locked" in after
        assert '"locked"' in after
        assert '"level"' in after  # untouched sibling survives

        _silent(apply_run, dest)
        assert frag_path.read_text(encoding="utf-8") == after


class TestExistingBindingsUntouched:
    def test_present_binding_not_reported_as_updated(self, tmp_path, capsys):
        # Sanity guard: nothing to splice -> no spurious "update" noise and
        # no byte change, matching the transplant_docs idempotence this
        # builds on.
        dest = tmp_path / "dsp"
        _scaffold(dest)
        _silent(
            method_run,
            dest,
            "mix",
            "scale",
            "sig",
            "float",
            "float",
            False,
            [],
        )
        frag_path = _frag(dest, "sig", "mix")
        before = frag_path.read_text(encoding="utf-8")

        _silent(apply_run, dest)

        assert frag_path.read_text(encoding="utf-8") == before

        capsys.readouterr()
        rc = _status.run(dest)
        out = capsys.readouterr().out
        assert rc == 0
        assert "STALE" not in out
