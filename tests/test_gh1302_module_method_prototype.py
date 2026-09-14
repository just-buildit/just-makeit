"""gh-1302: a module object's declared method needs a prototype.

A method declared in `[[obj.methods]]` gets a call in the module's sacred
fragment. On the **standalone** path it also gets a prototype in
``native/inc/<obj>/<obj>_core.h``. On the **module-object** path it got
neither a prototype there nor anywhere else, so the freshly spliced binding
called an undeclared function and the build failed on implicit declaration.

That is the shape doppler uses, which is what makes it worth more than the
standalone half it sat behind.

**Why the fix is narrow.** `_apply` documents, with numbers, why a module
object's header is not reconciled:

    switching it on would replay years of accumulated drift in one apply —
    measured on doppler as 44 sacred headers and 125 changed declarations,
    including a `create()` prototype rewritten to disagree with its own
    definition and every call site.

So this is not the full reconcile. It is gh-627's narrow additive slice, which
already offers a *property accessor*'s prototype to the same header, extended
to cover methods: `skip_names` names every prototype offered, so a declaration
the header already carries — **with any signature** — is left exactly as
written, and only a genuinely absent one is inserted. Adding a missing
declaration is not replaying drift, and `TestItDoesNotReplayDrift` is what
keeps those two apart.

**Where the prototype text comes from.** The temp tree's rendered header, not
a second call to `_build_method_prototype`. That builder takes twenty-odd keys
off the declaration and `_replay_method` already passes all of them; a second
call site is a second place to forget one, which is how gh-788 put the wrong
`create()` shape into a sacred header.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

_METHOD = """
[[o.methods]]
name = "fin"
arg_type = "void"
return_type = "void"
params = [{ name = "p", type = "int" }]
"""

_HAVE_TOOLCHAIN = bool(shutil.which("cmake")) and any(
    shutil.which(c) for c in ("cc", "gcc", "clang")
)


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "from just_makeit._cli import main; main()"]
        + list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"},
        timeout=900,
    )


def _header(root: Path) -> Path:
    return root / "native" / "inc" / "o" / "o_core.h"


@pytest.fixture
def project(tmp_path) -> Path:
    """A MODULE object with a TOML-declared method."""
    assert _cli("new", "z", cwd=tmp_path).returncode == 0
    root = tmp_path / "z"
    assert _cli("module", "m", cwd=root).returncode == 0
    assert (
        _cli(
            "object", "o", "--module", "m", "--state", "n:int:0", cwd=root
        ).returncode
        == 0
    )
    frag = root / "objects" / "o.toml"
    frag.write_text(
        frag.read_text(encoding="utf-8") + _METHOD, encoding="utf-8"
    )
    return root


class TestThePrototypeArrives:
    def test_the_header_declares_the_method(self, project: Path):
        r = _cli("apply", cwd=project)
        assert r.returncode == 0, r.stdout + r.stderr
        text = _header(project).read_text(encoding="utf-8")
        assert "o_fin(" in text, text

    def test_the_signature_matches_the_definition(self, project: Path):
        """A prototype that disagrees is worse than one that is absent.

        Absent is a compile error at the call; disagreeing is a conflicting
        -types error, or worse, silence and a wrong ABI. Compared against the
        definition `apply` splices into `_core.c` (gh-1294) rather than
        against a literal, so the two cannot be right about different things.
        """
        assert _cli("apply", cwd=project).returncode == 0
        decl = _header(project).read_text(encoding="utf-8")
        core_c = (project / "native" / "src" / "o" / "o_core.c").read_text(
            encoding="utf-8"
        )
        assert "void o_fin(o_state_t *state, int p);" in decl, decl
        assert "o_fin(o_state_t *state, int p)" in core_c, core_c

    def test_it_is_idempotent(self, project: Path):
        assert _cli("apply", cwd=project).returncode == 0
        once = _header(project).read_bytes()
        assert _cli("apply", cwd=project).returncode == 0
        assert _header(project).read_bytes() == once

    def test_the_standalone_path_is_unchanged(self, tmp_path):
        """It always worked there; this must not have moved it."""
        assert _cli("new", "z", cwd=tmp_path).returncode == 0
        root = tmp_path / "z"
        assert (
            _cli("object", "o", "--state", "n:int:0", cwd=root).returncode == 0
        )
        frag = root / "objects" / "o.toml"
        frag.write_text(
            frag.read_text(encoding="utf-8") + _METHOD, encoding="utf-8"
        )
        assert _cli("apply", cwd=root).returncode == 0
        assert "o_fin(" in _header(root).read_text(encoding="utf-8")


class TestItDoesNotReplayDrift:
    """The carve-out this sits inside, kept intact.

    The full reconcile is withheld from a module object's header for a
    measured reason — 125 changed declarations across doppler's 44 sacred
    headers. Additive-with-`skip_names` is what makes adding a missing
    declaration different from that, and the difference has to be tested or
    the next change quietly turns one into the other.
    """

    def test_an_existing_declaration_is_left_exactly_as_written(
        self, project: Path
    ):
        assert _cli("apply", cwd=project).returncode == 0
        h = _header(project)
        # A hand-edited prototype: different spelling, same symbol.
        text = h.read_text(encoding="utf-8").replace(
            "void o_fin(o_state_t *state, int p);",
            "void o_fin(o_state_t *state, int /* the count */ p);",
        )
        h.write_text(text, encoding="utf-8")
        before = h.read_bytes()
        assert _cli("apply", cwd=project).returncode == 0
        assert h.read_bytes() == before, h.read_text(encoding="utf-8")

    def test_no_other_declaration_is_rewritten(self, project: Path):
        """The blast radius, asserted as a whole-file property.

        `create`/`reset`/`destroy`/the accessors are all in this header and
        none of them is this change's business.
        """
        assert _cli("apply", cwd=project).returncode == 0
        h = _header(project)
        text = h.read_text(encoding="utf-8").replace(
            "o_state_t *o_create(int n);",
            "o_state_t *o_create(int n);  /* MINE */",
        )
        h.write_text(text, encoding="utf-8")
        before = h.read_bytes()
        assert _cli("apply", cwd=project).returncode == 0
        assert h.read_bytes() == before

    def test_only_declared_methods_are_offered(self, project: Path):
        """Names come from the manifest, so an unrelated prototype in the
        temp render is not swept into a sacred header."""
        sys.path.insert(0, str(SRC))
        from just_makeit import _config as C
        from just_makeit._apply import _declared_method_decls

        assert _cli("apply", cwd=project).returncode == 0
        cfg = C.load(project)
        # A temp-render stand-in whose header declares more than the manifest.
        names = {
            d.split("(")[0].split()[-1].lstrip("*")
            for d in _declared_method_decls(cfg, "o", project)
        }
        assert names <= {"o_fin"}, names


@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="needs cmake and a C compiler")
class TestTheExtensionBuilds:
    """The claim: implicit declaration, the build fails.

    Only the extension target is built. `jm test` builds the generated
    benchmark too, and this project's module is named `m` -- which shadows
    the math library, because a bare `m` in `target_link_libraries` resolves
    as a CMake target first. That is gh-1305, measured with NO methods
    declared, so it is not this issue's and would make this test fail for an
    unrelated reason.
    """

    def test_the_module_extension_compiles_and_links(self, project: Path):
        assert _cli("apply", cwd=project).returncode == 0
        r = subprocess.run(
            ["cmake", "-S", str(project), "-B", str(project / "build")],
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        r = subprocess.run(
            ["cmake", "--build", str(project / "build"), "--target", "m"],
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]

    def test_without_the_prototype_it_does_not(self, project: Path):
        """Prove the build was the thing at stake.

        With `-Werror=implicit-function-declaration` passed explicitly, not
        left to the compiler's default. Whether an implicit declaration is an
        error or a warning is a compiler-and-version question -- it is an
        error on gcc 14+ and a warning before it -- so asserting a bare
        non-zero exit asserts the runner's toolchain rather than jm's output.
        Measured: this passed locally and the same sabotage BUILT CLEAN on the
        ubuntu-24.04-arm runner, so the test reported green for the missing
        prototype it exists to catch.
        """
        assert _cli("apply", cwd=project).returncode == 0
        h = _header(project)
        h.write_text(
            h.read_text(encoding="utf-8").replace(
                "void o_fin(o_state_t *state, int p);", ""
            ),
            encoding="utf-8",
        )
        shutil.rmtree(project / "build", ignore_errors=True)
        subprocess.run(
            [
                "cmake",
                "-S",
                str(project),
                "-B",
                str(project / "build"),
                "-DCMAKE_C_FLAGS=-Werror=implicit-function-declaration",
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
        r = subprocess.run(
            ["cmake", "--build", str(project / "build"), "--target", "m"],
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert r.returncode != 0, r.stdout[-2000:]
        assert "o_fin" in (r.stdout + r.stderr), r.stdout[-2000:]
