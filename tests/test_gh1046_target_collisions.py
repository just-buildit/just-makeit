"""gh-1046: jm must not emit a CMake target name the project already declares.

A CMake target name is **global**. Two ``add_executable`` calls with the same
name are a hard configure error, not a shadow and not an override. jm emits
target names from two places and both asked what the *manifest* claims,
never what the project's own ``CMakeLists.txt`` declares:

* gh-1034 gave a function-only module a ``test_``/``bench_<cname>_core`` pair.
  Every consumer had hand-registered that pair *because jm did not generate
  it* (the gh-1023 workaround) — so the feature that removes the need for the
  workaround collided with the workaround instead of replacing it, and only
  the projects it was written for were broken by it. doppler could not
  configure at all.
* ``jm app --name X`` already suffixed a colliding target, but from a
  manifest-derived list, so a hand-written ``add_executable(myapp ...)`` was
  invisible and it emitted a second ``myapp`` beside it.

One question, answered once in :mod:`just_makeit._targets`. The manifest half
had also gone stale on its own terms — it listed ``test_<comp>_core`` and not
``bench_<comp>_core``, which jm has emitted beside it all along — which is the
argument for the two halves living together rather than apart.
"""

from __future__ import annotations

import io
import contextlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _render as R  # noqa: E402
from just_makeit import _targets  # noqa: E402
from just_makeit._app import run as app_run  # noqa: E402
from just_makeit._function import run as function_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _quiet(fn, *a, **kw):
    """Run a generator command without its progress output."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _fn_module_project(tmp_path: Path) -> Path:
    """A project whose `util` module is function-only (the gh-1034 shape)."""
    root = tmp_path / "demo"
    _quiet(new_run, "demo", root)
    _quiet(module_run, root, "util")
    _quiet(
        function_run,
        root,
        "clamp",
        "util",
        params=[("x", "double")],
        out_type="double",
    )
    return root


def _hand_register(root: Path, target: str) -> None:
    """What a project wrote itself, before jm generated the target."""
    cmake = root / "CMakeLists.txt"
    cmake.write_text(
        cmake.read_text(encoding="utf-8")
        + f"\n# hand-registered (the gh-1023 workaround)\n"
        f"add_executable({target} native/benchmarks/{target}.c)\n",
        encoding="utf-8",
    )


class TestDeclaredInCmake:
    """What the PROJECT claims, read from the one file it writes in."""

    def test_finds_a_hand_written_target(self, tmp_path):
        (tmp_path / "CMakeLists.txt").write_text(
            "add_executable(bench_util_core native/benchmarks/b.c)\n"
            "  add_executable( spaced native/x.c )\n",
            encoding="utf-8",
        )
        assert _targets.declared_in_cmake(tmp_path) == frozenset(
            {"bench_util_core", "spaced"}
        )

    def test_a_missing_cmakelists_is_empty_not_an_error(self, tmp_path):
        assert _targets.declared_in_cmake(tmp_path / "nope") == frozenset()

    def test_jms_own_app_block_is_not_a_project_claim(self, tmp_path):
        """Otherwise `jm app` would find its own target and suffix it.

        The block is jm's to rewrite on every run, so the target inside it is
        not someone else's claim -- reading it as one turns an idempotent
        command into `myapp_app`, then `myapp_app_app`.
        """
        (tmp_path / "CMakeLists.txt").write_text(
            "add_executable(theirs native/x.c)\n"
            f"{_targets.APP_CMAKE_SENTINEL}────────\n"
            "add_executable(myapp native/src/app/myapp.c)\n"
            f"{_targets.APP_CMAKE_END}────────\n",
            encoding="utf-8",
        )
        assert _targets.declared_in_cmake(tmp_path) == frozenset({"theirs"})


class TestFromManifest:
    """What jm itself will emit -- the half that had gone stale."""

    def test_a_components_bench_target_is_listed(self, tmp_path):
        """`bench_<comp>_core` was missing while jm emitted it.

        Pinned against the file jm actually writes, not against a copy of the
        list, so the two cannot drift apart again.
        """
        root = tmp_path / "demo"
        _quiet(new_run, "demo", root)
        _quiet(
            object_run,
            root,
            "w",
            None,
            state_vars=[("g", "float", "1.0f")],
            arg_type="float",
            return_type="float",
        )
        emitted = _targets.declared_in_cmake(root / "native" / "src" / "w")
        assert "bench_w_core" in emitted, "jm no longer emits this target"
        assert emitted <= _targets.from_manifest(C.load(root)), (
            f"jm emits {sorted(emitted)} and claims "
            f"{sorted(_targets.from_manifest(C.load(root)))}"
        )

    def test_a_function_modules_pair_is_listed(self, tmp_path):
        root = _fn_module_project(tmp_path)
        claimed = _targets.from_manifest(C.load(root))
        assert {"test_util_core", "bench_util_core"} <= claimed


class TestModuleTargets:
    """gh-1034's pair, against a project that already registered it."""

    def test_the_colliding_target_is_not_emitted(self, tmp_path):
        root = _fn_module_project(tmp_path)
        _hand_register(root, "bench_util_core")
        _quiet(
            function_run,
            root,
            "scale",
            "util",
            params=[("x", "double")],
            out_type="double",
        )
        mod_cmake = (
            root / "native" / "src" / "util" / "CMakeLists.txt"
        ).read_text(encoding="utf-8")
        assert "add_executable(bench_util_core" not in mod_cmake

    def test_the_other_target_is_still_emitted(self, tmp_path):
        """Skipped per target, not per pair."""
        root = _fn_module_project(tmp_path)
        _hand_register(root, "bench_util_core")
        _quiet(
            function_run,
            root,
            "scale",
            "util",
            params=[("x", "double")],
            out_type="double",
        )
        mod_cmake = (
            root / "native" / "src" / "util" / "CMakeLists.txt"
        ).read_text(encoding="utf-8")
        assert "add_executable(test_util_core" in mod_cmake

    def test_jm_does_not_read_its_own_module_cmakelists(self, tmp_path):
        """The scan is scoped to the ROOT, and this is why.

        jm owns `native/src/<cname>/CMakeLists.txt` and writes these targets
        there. A scan that included it would find jm's own emission, read it
        as a claim by someone else and stop emitting -- so the target would
        survive one command and vanish on the next.
        """
        root = _fn_module_project(tmp_path)
        mod_cmake = root / "native" / "src" / "util" / "CMakeLists.txt"
        assert "add_executable(bench_util_core" in mod_cmake.read_text(
            encoding="utf-8"
        )
        for name in ("scale", "offset"):
            _quiet(
                function_run,
                root,
                name,
                "util",
                params=[("x", "double")],
                out_type="double",
            )
            assert "add_executable(bench_util_core" in mod_cmake.read_text(
                encoding="utf-8"
            ), f"the target vanished after adding {name}()"

    def test_the_stand_down_is_reported(self, tmp_path, capsys):
        """A silent skip reads as 'jm generates this now' when it does not."""
        root = _fn_module_project(tmp_path)
        _hand_register(root, "bench_util_core")
        capsys.readouterr()
        _quiet(
            function_run,
            root,
            "scale",
            "util",
            params=[("x", "double")],
            out_type="double",
        )
        err = capsys.readouterr().err
        assert "bench_util_core" in err
        assert "CMakeLists.txt" in err
        # Named singly: the test target was NOT skipped and must not be
        # implied by a message about "the pair".
        assert "test_util_core" not in err

    def test_a_clean_project_still_gets_both(self, tmp_path):
        """The guard must not disarm the feature it guards."""
        root = _fn_module_project(tmp_path)
        mod_cmake = (
            root / "native" / "src" / "util" / "CMakeLists.txt"
        ).read_text(encoding="utf-8")
        assert "add_executable(test_util_core" in mod_cmake
        assert "add_executable(bench_util_core" in mod_cmake

    def test_the_emitter_skips_per_name(self):
        """Unit-level, independent of any project on disk."""
        both = R.module_targets_block("util", True)
        assert "test_util_core" in both and "bench_util_core" in both
        only_test = R.module_targets_block(
            "util", True, frozenset({"bench_util_core"})
        )
        assert "add_executable(test_util_core" in only_test
        assert "add_executable(bench_util_core" not in only_test
        assert R.module_targets_block("util", False) == ""


class TestAppTargets:
    """The same blind spot on the other surface."""

    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        root = tmp_path / "demo"
        _quiet(new_run, "demo", root)
        _quiet(
            object_run,
            root,
            "w",
            None,
            state_vars=[("g", "float", "1.0f")],
            arg_type="float",
            return_type="float",
        )
        cmake = root / "CMakeLists.txt"
        cmake.write_text(
            cmake.read_text(encoding="utf-8")
            + "\nadd_executable(myapp native/src/other/myapp.c)\n",
            encoding="utf-8",
        )
        return root

    def test_a_hand_written_name_is_avoided(self, project):
        _quiet(app_run, project, name="myapp", object_="w", target="c")
        text = (project / "CMakeLists.txt").read_text(encoding="utf-8")
        assert "add_executable(myapp_app" in text
        assert text.count("add_executable(myapp ") == 1
        # The binary keeps the name the author asked for.
        assert "OUTPUT_NAME myapp" in text

    def test_rerunning_does_not_suffix_again(self, project):
        """`myapp_app_app` is the failure a naive union produces.

        Checked after EVERY run, not once at the end. Counting jm's own app
        block as a project claim makes the name **oscillate** --
        `myapp_app` -> `myapp_app_app` -> `myapp_app` -- because each run
        frees the name the previous one vacated. A test that sampled only the
        final state passed against that, which is how this assertion came to
        be written the wrong way round first.
        """
        cmake = project / "CMakeLists.txt"
        for i in range(4):
            _quiet(app_run, project, name="myapp", object_="w", target="c")
            targets = [
                ln
                for ln in cmake.read_text(encoding="utf-8").splitlines()
                if ln.startswith("add_executable(")
            ]
            assert "add_executable(myapp_app native/src/app/myapp.c)" in (
                targets
            ), f"run {i + 1} produced {targets}"
            assert not any("myapp_app_app" in ln for ln in targets), (
                f"run {i + 1} suffixed again: {targets}"
            )

    def test_an_uncontested_name_is_untouched(self, tmp_path):
        root = tmp_path / "demo"
        _quiet(new_run, "demo", root)
        _quiet(
            object_run,
            root,
            "w",
            None,
            state_vars=[("g", "float", "1.0f")],
            arg_type="float",
            return_type="float",
        )
        _quiet(app_run, root, name="runner", object_="w", target="c")
        text = (root / "CMakeLists.txt").read_text(encoding="utf-8")
        assert "add_executable(runner native/src/app/runner.c)" in text
        assert "runner_app" not in text
