"""gh-428 — `manual_stub = true` preserves a hand-written `.pyi` method
across regen.

gh-426 found that jm's `.pyi` generators are pure functions of the
manifest: a hand-written method stub with zero manifest declaration
(doppler's repro: `Fft.execute_ci16`, a CPython overload hand-added
directly to the sacred `_ext_<obj>_extra.c` fragment) silently vanishes on
every `jm apply`. This is the follow-up: `manual_stub = true` gives such a
method a real (if minimal) manifest presence, and jm's `.pyi` codegen
splices the previously hand-written text back over the freshly rendered
placeholder instead of clobbering it.

Critical constraint: unlike `varargs` (which owns and creates a fresh
`<comp>_<name>_core.c` stub file, and so DOES emit an `extern` decl +
`PyMethodDef` row), a `manual_stub` method's C binding already exists,
hand-written, in a fragment jm never created -- jm must emit ZERO
C-side declarations for it, or the next `jm apply` collides with the
user's own binding.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._module import run as module_run
from just_makeit._object import run as object_run
from just_makeit._method import run as method_run
from just_makeit._remove import run as remove_run
from just_makeit._apply import run as apply_run
from just_makeit._cli_method import run as cli_method_run
from just_makeit._config import load, methods
from just_makeit import _status


# gh-744: the placeholder docstring is 120 columns as one line, so it is now
# emitted wrapped. The exact on-disk text is what this test substitutes for,
# hence the literal rather than the logical sentence.
_PLACEHOLDER = (
    "    def execute_ci16(self, *args: Any, **kwargs: Any) -> Any:\n"
    '        """<<MANUAL_STUB>> hand-write this signature/docstring in the'
    " .pyi — jm\n"
    "        preserves it verbatim on future regens.\n"
    '        """\n'
)
_HAND_WRITTEN = (
    "    def execute_ci16(self, x: NDArray[np.int16]) -> None:\n"
    '        """Hand-written int16 overload; no manifest entry."""\n'
    "        ...\n"
)


def _hand_edit(pyi_path: Path) -> None:
    text = pyi_path.read_text(encoding="utf-8")
    assert _PLACEHOLDER in text, text
    pyi_path.write_text(
        text.replace(_PLACEHOLDER, _HAND_WRITTEN), encoding="utf-8"
    )


class TestRoundTripSurvives:
    def test_module_path(self, tmp_path):
        # Standalone-vs-module dual coverage: _stubs.py::make_module_pyi /
        # _obj_stub is a completely separate generator from
        # _context/_methods.py's make_methods_ctx (used by standalone
        # objects), and both must independently preserve manual_stub text.
        dest = tmp_path / "dsp"
        new_run("dsp", dest)
        module_run(dest, "sig")
        object_run(dest, "nco", "sig", state_vars=[("freq", "double", "0.0")])
        method_run(
            dest,
            "nco",
            "execute_ci16",
            "sig",
            "void",
            "float _Complex",
            False,
            [],
            manual_stub=True,
        )
        pyi_path = dest / "src" / "dsp" / "sig" / "sig.pyi"
        _hand_edit(pyi_path)
        before = pyi_path.read_text(encoding="utf-8")

        apply_run(dest)

        after = pyi_path.read_text(encoding="utf-8")
        assert after == before
        assert "Hand-written int16 overload" in after

    def test_standalone_path(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest)
        object_run(dest, "fft", module=None)
        method_run(
            dest,
            "fft",
            "execute_ci16",
            None,
            "void",
            "float _Complex",
            False,
            [],
            manual_stub=True,
        )
        pyi_path = dest / "src" / "dsp" / "fft.pyi"
        _hand_edit(pyi_path)
        before = pyi_path.read_text(encoding="utf-8")

        apply_run(dest)

        after = pyi_path.read_text(encoding="utf-8")
        assert after == before
        assert "Hand-written int16 overload" in after


class TestFreshDeclaration:
    def test_placeholder_emitted_no_crash(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest)
        object_run(dest, "fft", module=None)
        method_run(
            dest,
            "fft",
            "execute_ci16",
            None,
            "void",
            "float _Complex",
            False,
            [],
            manual_stub=True,
        )
        pyi_path = dest / "src" / "dsp" / "fft.pyi"
        text = pyi_path.read_text(encoding="utf-8")
        assert "<<MANUAL_STUB>>" in text
        assert (
            "def execute_ci16(self, *args: Any, **kwargs: Any) -> Any:" in text
        )


class TestNoCSideRegression:
    def test_no_extern_or_pymethoddef_standalone(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest)
        object_run(dest, "fft", module=None)
        method_run(
            dest,
            "fft",
            "execute_ci16",
            None,
            "void",
            "float _Complex",
            False,
            [],
            manual_stub=True,
        )
        ext_c = (dest / "native" / "src" / "fft" / "fft_ext.c").read_text(
            encoding="utf-8"
        )
        core_h = (dest / "native" / "inc" / "fft" / "fft_core.h").read_text(
            encoding="utf-8"
        )
        core_c = (dest / "native" / "src" / "fft" / "fft_core.c").read_text(
            encoding="utf-8"
        )
        assert "execute_ci16" not in ext_c
        assert "execute_ci16" not in core_h
        assert "execute_ci16" not in core_c

    def test_no_extern_or_pymethoddef_after_apply(self, tmp_path):
        # jm apply's real reconciliation path (_overwrite_if_changed) is a
        # separate code path from method_run's own regen tail -- exercise
        # it too, so a regression there (e.g. a future ext.c change that
        # stops respecting manual_stub) is caught.
        dest = tmp_path / "dsp"
        new_run("dsp", dest)
        object_run(dest, "fft", module=None)
        method_run(
            dest,
            "fft",
            "execute_ci16",
            None,
            "void",
            "float _Complex",
            False,
            [],
            manual_stub=True,
        )
        apply_run(dest)
        ext_c = (dest / "native" / "src" / "fft" / "fft_ext.c").read_text(
            encoding="utf-8"
        )
        assert "execute_ci16" not in ext_c


class TestStatusComposesCorrectly:
    def test_round_tripped_stub_not_dropped(self, tmp_path, capsys):
        dest = tmp_path / "dsp"
        new_run("dsp", dest)
        object_run(dest, "fft", module=None)
        method_run(
            dest,
            "fft",
            "execute_ci16",
            None,
            "void",
            "float _Complex",
            False,
            [],
            manual_stub=True,
        )
        pyi_path = dest / "src" / "dsp" / "fft.pyi"
        _hand_edit(pyi_path)
        apply_run(dest)
        capsys.readouterr()

        rc = _status.run(dest)
        out = capsys.readouterr().out
        assert "DROPPED" not in out
        assert rc == 0


class TestRemovalDropsStub:
    def test_removed_method_is_gone_after_apply(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest)
        object_run(dest, "fft", module=None)
        method_run(
            dest,
            "fft",
            "execute_ci16",
            None,
            "void",
            "float _Complex",
            False,
            [],
            manual_stub=True,
        )
        pyi_path = dest / "src" / "dsp" / "fft.pyi"
        _hand_edit(pyi_path)
        apply_run(dest)
        assert "execute_ci16" in pyi_path.read_text(encoding="utf-8")

        remove_run(
            dest, "method", "execute_ci16", object_name="fft", force=True
        )
        apply_run(dest)

        assert "execute_ci16" not in pyi_path.read_text(encoding="utf-8")
        assert not any(
            m["name"] == "execute_ci16" for m in methods(load(dest), "fft")
        )


class TestConflictingFlagValidation:
    def test_manual_stub_with_arg_type_exits(self, tmp_path, monkeypatch):
        dest = tmp_path / "dsp"
        new_run("dsp", dest)
        object_run(dest, "fft", module=None)
        cfg_before = (dest / "just-makeit.toml").read_text(encoding="utf-8")
        monkeypatch.chdir(dest)

        with pytest.raises(SystemExit):
            cli_method_run(
                [
                    "fft",
                    "execute_ci16",
                    "--manual-stub",
                    "--arg-type",
                    "float",
                ]
            )

        assert (dest / "just-makeit.toml").read_text(
            encoding="utf-8"
        ) == cfg_before
        assert not any(
            m["name"] == "execute_ci16" for m in methods(load(dest), "fft")
        )
