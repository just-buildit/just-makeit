"""gh-1089: the generated project's own pytest suite is now a PR gate.

`docker/build_examples.py` was the only place in the repo that ran a generated
project's own test suite — the artefact a *user* gets, as opposed to the
walkthrough steps that produced it. It is not a required check. It was red on
`main` for **14 consecutive runs**, every PR gate stayed green, and the release
PR was the first thing to stop, because `release.yml` rebuilds the images on a
tag.

    | gate                 | runs                              | caught it |
    | make test            | unit suite, examples excluded      | no        |
    | make test-examples   | each example's walkthrough steps   | no        |
    | docker.yml           | the walkthrough PLUS generated pytest | yes    |

A gate that cannot fail anything is indistinguishable from no gate. This one
*did* fail, loudly, fourteen times, into a place nobody is required to look.

The fix is the issue's option 2: run it where the other example gates run.
`run_generated_pytest` **moved** into the package rather than being copied, so
there is one implementation and two callers — a second copy is exactly how the
two `.pyi` writers and the three `_py_default`s drifted.

Measured after wiring, by running every example rather than grepping:
**20 of 26** examples now gate at least one real generated test, **207
assertions** in total. `TestTheGateIsLive` keeps that honest — a gate whose
every case skips is the failure being fixed, one layer in.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._build import run_generated_pytest  # noqa: E402

_DOCKER = Path(__file__).parent.parent / "docker"


def _project(root: Path, body: str, *, ext: bool = True) -> Path:
    """A minimal tree shaped like a built generated project."""
    proj = root / "proj"
    tests = proj / "src" / "pkg" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_it.py").write_text(textwrap.dedent(body), encoding="utf-8")
    if ext:
        # `run_generated_pytest` gates on a compiled extension being present;
        # its content is irrelevant, only that the build produced one.
        (proj / "src" / "pkg" / "mod.so").write_bytes(b"")
    return proj


class TestItFailsWhenTheGeneratedSuiteFails:
    def test_a_failing_generated_test_is_reported(self, tmp_path):
        proj = _project(
            tmp_path,
            """
            def test_x():
                assert False
            """,
        )
        assert run_generated_pytest(proj) is False

    def test_a_passing_generated_test_is_accepted(self, tmp_path):
        proj = _project(
            tmp_path,
            """
            def test_x():
                assert True
            """,
        )
        assert run_generated_pytest(proj) is True


class TestTheThreeThingsThatAreNotFailures:
    """Each is a shape, not a defect, and failing them would make the gate
    report "the generated tests fail" for a project whose tests were never
    the question."""

    def test_no_src_directory(self, tmp_path):
        (tmp_path / "empty").mkdir()
        assert run_generated_pytest(tmp_path / "empty") is True

    def test_an_unbuilt_scaffold_is_skipped(self, tmp_path):
        """No compiled extension means nothing can import, so a failure here
        would be about the build, not the tests."""
        proj = _project(
            tmp_path,
            """
            def test_x():
                assert False
            """,
            ext=False,
        )
        assert run_generated_pytest(proj) is True

    def test_no_tests_collected_is_not_a_failure(self, tmp_path):
        """pytest exit 5. A functions-only module builds an extension and
        generates no pytest suite."""
        proj = _project(tmp_path, "# no tests here\n")
        assert run_generated_pytest(proj) is True


class TestThereIsOnlyOneImplementation:
    """The registration-free guard against the copy coming back.

    `docker/build_examples.py` carried the only implementation, which is how
    it ended up in a place no PR blocks on. Re-adding a local copy there would
    silently re-create that: the docker check and the PR check could then
    disagree about what "the generated tests pass" means.
    """

    def test_docker_imports_it_rather_than_defining_it(self):
        src = (_DOCKER / "build_examples.py").read_text(encoding="utf-8")
        assert "def _run_pytest" not in src, (
            "docker/build_examples.py defines its own _run_pytest again — "
            "import run_generated_pytest from just_makeit._build instead"
        )
        assert "from just_makeit._build import run_generated_pytest" in src

    def test_the_example_gate_actually_calls_it(self):
        """The CALL, parsed — not the name mentioned somewhere in the file.

        Grepping for `run_generated_pytest` passes on the import line alone,
        so it stayed green with the call deleted from `test_example` — which
        is the entire fix. Measured by sabotage; `feedback-anchor-the-match`
        is the general form.
        """
        import ast

        tree = ast.parse(
            (Path(__file__).parent / "test_examples.py").read_text(
                encoding="utf-8"
            )
        )
        fn = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "test_example"
        )
        called = {
            n.func.id
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "_generated_pytest_check" in called, (
            "test_example no longer runs the generated project's own pytest "
            "suite — that call IS gh-1089's fix"
        )

    def test_the_check_runs_the_shared_primitive(self):
        """...and that the check is not a stub that always passes."""
        import ast

        tree = ast.parse(
            (Path(__file__).parent / "test_examples.py").read_text(
                encoding="utf-8"
            )
        )
        fn = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef)
            and n.name == "_generated_pytest_check"
        )
        called = {
            n.func.id
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "run_generated_pytest" in called


class TestTheGateIsLive:
    """A gate that skips every case is the bug being fixed, one layer in.

    Running all 26 examples here would take many minutes, so this asserts the
    property on one known-built example rather than re-measuring the fleet.
    The fleet number is in this module's docstring and was measured by
    running, not grepping — `feedback_measure_coverage_by_running`.
    """

    def test_a_real_example_actually_runs_generated_tests(self, tmp_path):
        import contextlib
        import importlib.util
        import io

        spec = importlib.util.spec_from_file_location(
            "te", Path(__file__).parent / "test_examples.py"
        )
        te = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(te)
        if te._SKIP:
            import pytest

            pytest.skip(te._SKIP)
        d = [p for p in te._discover_examples() if p.name == "fir_filter"][0]
        with contextlib.redirect_stdout(io.StringIO()):
            te._load_run(d)(tmp_path)
        proj = next(tmp_path.rglob("just-makeit.toml")).parent
        assert list(proj.rglob("*.so")), "example produced no extension"
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(proj / "src"),
                "-q",
                "--no-header",
            ],
            cwd=str(proj),
            env={**__import__("os").environ, "PYTHONPATH": str(proj / "src")},
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert " passed" in r.stdout, r.stdout
        assert "0 passed" not in r.stdout, r.stdout
