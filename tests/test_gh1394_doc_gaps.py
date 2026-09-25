"""gh-1394 / gh-1396: `jm status --docs` names what nothing documents.

doppler's 0.77.2 bump turned 39 property docstrings into name stubs, and the
only way to find them was to diff generated output. None had documentation of
its own: gh-1300 stopped a field inheriting a same-named field's comment from
another struct, so what fell away belonged to `psd_state_t` or `tone_meas_t`.
The gap had always been there; nothing reported it.

The tests below pin BOTH directions of the one property that matters:

- a property the header documents is absent from the report AND carries that
  text in the generated binding;
- a property nothing documents is present in the report AND renders the name
  stub in the binding.

Both faces are asserted on the same project because the report's only value
is agreeing with what jm generates. `_docstring.property_doc` is the single
chain they share; a second copy is what would let them drift, and asserting
one face alone would not notice.
"""

from __future__ import annotations
from _jminc import INC_ROOT  # noqa: E402

import re
from pathlib import Path

import pytest

from _jmrun import run_cli


def _scaffold(tmp_path: Path, documented: bool) -> Path:
    """A project whose `psd` object has two field-backed properties.

    With *documented*, the sacred header carries a trailing `/**<` on the
    `nfft` field -- the doppler shape. `rbw` is never documented, so each
    project holds one of each case.
    """
    root = tmp_path / "proj"
    root.mkdir()
    assert (
        run_cli(
            "new",
            "p",
            "--object",
            "psd",
            "--arg-type",
            "float",
            "--return-type",
            "float",
            "--state",
            "nfft:size_t:1024",
            "--state",
            "rbw:double:1.0",
            cwd=root,
        ).returncode
        == 0
    )
    proj = root / "p"
    header = proj / INC_ROOT / "psd" / "psd_core.h"
    if documented:
        text = header.read_text(encoding="utf-8")
        assert "    size_t nfft;" in text
        header.write_text(
            text.replace(
                "    size_t nfft;",
                "    size_t nfft;  /**< Zero-padded transform length. */",
                1,
            ),
            encoding="utf-8",
        )
    for name, ctype in (("nfft", "size_t"), ("rbw", "double")):
        assert (
            run_cli(
                "property", "psd", name, "--type", ctype, "--field", cwd=proj
            ).returncode
            == 0
        )
    return proj


def _binding_doc(proj: Path, prop: str) -> str:
    """The text of *prop*'s entry in the generated PyGetSetDef table."""
    ext = (proj / "native" / "src" / "psd" / "psd_ext.c").read_text(
        encoding="utf-8"
    )
    m = re.search(rf'\{{ "{prop}",[^\n]*?"((?:[^"\\]|\\.)*)"', ext, re.S)
    assert m, f"no getset entry for {prop} in psd_ext.c"
    # The slot is a C string literal, so it carries its own trailing `\n`.
    return m.group(1).replace("\\n", "\n").strip()


class TestAMemberNothingDocuments:
    def test_it_is_reported_and_renders_the_name_stub(self, tmp_path):
        proj = _scaffold(tmp_path, documented=False)
        out = run_cli("status", "--docs", cwd=proj)
        assert out.returncode == 0, out.stderr
        assert "psd.rbw (property)" in out.stdout
        assert "psd_state_t.rbw" in out.stdout, "must say where to write it"
        # The other face, same project: the binding really does say "Rbw.".
        assert _binding_doc(proj, "rbw") == "Rbw."


class TestAMemberTheHeaderDocuments:
    def test_it_is_absent_from_the_report_and_carries_its_text(self, tmp_path):
        proj = _scaffold(tmp_path, documented=True)
        out = run_cli("status", "--docs", cwd=proj)
        assert out.returncode == 0, out.stderr
        assert "psd.nfft" not in out.stdout
        assert _binding_doc(proj, "nfft") == "Zero-padded transform length."
        # ...and the undocumented sibling is still reported, so this is not
        # passing because the report found nothing at all.
        assert "psd.rbw (property)" in out.stdout


class TestTheReportIsNotAGate:
    def test_it_exits_zero_with_gaps(self, tmp_path):
        """An undocumented property is the author's call, not a failure.

        A ratchet here would fail a project on the day it declares a property
        it has not documented yet, which is the normal order of work.
        """
        proj = _scaffold(tmp_path, documented=False)
        assert run_cli("status", "--docs", cwd=proj).returncode == 0

    def test_a_clean_project_says_so_rather_than_printing_nothing(
        self, tmp_path
    ):
        """Silence reads as a command that did not run."""
        root = tmp_path / "bare"
        root.mkdir()
        assert run_cli("new", "q", cwd=root).returncode == 0
        out = run_cli("status", "--docs", cwd=root / "q")
        assert out.returncode == 0, out.stderr
        assert "every member carries a docstring" in out.stdout


class TestOutsideAProject:
    def test_it_refuses_rather_than_reporting_a_clean_tree(self, tmp_path):
        """No manifest is not the same answer as nothing to report."""
        out = run_cli("status", "--docs", cwd=tmp_path)
        assert out.returncode == 1
        assert "just-makeit.toml" in out.stderr


@pytest.mark.parametrize("documented", [True, False])
def test_the_report_and_the_binding_never_disagree(tmp_path, documented):
    """The property is in the report exactly when the binding shows a stub.

    This is the property that makes the report worth reading, and the reason
    both faces call `_docstring.property_doc` rather than each spelling the
    precedence out.
    """
    proj = _scaffold(tmp_path, documented=documented)
    reported = (
        "psd.nfft (property)" in run_cli("status", "--docs", cwd=proj).stdout
    )
    assert reported == (_binding_doc(proj, "nfft") == "Nfft.")


class TestMethods:
    """gh-1396: a method's chain is manifest `doc` > header `@brief` > name."""

    def _project(self, tmp_path: Path) -> Path:
        root = tmp_path / "m"
        root.mkdir()
        assert (
            run_cli(
                "new",
                "p",
                "--object",
                "psd",
                "--arg-type",
                "float",
                "--return-type",
                "float",
                cwd=root,
            ).returncode
            == 0
        )
        return root / "p"

    def test_an_undocumented_method_is_reported(self, tmp_path):
        proj = self._project(tmp_path)
        assert (
            run_cli(
                "method",
                "psd",
                "sfdr",
                "--arg-type",
                "void",
                "--return-type",
                "double",
                cwd=proj,
            ).returncode
            == 0
        )
        out = run_cli("status", "--docs", cwd=proj)
        assert "psd.sfdr (method)" in out.stdout
        assert "psd_sfdr()" in out.stdout, "must name the C symbol to document"

    def test_a_header_brief_answers_it(self, tmp_path):
        proj = self._project(tmp_path)
        assert (
            run_cli(
                "method",
                "psd",
                "sfdr",
                "--arg-type",
                "void",
                "--return-type",
                "double",
                cwd=proj,
            ).returncode
            == 0
        )
        header = proj / INC_ROOT / "psd" / "psd_core.h"
        text = header.read_text(encoding="utf-8")
        decl = "double psd_sfdr("
        assert decl in text
        header.write_text(
            text.replace(
                decl,
                "/** @brief Spurious-free dynamic range, dB. */\n" + decl,
                1,
            ),
            encoding="utf-8",
        )
        out = run_cli("status", "--docs", cwd=proj)
        assert "psd.sfdr (method)" not in out.stdout


class TestModuleFunctions:
    """gh-1396: a free function's chain, keyed by its bare name."""

    def _project(self, tmp_path: Path) -> Path:
        root = tmp_path / "f"
        root.mkdir()
        assert run_cli("new", "p", cwd=root).returncode == 0
        proj = root / "p"
        assert run_cli("module", "dsp", cwd=proj).returncode == 0
        r = run_cli(
            "function",
            "scale",
            "--module",
            "dsp",
            "--param",
            "x:float",
            "--return-type",
            "float",
            cwd=proj,
        )
        assert r.returncode == 0, r.stderr
        return proj

    def test_an_undocumented_function_is_reported(self, tmp_path):
        proj = self._project(tmp_path)
        out = run_cli("status", "--docs", cwd=proj)
        assert out.returncode == 0, out.stderr
        assert "dsp.scale (function)" in out.stdout

    def test_a_manifest_doc_answers_it(self, tmp_path):
        proj = self._project(tmp_path)
        # A scaffold splits per-module tables into modules/<name>.toml, so
        # the declaration is there rather than in the central manifest.
        toml = next(
            f
            for f in [proj / "just-makeit.toml", *proj.glob("modules/*.toml")]
            if 'name = "scale"' in f.read_text(encoding="utf-8")
        )
        text = toml.read_text(encoding="utf-8")
        toml.write_text(
            text.replace(
                'name = "scale"',
                'name = "scale"\ndoc = "Scale a sample."',
                1,
            ),
            encoding="utf-8",
        )
        out = run_cli("status", "--docs", cwd=proj)
        assert "dsp.scale (function)" not in out.stdout


class TestRecordFields:
    """A record's columns are documented by the C struct they come from."""

    def test_an_undocumented_result_field_is_reported(self, tmp_path):
        root = tmp_path / "r"
        root.mkdir()
        assert (
            run_cli(
                "new",
                "p",
                "--object",
                "meter",
                "--arg-type",
                "float",
                "--return-type",
                "float",
                cwd=root,
            ).returncode
            == 0
        )
        proj = root / "p"
        # The row struct is the author's, and its fields are deliberately
        # left without `/**<` comments -- that is the gap under test.
        header = proj / INC_ROOT / "meter" / "meter_core.h"
        text = header.read_text(encoding="utf-8")
        marker = "typedef struct"
        text = text.replace(
            marker,
            "typedef struct {\n"
            "    double peak_db;\n"
            "    double rms_db;\n"
            "    double crest;  /**< Peak-to-RMS ratio, dB. */\n"
            "} meter_row_t;\n\n" + marker,
            1,
        )
        header.write_text(text, encoding="utf-8")
        r = run_cli(
            "method",
            "meter",
            "read",
            "--arg-type",
            "void",
            "--return-type",
            "meter_row_t",
            "--single",
            "--result-field",
            "peak_db:double",
            "--result-field",
            "rms_db:double",
            "--result-field",
            "crest:double",
            cwd=proj,
        )
        assert r.returncode == 0, r.stderr
        out = run_cli("status", "--docs", cwd=proj)
        assert out.returncode == 0, out.stderr
        assert "meter.read.peak_db (record field)" in out.stdout
        assert "meter.read.rms_db (record field)" in out.stdout
        # ...and the column the struct DOES document is not listed, so this
        # is not passing because every field is reported blindly.
        assert "crest" not in out.stdout
