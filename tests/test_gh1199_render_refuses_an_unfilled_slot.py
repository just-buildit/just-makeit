"""gh-1199: a module with `objects = []`, and the slot that reached cmake.

`CMAKE_LISTS_MODULE` has two render sites with divergent contexts. The one
`objects = []` takes supplied neither `extra_ext_sources` nor `module_comment`,
so the written CMakeLists carried the literal template text:

    # <<module_comment>>
    Python3_add_library(emptymod MODULE WITH_SOABI emptymod_ext.c<<extra_ext_sources>>)

and cmake then failed looking for a source file whose name contains `<<`. The
connection back to a missing context key is not obvious from there, which is
most of what it cost.

The wider point, and where the check belongs
--------------------------------------------
The issue asks for `render` to raise on an unmatched slot. **Measured, that is
the wrong place**: rendering is layered, a slot filled a pass later is normal,
and making `render` strict reported **1,557 failures over two slots**
(`<<scaffold_checks>>`, `<<property_struct_fields>>`) that every real path
fills afterwards.

So the question "did anything fill this" is only answerable at the moment a
string becomes a FILE. `_init._write` is that moment, and it is the one every
generator goes through — so one check covers them all rather than each caller
remembering.

Two forms, and only one of them is the bug
------------------------------------------
C/H templates spell slots `/*<<token>>*/` so clang-format can parse them as
valid C. A leftover of that form lands **inside a comment**: untidy, invisible
to the compiler, harmless. The bare `<<token>>` is the one that reaches live
code.

The check counts the bare form only. Counting both turns the suite red at 92
failures over one product shape — a `--no-state` object whose header keeps
`/*<<property_struct_fields>>*/` — which is filed as gh-1200 with that
measurement, rather than folded in here where it would make the refusal fire
on something that harms nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _render as R  # noqa: E402
from just_makeit._init import _write  # noqa: E402


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


@pytest.fixture
def empty_module(tmp_path: Path) -> Path:
    """The issue's repro verbatim: a module declared with no objects."""
    assert _cli("new", "probe", cwd=tmp_path).returncode == 0
    root = tmp_path / "probe"
    manifest = root / "just-makeit.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + "\n[module.emptymod]\nobjects = []\n",
        encoding="utf-8",
    )
    out = _cli("apply", cwd=root)
    assert out.returncode == 0, out.stdout + out.stderr
    return root


class TestTheRepro:
    def test_the_cmakelists_has_no_slots(self, empty_module: Path) -> None:
        cm = (empty_module / "native/src/emptymod/CMakeLists.txt").read_text(
            encoding="utf-8"
        )
        assert "<<" not in cm, cm

    def test_the_add_library_line_names_a_real_source(
        self, empty_module: Path
    ) -> None:
        """The exact text cmake choked on."""
        cm = (empty_module / "native/src/emptymod/CMakeLists.txt").read_text(
            encoding="utf-8"
        )
        assert (
            "Python3_add_library(emptymod MODULE WITH_SOABI emptymod_ext.c)"
            in cm
        ), cm

    def test_the_comment_is_a_comment(self, empty_module: Path) -> None:
        cm = (empty_module / "native/src/emptymod/CMakeLists.txt").read_text(
            encoding="utf-8"
        )
        assert "# emptymod Python module" in cm, cm

    def test_a_module_with_objects_is_unchanged(self, tmp_path: Path) -> None:
        """The peer render site was always right, and has to stay right —
        this fix adds keys to one context, not to the template."""
        assert _cli("new", "p2", cwd=tmp_path).returncode == 0
        root = tmp_path / "p2"
        assert _cli("module", "m", cwd=root).returncode == 0
        assert (
            _cli(
                "object",
                "o",
                "--module",
                "m",
                "--state",
                "g:double:1.0",
                cwd=root,
            ).returncode
            == 0
        )
        cm = (root / "native/src/m/CMakeLists.txt").read_text("utf-8")
        assert "<<" not in cm, cm
        assert "aggregates: O" in cm, cm


class TestTheWriteRefusesASlot:
    """The class fix. `_write` is the one place every generator turns a
    string into a file, so one check covers them all."""

    def test_it_refuses_and_names_the_file_and_the_slot(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "out.txt"
        with pytest.raises(ValueError) as exc:
            _write(target, "add_library(x <<extra_ext_sources>>)\n")
        msg = str(exc.value)
        assert "out.txt" in msg, msg
        assert "<<extra_ext_sources>>" in msg, msg
        assert not target.exists(), "refused and wrote anyway"

    def test_it_names_every_slot(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError) as exc:
            _write(tmp_path / "o.txt", "<<a>> and <<b>>\n")
        msg = str(exc.value)
        assert "<<a>>, <<b>>" in msg, msg

    def test_a_filled_template_writes(self, tmp_path: Path) -> None:
        target = tmp_path / "ok.txt"
        _write(target, "nothing to see\n")
        assert target.read_text(encoding="utf-8") == "nothing to see\n"


class TestWhatIsDeliberateOutput:
    """Three `<<…>>` shapes are output rather than slots. A check that
    refused them would refuse jm's own correct files."""

    def test_the_implement_marker(self, tmp_path: Path) -> None:
        target = tmp_path / "a.c"
        _write(target, "/* <<IMPLEMENT: fir_step>> */\n")
        assert target.exists()

    def test_the_manual_stub_marker(self, tmp_path: Path) -> None:
        target = tmp_path / "a.pyi"
        _write(target, "    <<MANUAL_STUB>>\n")
        assert target.exists()

    def test_the_c_comment_wrapped_form_is_refused_too(
        self, tmp_path: Path
    ) -> None:
        """gh-1200 removed the carve-out this used to assert.

        gh-1199 exempted `/*<<k>>*/` for two reasons: a leftover there lands
        inside a C comment, and counting it cost 92 failures. Both are gone.
        The 92 were a single product shape — a `--no-state` object's header —
        and gh-1200 fixed it at the source, in `render`. Re-measured after
        that fix, widening costs nothing but this assertion.
        """
        with pytest.raises(ValueError) as exc:
            _write(
                tmp_path / "a.h",
                "typedef struct {\n/*<<property_struct_fields>>*/\n} t;\n",
            )
        assert "property_struct_fields" in str(exc.value)

    def test_both_forms_are_reported_together(self, tmp_path: Path) -> None:
        """A file carrying one of each names both, not just the bare one."""
        with pytest.raises(ValueError) as exc:
            _write(tmp_path / "a.h", "/*<<wrapped>>*/ and <<bare>>\n")
        assert "<<bare>>" in str(exc.value)
        assert "<<wrapped>>" in str(exc.value)


class TestRenderItselfStaysPermissive:
    """Where the issue suggested putting it, and why it is not there.

    Rendering is layered. `render` returning a string with a slot in it is
    normal and every real path fills it a pass later; making `render` strict
    reported 1,557 failures over two such slots.
    """

    def test_render_leaves_an_unmatched_slot(self) -> None:
        assert R.render("a <<b>> c", {}) == "a <<b>> c"

    def test_the_layering_this_protects(self) -> None:
        """The second pass is a real thing, not a hypothetical: this is how
        `property_struct_fields` lands inside braces the first pass wrote."""
        first = R.render("struct {\n<<later>>\n}", {"other": "x"})
        assert "<<later>>" in first
        assert R.render(first, {"later": "  int n;"}) == (
            "struct {\n  int n;\n}"
        )


class TestTheSlotScanner:
    """`unfilled_slots` is the one predicate both the refusal and any future
    caller read, so it is asserted directly."""

    @pytest.mark.parametrize(
        "text,want",
        [
            ("<<a>>", {"a"}),
            ("/*<<a>>*/", {"a"}),  # gh-1200: no longer exempt
            ("/* <<IMPLEMENT: x>> */", set()),
            ("<<MANUAL_STUB>>", set()),
            ("<<a>> /*<<b>>*/ <<c>>", {"a", "b", "c"}),  # gh-1200
            ("no slots here", set()),
            ("<< spaced >>", set()),
            ("a << b", set()),
        ],
    )
    def test_it(self, text: str, want: set) -> None:
        assert R.unfilled_slots(text) == want
