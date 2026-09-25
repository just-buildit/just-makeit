"""gh-1404: two members that speak one element, and the generated contract.

A width family states its rule as *"what you read is exactly what you can
write"*. Before this, nothing tied the two faces: measured on 0.78.1, a
`write` taking complex64 beside a `drain` returning float64 was accepted
silently, and the generated pytest asserted no round trip.

Two halves, and the tests below are grouped by them.

**(a) The tie.** `[[<obj>.records]]` already named a STRUCT element once for
both directions (gh-1405); it now also names a SCALAR one, which is the
kind a width family actually carries -- `float _Complex` has no typedef to
point at. A member references it by name in `arg_type` / `return_type`, and
jm substitutes the declared width. So the divergence is not refused, it is
**unrepresentable**: both faces read one declaration.

The manifest keeps the NAME. Writing the resolved width back would restate
the element per member, which is the drift the declaration removes, and is
why `_method.run` keeps `declared_arg_type` separate from the resolved one.

**(b) The generated invariant.** In a jm-owned file, because the author's
`test_<comp>.py` is create-only and would never receive it (see
`_invariants` for the gh-1361 precedent). Split by what is observable
rather than by a flag:

- the input face is generated ALWAYS and passes on a fresh scaffold, which
  is the property `test_a_fresh_scaffold_is_green` pins -- jm's standing
  rule is that every valid command sequence produces a scaffold that
  passes, and a round trip cannot honour it because a borrowing reader's
  stub returns NULL;
- the round trip is generated once the kernel is real, with no `skipif`
  anywhere: a test that reports success while covering nothing is the
  failure mode `_hollow.py` exists to catch.
"""

from __future__ import annotations

from just_makeit import _incpath as INC  # noqa: E402
from pathlib import Path

import shutil

import pytest

from _jmrun import run_cli

FRAG = Path("objects") / "ring.toml"
CORE = Path("native") / "src" / "ring" / "ring_core.c"
PYI = Path("src") / "p" / "ring.pyi"
INV = Path("src") / "p" / "tests" / "test_ring_invariants.py"


def _pair_project(tmp_path: Path) -> Path:
    """A declared scalar element with a writer and a borrowing reader.

    The reader borrows rather than using `variable_output` on purpose: that
    is the shape doppler's buffer family actually declares, and a tie that
    only understood `variable_output` would miss the first adopter.
    """
    root = tmp_path / "w"
    root.mkdir()
    assert (
        run_cli(
            "new",
            "p",
            "--object",
            "ring",
            "--arg-type",
            "float _Complex",
            "--return-type",
            "float _Complex",
            cwd=root,
        ).returncode
        == 0
    )
    proj = root / "p"
    r = run_cli(
        "record", "ring", "sample", "--type", "float _Complex", cwd=proj
    )
    assert r.returncode == 0, r.stderr
    r = run_cli(
        "method",
        "ring",
        "write",
        "--arg-type",
        "sample[]",
        "--return-type",
        "bool",
        cwd=proj,
    )
    assert r.returncode == 0, r.stderr
    r = run_cli(
        "method",
        "ring",
        "wait",
        "--arg-type",
        "void",
        "--return-type",
        "sample",
        "--borrow",
        "--param",
        "n:size_t",
        cwd=proj,
    )
    assert r.returncode == 0, r.stderr
    return proj


def _implement(proj: Path) -> None:
    """Replace the scaffold stubs with bodies that USE state.

    `_invariants.kernel_is_stub` reads that property of the code rather
    than a marker, so this is what "the kernel is real" means to jm.
    """
    core = proj / CORE
    s = core.read_text()
    s = s.replace(
        "(void)state; (void)x; (void)x_len;", "state->gain += x_len;"
    )
    s = s.replace("(void)state; (void)n;", "if (n) state->gain += 1;")
    core.write_text(s)


class TestTheDeclaration:
    def test_a_scalar_element_is_declarable(self, tmp_path):
        proj = _pair_project(tmp_path)
        frag = (proj / FRAG).read_text()
        assert 'name = "sample"' in frag
        assert 'type = "float _Complex"' in frag

    def test_field_and_type_together_are_refused(self, tmp_path):
        """Two KINDS of element under one name is not a thing to allow."""
        proj = _pair_project(tmp_path)
        r = run_cli(
            "record",
            "ring",
            "x",
            "--type",
            "double",
            "--field",
            "i:int16_t",
            cwd=proj,
        )
        assert r.returncode == 1
        assert "two KINDS of element" in r.stderr

    def test_an_element_with_neither_is_refused(self, tmp_path):
        proj = _pair_project(tmp_path)
        r = run_cli("record", "ring", "x", cwd=proj)
        assert r.returncode == 1
        assert "--type <scalar>" in r.stderr


class TestBothFacesReadOneDeclaration:
    def test_the_generated_c_uses_the_declared_width(self, tmp_path):
        """The artefact, not the manifest: one declaration, two prototypes."""
        proj = _pair_project(tmp_path)
        hdr = INC.core_h(proj, "ring").read_text()
        assert "const float _Complex *x" in hdr  # the writer
        assert "float _Complex *ring_wait" in hdr  # the reader
        # Anchored on the DECLARATOR, not the bare word: the generated
        # Doxygen says "Input sample (float _Complex)", so a substring
        # search for "sample" matches jm's prose about itself and asserts
        # nothing. That is the gh-1405 lesson, one feature on.
        assert "const sample *" not in hdr
        assert "sample *ring_wait" not in hdr

    def test_the_two_python_faces_agree(self, tmp_path):
        proj = _pair_project(tmp_path)
        pyi = (proj / PYI).read_text()
        assert "def write(self, x: NDArray[np.complex64]) -> bool:" in pyi
        assert "def wait(self, n: int) -> NDArray[np.complex64]:" in pyi

    def test_the_manifest_keeps_the_name_not_the_width(self, tmp_path):
        """Storing the width would restate the element per member."""
        proj = _pair_project(tmp_path)
        frag = (proj / FRAG).read_text()
        assert 'arg_type = "sample[]"' in frag
        assert 'return_type = "sample"' in frag

    def test_apply_does_not_rewrite_the_reference(self, tmp_path):
        """`apply` replays through `_method.run`, which resolves -- so this
        is where a resolution placed one layer too early shows up."""
        proj = _pair_project(tmp_path)
        before = (proj / FRAG).read_text()
        r = run_cli("apply", cwd=proj)
        assert r.returncode == 0, r.stdout + r.stderr
        assert (proj / FRAG).read_text() == before

    def test_script_replays_a_scalar_element(self, tmp_path):
        """`--type` has no `--field`, so a replay that emitted neither
        produced a `record` command its own CLI refuses."""
        proj = _pair_project(tmp_path)
        out = run_cli("script", cwd=proj)
        assert out.returncode == 0, out.stderr
        assert "just-makeit record ring sample" in out.stdout
        assert "--type" in out.stdout


class TestTheGeneratedInvariant:
    def test_the_input_face_is_generated(self, tmp_path):
        proj = _pair_project(tmp_path)
        assert run_cli("apply", cwd=proj).returncode == 0
        inv = (proj / INV).read_text()
        assert "def test_write_speaks_sample():" in inv
        assert "np.complex64" in inv
        # The refusal half: a foreign width must not be reinterpreted.
        assert "pytest.raises((TypeError, ValueError))" in inv

    def test_no_round_trip_against_a_scaffold_stub(self, tmp_path):
        """Generated unconditionally it would be RED on every new project.

        No `skipif` either -- see the module docstring.
        """
        proj = _pair_project(tmp_path)
        assert run_cli("apply", cwd=proj).returncode == 0
        inv = (proj / INV).read_text()
        assert "round_trip" not in inv
        assert "skipif" not in inv

    def test_the_round_trip_appears_once_the_kernel_is_real(self, tmp_path):
        proj = _pair_project(tmp_path)
        assert run_cli("apply", cwd=proj).returncode == 0
        _implement(proj)
        assert run_cli("apply", cwd=proj).returncode == 0

        inv = (proj / INV).read_text()
        assert "def test_sample_round_trip():" in inv
        assert "assert y.dtype == x.dtype" in inv
        assert "assert y.ndim == x.ndim" in inv
        assert "np.testing.assert_array_equal(y, x)" in inv

    def test_the_file_is_jm_owned_not_the_authors(self, tmp_path):
        """It must be REWRITTEN, or an existing project never gets it."""
        proj = _pair_project(tmp_path)
        assert run_cli("apply", cwd=proj).returncode == 0
        (proj / INV).write_text("# clobbered\n")
        assert run_cli("apply", cwd=proj).returncode == 0
        assert "clobbered" not in (proj / INV).read_text()

    def test_no_file_when_nothing_declares_a_pair(self, tmp_path):
        """A writer with no reader is not a contract, and an empty test
        module is the hollow-target shape."""
        root = tmp_path / "w2"
        root.mkdir()
        assert (
            run_cli(
                "new",
                "q",
                "--object",
                "solo",
                "--arg-type",
                "float",
                "--return-type",
                "float",
                cwd=root,
            ).returncode
            == 0
        )
        proj = root / "q"
        assert run_cli("apply", cwd=proj).returncode == 0
        assert not (
            proj / "src" / "q" / "tests" / "test_solo_invariants.py"
        ).exists()


def _no_toolchain():
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    return None


_SKIP = _no_toolchain()


@pytest.mark.skipif(bool(_SKIP), reason=str(_SKIP))
def test_a_fresh_scaffold_is_green(tmp_path):
    """The whole reason the round trip is not generated against a stub.

    jm's standing rule is that every valid command sequence produces a
    scaffold that COMPILES AND PASSES. `jm test` builds the project and
    runs CTest plus the generated pytest -- including the contract file --
    so the claim is measured rather than reasoned. It is the assertion that
    goes red the moment the round trip is emitted unconditionally, which is
    what pins the two-tier design.

    `test`, NOT `build`: `jm build` also produces a wheel and runs a repair
    pass over it, and on macOS that fails with "Failed to find any binary
    with the required architecture: 'x86_64'" -- the wheel is tagged
    `universal2` while the binary is arm64-only. Wheel packaging is a real
    concern and someone else's; it is nothing this test is about, and
    reaching it turned one leg of CI red for a reason unrelated to the
    feature.

    In THIS process (gh-1374): `run_cli` gives the same isolation as a
    child and the suite does not pay for one. cmake and ctest are still
    children of their own -- that is jm's build, not jm's CLI.
    """
    proj = _pair_project(tmp_path)
    assert run_cli("apply", cwd=proj).returncode == 0
    assert (proj / INV).exists()

    r = run_cli("test", cwd=proj)
    assert r.returncode == 0, (r.stdout + r.stderr)[-4000:]
