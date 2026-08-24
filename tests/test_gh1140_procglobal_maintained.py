"""gh-1140: the generated process-global contract header is maintained.

gh-1134 corrected the module path a `process_global` rendezvous imports. It
reached the two places jm *generates* — the rendezvous spliced into every
generated `PyInit_`, and `_procglobal.render_header`'s render of the
`<COMP>_PG_OWNER` macro — and stopped there, because the header itself was
written once at scaffold time and never afterwards. `render_header` had
exactly two callers, both scaffolding (`_init.run`, `_object.run`); `apply`
did not reconcile the file and `status` did not compare it.

So upgrading fixed the generated modules and left the header naming the
package. A `no_generate` module — the case gh-1128 published this header
*for*, since a hand-written binding cannot read the names out of another
module's generated C — then adopted through the stale macro and failed at
import exactly as before. Measured in doppler at 0.67.2: `doppler.interrupt`
and every generated adopter imported, `doppler.buffer` and `doppler.stream`
raised `AttributeError: ... has no attribute '_jm_pg_dp_interrupt_guard'`,
and the suite took 100 collection errors under a pin bump whose entire
subject was that one line.

Both halves are tested here, and the second is the one that let the first
survive: a generated file carrying `DO NOT EDIT` that `apply` will not
restore and `status` will not compare is one no gate is holding. Clobbering
it outright reported clean.

Driven through the CLI against a tree built by running the tool, not against
a hand-written fixture — gh-1134's own end-to-end test asserted on generated
*text*, so the text was self-consistent and wrong about the world.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"

CLOBBER = "/* CLOBBERED */\n"


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


def _declare(root: Path, fragment: str, line: str) -> None:
    """Add *line* to an object fragment. `process_global` and a linking
    `depends_on` are manifest-only keys (gh-1117 / gh-225), so the manifest
    is where a test declares them too."""
    p = root / "objects" / f"{fragment}.toml"
    body = p.read_text(encoding="utf-8")
    anchor = 'no_step = "false"\n'
    assert anchor in body, body
    p.write_text(body.replace(anchor, anchor + line + "\n", 1), "utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """jm's real layout for the shape gh-1134/gh-1140 live in.

    `flag` declares `process_global` and lives in module `own`, so its
    extension is `pgdemo.own.own` behind a re-exporting `pgdemo/own/__init__`
    — the two names the stale macro conflated. `user`, in a second module,
    links `flag_core`, which is what makes the core shared and the rendezvous
    exist at all. A standalone `solo` is here so that a finding about a module
    object is visibly not a finding about every component.
    """
    assert _cli("new", "pgdemo", cwd=tmp_path).returncode == 0
    root = tmp_path / "pgdemo"
    for mod in ("own", "other"):
        assert _cli("module", mod, cwd=root).returncode == 0
    for comp, mod in (("flag", "own"), ("user", "other")):
        assert _cli("object", comp, "--module", mod, cwd=root).returncode == 0
    assert _cli("object", "solo", cwd=root).returncode == 0
    _declare(root, "flag", "process_global = true")
    _declare(root, "user", 'depends_on = [{ name = "flag", link = true }]')
    assert _cli("apply", cwd=root).returncode == 0
    baseline = _cli("status", "--check", cwd=root)
    assert baseline.returncode == 0, baseline.stdout
    return root


HEADER = "native/inc/flag/flag_procglobal.h"

# What the rendezvous must import: the EXTENSION module, not the package that
# re-exports it (gh-1134). The whole of gh-1140 is that this string reached
# the generated bindings and not the header beside them.
OWNER = '#define FLAG_PG_OWNER   "pgdemo.own.own"'


def _owner_line(root: Path) -> str:
    return next(
        ln
        for ln in (root / HEADER).read_text(encoding="utf-8").splitlines()
        if ln.startswith("#define FLAG_PG_OWNER")
    )


class TestApplyMaintainsTheHeader:
    def test_scaffold_names_the_extension_module(self, project: Path) -> None:
        """The premise: jm's own render is right, which is why nothing
        noticed that it never reached the file again."""
        assert _owner_line(project) == OWNER

    def test_a_stale_owner_macro_is_rewritten(self, project: Path) -> None:
        """gh-1140's own failure, exactly: the pre-gh1134 string in a header
        that already exists. This is what an adopting project carries after
        the pin bump."""
        p = project / HEADER
        p.write_text(
            p.read_text(encoding="utf-8").replace(
                '"pgdemo.own.own"', '"pgdemo.own"'
            ),
            encoding="utf-8",
        )
        assert _cli("status", "--check", cwd=project).returncode == 1
        assert _cli("apply", cwd=project).returncode == 0
        assert _owner_line(project) == OWNER
        assert _cli("status", "--check", cwd=project).returncode == 0

    def test_a_clobbered_header_is_restored(self, project: Path) -> None:
        """`DO NOT EDIT` is only true if something rewrites it."""
        (project / HEADER).write_text(CLOBBER, encoding="utf-8")
        assert _cli("status", "--check", cwd=project).returncode == 1
        assert _cli("apply", cwd=project).returncode == 0
        assert _owner_line(project) == OWNER

    def test_a_deleted_header_is_materialized(self, project: Path) -> None:
        """Not the same path as the two above — `_sync_missing` creates it,
        from bytes rendered against the whole manifest rather than whatever
        the replay held when it scaffolded this one component."""
        (project / HEADER).unlink()
        assert _cli("apply", cwd=project).returncode == 0
        assert _owner_line(project) == OWNER

    def test_disk_matches_what_jm_renders_today(self, project: Path) -> None:
        """The invariant, stated without the literal — whatever
        `render_header` says over this project's own manifest is what `apply`
        leaves on disk. gh-1140 is exactly this assertion failing: the render
        was right and nothing wrote it out.

        It also pins the assumption `_reconcile_procglobal_headers` rests on.
        It reconciles from the replayed tree rather than rendering a second
        time, which is right only while the scaffold-time render is the
        finished one; if that stops being true, this names it.
        """
        from just_makeit import _config, _procglobal

        cfg = _config.load(project)
        want = _procglobal.render_header(cfg, "flag")
        assert want, "the fixture no longer declares process_global"
        assert (project / HEADER).read_text(encoding="utf-8") == want

    def test_apply_converges(self, project: Path) -> None:
        """A reconcile whose two sides disagree reports drift forever, which
        is gh-635's failure and would make the gate above unclearable."""
        for _ in range(2):
            assert _cli("apply", cwd=project).returncode == 0
        out = _cli("apply", cwd=project)
        assert "already matches" in out.stdout, out.stdout
        assert _cli("status", "--check", cwd=project).returncode == 0


# The files whose content is the AUTHOR's, which `status` deliberately does not
# compare — its own help text names this category: "_core.c, your C and Python
# tests, README, pyproject.toml — which differ from their scaffold as soon as
# the project is real". Every entry below is one of those, with one exception
# that is not, and is filed rather than excused:
#
#   native/src/pgdemo_lib.c — gh-1141. Generated, no hand-owned half, and it
#   embeds `[project] version` in `pgdemo_version()`. Bump the manifest and
#   the C keeps returning the old string forever, with `--check` clean.
#
# An exact set, not a pattern list, and that is the point in both directions:
# a newly generated file that no gate holds fails this, and a file that BECOMES
# visible fails it too, so the list can only be shortened deliberately.
YOURS = {
    "README.md",
    "benchmarks/history/.gitkeep",
    "docs/api.md",
    "docs/index.md",
    "native/inc/flag/flag_core.h",
    "native/inc/other/other_core.h",
    "native/inc/own/own_core.h",
    "native/inc/solo/solo_core.h",
    "native/inc/user/user_core.h",
    "native/src/flag/flag_core.c",
    "native/src/other/other_core.c",
    "native/src/own/own_core.c",
    "native/src/pgdemo_lib.c",
    "native/src/solo/solo_core.c",
    "native/src/user/user_core.c",
    "native/tests/test_flag_core.c",
    "native/tests/test_solo_core.c",
    "native/tests/test_user_core.c",
    "pyproject.toml",
    "src/pgdemo/benchmarks/__init__.py",
    "src/pgdemo/benchmarks/bench_solo.py",
    "src/pgdemo/other/benchmarks/__init__.py",
    "src/pgdemo/other/benchmarks/bench_user.py",
    "src/pgdemo/other/tests/__init__.py",
    "src/pgdemo/other/tests/test_user.py",
    "src/pgdemo/own/benchmarks/__init__.py",
    "src/pgdemo/own/benchmarks/bench_flag.py",
    "src/pgdemo/own/tests/__init__.py",
    "src/pgdemo/own/tests/test_flag.py",
    "src/pgdemo/tests/__init__.py",
    "src/pgdemo/tests/test_solo.py",
}


class TestNothingGeneratedIsInvisibleToStatus:
    """The half of gh-1140 that let the other half survive a release.

    A stale line is a bug; a file no gate is holding is what turns a bug into
    one that outlives its own fix. So the gate is not "is this header
    compared" — that is the instance, and a note in a docstring saying to
    remember the next one is the control this repo has already watched fail.
    It is: **clobber every file in a real project and demand `status` notice**,
    with a named exemption for the ones whose content is the author's.

    Registration-free in the direction that matters. A file jm learns to
    generate is covered on the day it is generated, with no list to update —
    the list only grows when someone deliberately adds a file whose content is
    the author's, which is a decision worth making in a diff.
    """

    def test_status_sees_every_file_that_is_jms(self, project: Path) -> None:
        base = _cli("status", cwd=project).stdout
        invisible = set()
        for path in sorted(project.rglob("*")):
            if not path.is_file() or "build" in path.parts:
                continue
            rel = path.relative_to(project).as_posix()
            if rel == "just-makeit.toml" or rel.startswith("objects/"):
                continue  # the manifest is the input, not an artefact
            keep = path.read_bytes()
            path.write_text(CLOBBER, encoding="utf-8")
            try:
                if _cli("status", cwd=project).stdout == base:
                    invisible.add(rel)
            finally:
                path.write_bytes(keep)
        assert invisible == YOURS
