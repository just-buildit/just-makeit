"""gh-1294: a method declared in TOML must get a body, or nothing links.

Declared through the CLI, a method gets a prototype in ``_core.h``, a call in
the binding, **and** a stub body in ``_core.c``. Declared in TOML and
materialised by ``jm apply``, it got the first two and not the third — so the
extension did not link, while ``apply`` printed *"Project already matches
just-makeit.toml — nothing to do"* and ``status --check`` returned 0.

TOML is the documented way to declare a multi-method component, so the route
jm recommends was the one that did not build.

**Why splicing and not warning.** The issue was filed proposing a warning, on
the premise that ``apply`` must never write to the sacred ``_core.c``. That
premise is wrong: gh-541 already patches it in place (``void`` -> ``int`` on a
``destroy`` that declares a status, appending ``return 0;``), and states the
actual rule — *"`_core.c` is never re-rendered and `_core.h` only ever gains
missing declarations"*. That is an **additive** rule, and a missing definition
is the additive case. The alternative remedy was ``jm regenerate <component>``,
a whole-component rebuild with body-lifting, for one absent function.

**The text is the temp tree's, not a fifth emitter.** ``apply`` already
replays every declared method into its throwaway scaffold, so the stub jm
would write is on disk there, produced by the same four-way shape dispatch
``jm method`` uses. Re-deriving it would be a fifth copy of that dispatch —
the mistake gh-1272 found four of.

**What stops a duplicate symbol.** gh-275: a component's OBJECT lib may
compile sources besides ``<comp>_core.c``. Every ``.c`` in the component's own
directory is read before deciding, and when the CMake names extra sources and
the symbol is in none of them, jm warns instead of guessing — a wrong guess
there is a duplicate definition, which is worse than the missing one.
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


def _core_c(root: Path) -> Path:
    return root / "native" / "src" / "o" / "o_core.c"


@pytest.fixture
def project(tmp_path) -> Path:
    assert _cli("new", "z", cwd=tmp_path).returncode == 0
    root = tmp_path / "z"
    assert _cli("object", "o", "--state", "n:int:0", cwd=root).returncode == 0
    frag = root / "objects" / "o.toml"
    frag.write_text(
        frag.read_text(encoding="utf-8") + _METHOD, encoding="utf-8"
    )
    return root


def _strip_fin(path: Path) -> None:
    """Remove the spliced `o_fin` definition, marker and all."""
    import re

    text = re.sub(
        r"/\* <<IMPLEMENT: fin >> \*/\nvoid\no_fin\(.*?\n\}\n",
        "",
        path.read_text(encoding="utf-8"),
        flags=re.S,
    )
    assert "o_fin" not in text, text
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def module_project(tmp_path) -> Path:
    """The same object, inside a module — where gh-275's guard survives."""
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
    assert _cli("apply", cwd=root).returncode == 0
    return root


class TestTheBodyArrives:
    def test_apply_defines_the_declared_method(self, project: Path):
        r = _cli("apply", cwd=project)
        assert r.returncode == 0, r.stdout + r.stderr
        body = _core_c(project).read_text(encoding="utf-8")
        assert "o_fin(o_state_t *state, int p)" in body, body

    def test_apply_says_so(self, project: Path):
        """`apply` used to print "nothing to do" over exactly this."""
        out = _cli("apply", cwd=project).stdout
        assert "scaffolded o_fin()" in out, out

    def test_it_carries_the_IMPLEMENT_marker(self, project: Path):
        """Load-bearing, not decorative.

        `already_provides` reads it through `_is_method_stub` to tell jm's own
        stub from a built-in body (gh-994). A body spliced without one reads
        as hand-written, so a later `jm method` of the same name would decide
        a built-in already provides it and skip.
        """
        assert _cli("apply", cwd=project).returncode == 0
        body = _core_c(project).read_text(encoding="utf-8")
        assert "/* <<IMPLEMENT: fin >> */" in body, body

        from just_makeit._method import already_provides

        assert already_provides(project, "o", "o_fin", frozenset()) == ""

    def test_it_is_additive_and_idempotent(self, project: Path):
        """A second apply must not append the same body again."""
        assert _cli("apply", cwd=project).returncode == 0
        once = _core_c(project).read_bytes()
        r = _cli("apply", cwd=project)
        assert r.returncode == 0
        assert _core_c(project).read_bytes() == once
        assert "scaffolded" not in r.stdout

    def test_an_existing_body_is_never_touched(self, project: Path):
        """The sacred rule this sits inside: append only, never rewrite."""
        assert _cli("apply", cwd=project).returncode == 0
        path = _core_c(project)
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "    (void)state; (void)p;", "    state->n += p; /* MINE */"
            ),
            encoding="utf-8",
        )
        before = path.read_bytes()
        assert _cli("apply", cwd=project).returncode == 0
        assert path.read_bytes() == before


class TestStatusAgrees:
    """What `status` says, scoped to what it can currently say.

    It does NOT gate on "declared, called, never defined" — a check for that
    was built and withdrawn, because it fires on every property accessor the
    author has not implemented yet, which is a legitimate state for a project
    to be in and broke 89 tests. gh-1303 carries the measurement. `apply` is
    the surface that reports here, and it now fixes the case rather than
    reporting it.
    """

    def test_a_project_needing_the_splice_is_not_reported_clean(
        self, project: Path
    ):
        """Before `apply` has run, `status` must not call this up to date.

        It is the `.pyi` and the binding that make it non-clean, not the
        missing definition — which is exactly the gap gh-1303 is about.
        """
        assert _cli("status", "--check", cwd=project).returncode != 0

    def test_after_apply_it_is_clean(self, project: Path):
        assert _cli("apply", cwd=project).returncode == 0
        assert _cli("status", "--check", cwd=project).returncode == 0


@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="needs cmake and a C compiler")
class TestItActuallyLinks:
    """The claim in the issue's title, and the only test that can settle it.

    Every assertion above is about text. "Does not link" is not a property of
    text, and a fix that produced a plausible-looking body with the wrong
    signature would pass all of them.

    Driven through `jm test`, never `jm build` — gh-1109's lesson, whose wheel
    step fails on macOS runners.
    """

    def test_the_generated_project_builds_and_passes_its_own_suite(
        self, project: Path
    ):
        assert _cli("apply", cwd=project).returncode == 0
        r = _cli("test", cwd=project)
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]

    def test_without_the_body_it_does_not(self, project: Path):
        """The sabotage, as a test: prove the build was the thing at stake.

        Deleting the spliced definition and rebuilding must fail, or
        `test_the_generated_project_builds_...` above proves only that jm
        generates a project that builds — which it did before this fix too,
        right up until a method was declared in TOML.
        """
        import re

        assert _cli("apply", cwd=project).returncode == 0
        path = _core_c(project)
        text = path.read_text(encoding="utf-8")
        stripped = re.sub(
            r"/\* <<IMPLEMENT: fin >> \*/\nvoid\no_fin\(.*?\n\}\n",
            "",
            text,
            flags=re.S,
        )
        assert "o_fin" not in stripped, stripped
        path.write_text(stripped, encoding="utf-8")
        shutil.rmtree(project / "build", ignore_errors=True)
        assert _cli("test", cwd=project).returncode != 0


class TestTheDuplicateSymbolGuard:
    """gh-275: the body may legitimately live in a sibling source."""

    def test_a_definition_in_another_file_in_the_dir_is_found(
        self, project: Path
    ):
        """Read every `.c` the component owns, not just `_core.c`."""
        assert _cli("apply", cwd=project).returncode == 0
        path = _core_c(project)
        text = path.read_text(encoding="utf-8")
        i = text.index("/* <<IMPLEMENT: fin >> */")
        moved, rest = text[i:], text[:i]
        path.write_text(rest, encoding="utf-8")
        (path.parent / "o_extra_impl.c").write_text(
            '#include "o/o_core.h"\n' + moved, encoding="utf-8"
        )
        before = path.read_bytes()
        r = _cli("apply", cwd=project)
        assert r.returncode == 0, r.stdout + r.stderr
        # Not appended again — that would be a duplicate definition.
        assert path.read_bytes() == before
        assert "scaffolded" not in r.stdout, r.stdout

    def test_a_reformatted_signature_is_still_a_definition(
        self, project: Path
    ):
        """The near-miss that would have shipped a duplicate symbol.

        `_extract_c_function_bodies` wants `<returntype>\\n<name>(` on
        adjacent lines. Reading BOTH sides with it hides that, because both
        are blind identically — but a `c_style` project runs a formatter over
        `_core.c`, and the moment one side sees a definition the other does
        not, `apply` appends a SECOND one. Measured before the fix: joining
        `o_create`'s signature onto one line made `apply` write a duplicate
        `o_create`, and the project stopped linking.
        """
        assert _cli("apply", cwd=project).returncode == 0
        path = _core_c(project)
        text = path.read_text(encoding="utf-8")
        assert "o_state_t *\no_create(" in text, text
        path.write_text(
            text.replace("o_state_t *\no_create(", "o_state_t *o_create("),
            encoding="utf-8",
        )
        r = _cli("apply", cwd=project)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "scaffolded" not in r.stdout, r.stdout
        assert path.read_text(encoding="utf-8").count("o_create(") == 1

    def test_extra_cmake_sources_warn_instead_of_guessing(
        self, module_project: Path
    ):
        """When the symbol is nowhere jm can see and the CMake names sources
        it does not own, appending could define it twice. Say so instead.

        A MODULE object, deliberately. On the standalone path `apply`
        rewrites the object's `CMakeLists.txt` and strips the extra source
        before this check ever reads it -- which is its own bug (gh-1301) and
        would make this test pass for a reason unrelated to the guard.
        """
        root = module_project
        path = root / "native" / "src" / "o" / "o_core.c"
        _strip_fin(path)
        cmake = path.parent / "CMakeLists.txt"
        cmake.write_text(
            cmake.read_text(encoding="utf-8").replace(
                "add_library(o_core OBJECT o_core.c)",
                "add_library(o_core OBJECT o_core.c vendored.c)",
            ),
            encoding="utf-8",
        )
        r = _cli("apply", cwd=root)
        blob = r.stdout + r.stderr
        assert "does not define o_fin()" in blob, blob
        assert "vendored.c" in blob, blob
        assert "will not link" in blob, blob
        # And it did NOT append.
        assert "o_fin(" not in path.read_text(encoding="utf-8")

    # NOT tested here: that the warning above GATES `status --check`.
    # `status`'s oracle reads `<comp>_core.h` (see `undefined_core_symbols`),
    # and a MODULE object's method gets no prototype there at all -- gh-1302,
    # which is why this case cannot currently be gated. The standalone
    # object's undefined symbols are gated, and `TestStatusAgrees` covers it.
