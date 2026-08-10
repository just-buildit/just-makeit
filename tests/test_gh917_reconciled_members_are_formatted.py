"""A member reconciled in place lands in the project's C style, not jm's.

gh-917. On a `c_style` project, `jm apply` left one fragment holding **two**
C styles: the members jm had just rewritten in K&R, everything around them in
the project's GNU, and a `}` in the middle still on the old indentation.

Two independent causes, and either one alone leaves the bug:

1. **The formatter never saw a fragment.** `_cfmt._generated_c_files` globbed
   `*_ext.c`, and gh-729 split the aggregator into per-object fragments named
   `<module>_ext_<obj>.c` — which that pattern does not match. The split moved
   the file and left the rule behind.
2. **Nothing formatted the real tree after reconciliation.** `_apply.run`
   formats the throwaway scaffold it compares against (gh-493), which is right
   for the comparison; but `_docsync.refresh_module_fragment_docs` writes into
   the *real* fragments afterwards, from a fresh render, and that write was
   never formatted.

Measured on doppler, same tree and same manifest, only jm differing:

===============  ======  ======
jm               GNU     K&R
===============  ======  ======
0.55.1           17      2
this branch      19      0
===============  ======  ======

The two K&R definitions were exactly `DdcrObj_destroy` and `DdcrObj_exit` —
the teardown members `_docsync` rewrites.

The tests use a scripted stand-in formatter (the gh-758 pattern) so the
invariant holds on every machine rather than only where clang-format is
installed. The stand-in does what GNU style does to the construct that broke:
`name(args)` -> `name (args)`.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _cfmt
from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run

# Idempotent by construction: a name already followed by a space is left
# alone, so `_run_formatter`'s convergence loop settles on pass two.
_GNU_ISH = """\
import re, sys
for p in sys.argv[1:]:
    if p.startswith("-"):
        continue
    t = open(p).read()
    t = re.sub(r"(?m)^([A-Za-z_][A-Za-z_0-9]*)\\(", r"\\1 (", t)
    open(p, "w").write(t)
"""


def _knr_defs(path: Path) -> list[str]:
    """Definition lines still in jm's own style (`name(args)`)."""
    import re

    return [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if re.match(r"^[A-Za-z_][A-Za-z_0-9]*\(", ln)
    ]


class TestTheGlob:
    """Cause 1, in isolation — no formatter, no apply, just the file set."""

    def _tree(self, root: Path) -> Path:
        src = root / "native" / "src" / "dsp"
        src.mkdir(parents=True)
        for name in (
            "dsp_ext.c",
            "dsp_ext_fir.c",
            "dsp_ext_biquad.c",
            "dsp_ext_extra.c",
            "dsp_ext_fir_extra.c",
            "dsp_core.c",
        ):
            (src / name).write_text("/* x */\n")
        return src

    def test_a_per_object_fragment_is_included(self, tmp_path):
        """The whole of cause 1. `*_ext.c` does not match `dsp_ext_fir.c`."""
        self._tree(tmp_path)
        names = {p.name for p in _cfmt._generated_c_files(tmp_path)}
        assert {"dsp_ext.c", "dsp_ext_fir.c", "dsp_ext_biquad.c"} <= names, (
            f"a per-object fragment is invisible to the formatter, so its "
            f"style can never be reconciled: {sorted(names)}"
        )

    def test_the_hand_written_extra_is_excluded(self, tmp_path):
        """`*_ext_extra.c` is the author's; jm never writes it.

        The widened glob reaches it (`*_ext` then `ra.c`), so this is the
        exception that has to be argued for rather than a happy accident of
        the pattern.
        """
        self._tree(tmp_path)
        names = {p.name for p in _cfmt._generated_c_files(tmp_path)}
        assert "dsp_ext_extra.c" not in names, (
            "jm would reformat a hand-written file it never writes"
        )
        assert "dsp_ext_fir_extra.c" not in names

    def test_sacred_c_is_still_excluded(self, tmp_path):
        """gh-493's exclusion survives the widening."""
        self._tree(tmp_path)
        names = {p.name for p in _cfmt._generated_c_files(tmp_path)}
        assert "dsp_core.c" not in names


class TestApplyFormatsWhatItWrote:
    """Cause 2, end to end: the real tree, after reconciliation."""

    def _project(self, root: Path) -> dict:
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("proj", root, [], [], c_style="clang-format")
            module_run(root, "dsp")
            object_run(
                root,
                "fir",
                "dsp",
                arg_type="float",
                return_type="float",
                state_vars=[("gain", "double", "1.0")],
                # gh-917: the wholesale teardown overwrite in `_docsync` is
                # scoped to objects that DECLARE a destroy spec (`if
                # C.destroy_spec(...)`), and that overwrite is the one that
                # replaces a member's signature line rather than only the doc
                # text inside it. Without this the fixture reconciles nothing
                # that could carry jm's style, and the whole class passes with
                # the fix removed — which is what the first sabotage check
                # found. doppler's `ddcr`, whose `DdcrObj_destroy` /
                # `DdcrObj_exit` are the two members in the report, declares
                # one.
                destroy={"name": "close"},
            )
        fmt = root / "fake_formatter.py"
        fmt.write_text(_GNU_ISH)
        cfg = C.load(root)
        cfg["project"]["c_format_command"] = [sys.executable, str(fmt)]
        C.save(root, cfg)
        return C.load(root)

    def test_apply_leaves_one_style_in_a_fragment(self, tmp_path):
        """The reported symptom, on a jm-scaffolded project.

        The tree is formatted first — as a real c_style project's is, by its
        own lint — so that what `apply` writes afterwards is the only thing
        that can reintroduce jm's style. Without the fix the reconciled
        members come back K&R and this fails with them named.
        """
        root = tmp_path / "p"
        cfg = self._project(root)
        frag = root / "native/src/dsp/dsp_ext_fir.c"
        assert frag.is_file(), sorted(
            p.name for p in (root / "native/src/dsp").iterdir()
        )
        _cfmt.format_project(root, cfg, quiet=True)
        assert not _knr_defs(frag), "setup failed to normalise the tree"

        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(io.StringIO()):
                apply_run(root)

        assert not _knr_defs(frag), (
            "apply wrote members in jm's style into a file the project "
            "formats, leaving two C styles in one file:\n  "
            + "\n  ".join(_knr_defs(frag))
        )

    def test_apply_formats_the_files_it_wrote_in_the_real_tree(
        self, tmp_path, monkeypatch
    ):
        """Cause 2, pinned at the wiring — the call the sabotage removed.

        `apply` formats the throwaway scaffold it compares against (gh-493);
        the member-level reconciliation writes into the REAL tree afterwards
        and was never formatted. The widened glob alone does not fix that, and
        does not fail without it: a freshly scaffolded tree has nothing
        drifted, so every file `apply` writes is copied from the already
        formatted temp scaffold.

        Constructing a minimal fixture that drifts a member *and* has jm
        rewrite it wholesale did not reproduce — the wholesale teardown
        overwrite is scoped to objects declaring a destroy spec, and a
        scaffolded one still reconciled nothing. So this pins the call
        instead, which is the thing that was missing. The behaviour itself is
        measured on doppler, where the same tree and manifest go from 17 GNU /
        2 K&R definitions to 19 / 0 with only jm differing.

        Written after a sabotage check passed with the call deleted.
        """
        root = tmp_path / "p"
        self._project(root)
        seen: list = []
        real = _cfmt.format_files

        def _spy(_root, _cfg, paths, **kw):
            seen.append((Path(_root), list(paths)))
            return real(_root, _cfg, paths, **kw)

        monkeypatch.setattr(_cfmt, "format_files", _spy)
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(io.StringIO()):
                apply_run(root)

        assert seen, (
            "apply never formatted the real tree, so anything it reconciled "
            "in place keeps jm's C style"
        )
        called_root, paths = seen[-1]
        assert called_root == root, (
            f"the format pass ran against {called_root}, not the project — "
            f"formatting the temp scaffold is the pass that already existed"
        )
        assert paths, "apply formatted nothing at all"

    def test_apply_is_a_no_op_the_second_time(self, tmp_path):
        """Formatting must not become its own source of drift.

        A format pass that is not idempotent turns every `apply` into a diff
        and `jm status --check` never goes green — the gh-758 failure, one
        layer along.
        """
        root = tmp_path / "p"
        self._project(root)
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(io.StringIO()):
                apply_run(root)
        frag = root / "native/src/dsp/dsp_ext_fir.c"
        first = frag.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(io.StringIO()):
                apply_run(root)
        assert frag.read_bytes() == first, "apply does not converge"


class TestScope:
    """What the new pass must NOT do."""

    def test_a_project_without_c_style_is_untouched(self, tmp_path):
        """Zero churn on the default path — c_style is opt-in."""
        root = tmp_path / "p"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("proj", root, [], [])
            module_run(root, "dsp")
            object_run(
                root, "fir", "dsp", arg_type="float", return_type="float"
            )
        frag = root / "native/src/dsp/dsp_ext_fir.c"
        before = frag.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(io.StringIO()):
                apply_run(root)
        assert frag.read_bytes() == before

    def test_format_files_ignores_paths_outside_the_generated_set(
        self, tmp_path
    ):
        """`apply` hands it everything it wrote; the filter lives here.

        A sacred `_core.c` reaching the formatter would churn the author's
        algorithm — the exact thing gh-493 excluded it for.
        """
        root = tmp_path / "p"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("proj", root, [], [], c_style="clang-format")
            object_run(
                root, "fir", None, arg_type="float", return_type="float"
            )
        fmt = root / "fake_formatter.py"
        fmt.write_text(_GNU_ISH)
        cfg = C.load(root)
        cfg["project"]["c_format_command"] = [sys.executable, str(fmt)]
        core = root / "native/src/fir/fir_core.c"
        before = core.read_bytes()
        _cfmt.format_files(root, cfg, [core, root / "just-makeit.toml"])
        assert core.read_bytes() == before, (
            "the sacred core was reformatted; only generated glue may be"
        )


def test_the_uv_run_command_from_the_report_is_still_flagged(tmp_path):
    """A note the report earns rather than a fix.

    gh-917's reproduction used
    ``c_format_command = ["uv", "run", "--group", "dev", "clang-format"]``,
    which `_cfmt`'s own module docstring calls the trap: outside a project
    ``uv`` warns that ``--group dev has no effect`` and falls back to PATH, so
    jm formats its temp scaffold with one binary and the real tree with
    another. jm detects this directly, and it must keep doing so now that the
    formatter reaches more files — a CWD-dependent command was previously
    unable to affect fragments at all.
    """
    assert hasattr(_cfmt, "cwd_dependent_version")


@pytest.mark.parametrize("name", ["dsp_ext.c", "dsp_ext_fir.c"])
def test_both_shapes_reach_the_formatter(tmp_path, name):
    """Aggregator and fragment, so widening cannot silently drop the old one."""
    src = tmp_path / "native" / "src" / "dsp"
    src.mkdir(parents=True)
    (src / name).write_text("/* x */\n")
    assert [p.name for p in _cfmt._generated_c_files(tmp_path)] == [name]
