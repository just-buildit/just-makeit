"""gh-1033: a stand-down must not read as a clean tree.

`_hollow.orphans()` returned `list[Orphan]`, and returned `[]` for two
answers that are not the same answer: "I read every build file and nothing is
unbuilt", and "a build file globs its sources, so I could not tell". The
gh-806 `UNBUILT` gate consumes that list, so it reported **clean** in both
cases.

That is the gh-806 failure one layer out. The whole reason `UNBUILT` gates
rather than notes is that its failure mode is a *green CI run*; a stand-down
reported as clean reproduces exactly that silence inside the detector written
to break it. gh-1032 removed the common cause — a vendored `file(GLOB)` no
longer stands the whole tree down — but not the silence, and what survives is
sharper: a wildcard that genuinely could compile the scanned sources, which is
precisely the tree where a reader most needs telling the gate did not run.

`built_stems` has had the right shape since gh-1023 (`None` for "could not
tell", which is what lets `jm bench` print its `note` line instead of quietly
running fewer benchmarks). This copies that shape rather than inventing a
second one, so the pair that already share a scanner now also share what
"could not tell" looks like.

The gate at the bottom is the registration-free half. It does not name
`orphans`: it walks `_hollow` for **any** function whose body reaches
`_build_texts`, and requires each one to be able to say it stood down. A
scanner added later is covered with no edit here — which is the point, since
this bug is what a second scanner sharing the first one's blindness looks
like.
"""

from __future__ import annotations

import ast
import contextlib
import inspect
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _hollow  # noqa: E402
from just_makeit import _status  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "proj"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("proj", dest)
        object_run(
            dest,
            "frame",
            module=None,
            arg_type="float",
            return_type="float",
            state_vars=[("g", "double", "1.0")],
        )
    return dest


def _glob(project, where="native/benchmarks"):
    """Put a wildcard where the scan reads it, so `_build_texts` stands down.

    Deliberately NOT under `vendor/` — gh-1031 made jm decline to read that,
    which is the case gh-1032 removed. What is left, and what this exercises,
    is a glob in the project's own tree.
    """
    d = project / where
    d.mkdir(parents=True, exist_ok=True)
    (d / "CMakeLists.txt").write_text(
        'file(GLOB SOURCES "*.c")\nadd_library(x ${SOURCES})\n',
        encoding="utf-8",
    )


def _orphan(project, stem="ghost"):
    (project / f"native/benchmarks/bench_{stem}_core.c").write_text(
        "int main(void) { return 0; }\n", encoding="utf-8"
    )


class TestTheTwoAnswersAreDistinguishable:
    def test_a_clean_tree_is_the_empty_list(self, project):
        assert _hollow.orphans(project, C.load(project)) == []

    def test_a_stand_down_is_none(self, project):
        """The fix, at its narrowest. `[]` here was the bug."""
        _glob(project)
        assert _hollow.orphans(project, C.load(project)) is None

    def test_a_stand_down_is_none_even_with_an_orphan_present(self, project):
        """`None` is not "no findings under a glob" — it is "did not look".

        Worth its own case: an implementation that scanned anyway and merely
        annotated the result would pass the test above and still be wrong,
        because the scan's answer over a wildcard build file is not
        trustworthy in either direction.
        """
        _glob(project)
        _orphan(project)
        assert _hollow.orphans(project, C.load(project)) is None

    def test_it_matches_its_sibling(self, project):
        """`built_stems` is the shape being copied; assert they agree.

        The pair share `_build_texts`, so they stand down together or not at
        all. A future change that gives one of them a reason to stand down
        alone should have to say so here.
        """
        assert _hollow.built_stems(project, "bench") is not None
        assert _hollow.orphans(project, C.load(project)) is not None
        _glob(project)
        assert _hollow.built_stems(project, "bench") is None
        assert _hollow.orphans(project, C.load(project)) is None


def _status_text(project, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _status.run(project, **kw)
    return buf.getvalue()


class TestTheReaderIsTold:
    """The distinction is only worth having if it reaches the output.

    gh-1033's title is about `orphans()`, but the cost is paid in `jm status`:
    a tree that was never scanned printed no UNBUILT section and an `OK — up
    to date` line, which is byte-for-byte what a scanned, clean tree prints.
    """

    def test_a_clean_tree_says_nothing_about_the_scan(self, project):
        out = _status_text(project)
        assert "UNCHECKED" not in out
        assert "unbuilt not checked" not in out

    def test_a_stand_down_prints_an_unchecked_section(self, project):
        _glob(project)
        out = _status_text(project)
        assert "UNCHECKED" in out
        # Name the mechanism, not just the fact — a reader who does not know
        # what stood the scan down cannot put the tree back under the gate.
        assert "wildcard" in out

    def test_the_ok_line_is_qualified(self, project):
        """gh-767's rule: do not say "up to date" over an unrun check.

        The `OK` line is what a reader takes away, and over a globbed tree it
        was claiming a property jm had not checked.
        """
        _glob(project)
        out = _status_text(project)
        assert "OK — up to date" in out
        assert "unbuilt not checked" in out

    def test_the_check_summary_is_qualified(self, project):
        """`--check` prints the one-line summary, and it needs it too."""
        _glob(project)
        _orphan(project)  # force the `summary:` branch, not the OK branch
        out = _status_text(project, check=True)
        assert "unbuilt not checked" in out

    def test_json_carries_the_flag(self, project):
        """A CI consumer reads the JSON, and `unbuilt_sources: []` alone is
        the same ambiguity in machine-readable form."""
        import json

        clean = json.loads(_status_text(project, as_json=True))
        assert clean["unbuilt_scanned"] is True
        _glob(project)
        stood_down = json.loads(_status_text(project, as_json=True))
        assert stood_down["unbuilt_scanned"] is False
        assert stood_down["unbuilt_sources"] == []


class TestApplySaysItToo:
    """`jm apply` reports the same finding through `_hollow.report`.

    It printed nothing over a globbed tree — which is also what it prints
    over a clean one, so the one command that runs on every change carried
    the ambiguity too.
    """

    def test_report_is_silent_on_a_clean_tree(self, project):
        buf = io.StringIO()
        _hollow.report(project, C.load(project), stream=buf)
        assert buf.getvalue() == ""

    def test_report_names_the_stand_down(self, project):
        _glob(project)
        buf = io.StringIO()
        _hollow.report(project, C.load(project), stream=buf)
        out = buf.getvalue()
        assert "did not run" in out
        assert "NOT a clean result" in out

    def test_the_stand_down_is_advisory_not_gating(self, project):
        """gh-767: a gate must name a command that clears it.

        `jm apply` cannot rewrite the project's wildcard, so marking this as
        gating would teach the reader to ignore the mark — the exact failure
        `_report`'s two weights exist to avoid.
        """
        from just_makeit import _report

        _glob(project)
        _report.reset()
        _hollow.report(project, C.load(project), stream=io.StringIO())
        assert _report.gating_count() == 0


class TestEveryScannerCanSayItStoodDown:
    """The registration-free gate.

    Nothing here names `orphans`. `_build_texts` is the one place that decides
    "I cannot answer by reading", and it says so by returning `None`; every
    function that calls it therefore holds an answer it must be able to pass
    on. A scanner that cannot represent the stand-down will swallow it, and
    swallowing it is this bug.

    Written as a scan of the module rather than a list of function names for
    the reason the repo keeps rediscovering: a list you must remember to
    append to fails the same silent way the bug does.
    """

    @staticmethod
    def _callers_of_build_texts() -> list[str]:
        tree = ast.parse(inspect.getsource(_hollow))
        out = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "_build_texts"
                ):
                    out.append(node.name)
                    break
        return sorted(out)

    def test_the_gate_is_armed(self):
        """A scan that finds nothing to check is indistinguishable from a
        scan that passes — so assert it found the population it exists for."""
        callers = self._callers_of_build_texts()
        assert "orphans" in callers
        assert "built_stems" in callers

    def test_each_one_can_return_none(self):
        found = self._callers_of_build_texts()
        cannot = []
        for name in found:
            fn = getattr(_hollow, name)
            ann = inspect.signature(fn).return_annotation
            # Annotations are strings here (`from __future__ import
            # annotations`), so this is a text test on purpose — the
            # alternative is resolving forward refs for no extra strictness.
            if "None" not in str(ann):
                cannot.append(f"{name} -> {ann}")
        assert not cannot, (
            "these read `_build_texts`, which returns None for 'a build file "
            "globs its sources, so I cannot tell', but cannot pass that "
            "answer on — so a tree they never scanned is reported the same "
            f"way as a tree they scanned and found clean (gh-1033): {cannot}"
        )
