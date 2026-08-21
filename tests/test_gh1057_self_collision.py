"""gh-1055/gh-1057: jm must not emit one target name twice, and must notice.

gh-1046 gave jm a way to avoid colliding with the **project**. Nothing gave it
a way to avoid colliding with **itself**, and the data structure it used made
that undetectable by construction: `_targets.from_manifest()` accumulated into
a `set`, so a name produced twice was indistinguishable from one produced once.

The instance (gh-1055) is a **release blocker**: gh-1034 gives a module with
free functions a `test_`/`bench_<cname>_core` pair. For a *collocated
module-object* -- a module whose `objects` list contains its own name -- the
object already emits `test_<obj>_core` into the very same `CMakeLists.txt`, so
the module's identically-named pair is a second `add_executable` with one name
in one file. `cmake` refuses to configure at all, and it breaks `add_test` one
line further down too.

Measured on doppler at the time of filing: `from_manifest()` reported a set of
356 while 365 names were emitted -- **9 produced twice, and the set reported
zero**. Four of those were fatal (`agc`, `wfm_writer`) and five were an
over-count in the enumeration itself: a module object has no extension target
of its own, since it shares the module's `.so`.

Both halves are jm's own bugs, so the gate asserts the invariant rather than
the instance: **jm emits each target name exactly once**.
"""

from __future__ import annotations

import contextlib
import io
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _targets as T  # noqa: E402
from just_makeit._function import run as function_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _collocated(tmp_path: Path, name: str = "agc") -> Path:
    """A module whose `objects` contains its own name, plus a free function.

    doppler's `agc` and `wfm_writer` exactly.
    """
    root = tmp_path / "demo"
    _quiet(new_run, "demo", root)
    _quiet(module_run, root, name)
    _quiet(
        object_run,
        root,
        name,
        name,
        state_vars=[("g", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    _quiet(
        function_run,
        root,
        "settling_samples",
        name,
        params=[("n", "size_t")],
        return_type="size_t",
    )
    return root


def _adds(root: Path, module: str, target: str) -> int:
    """How many `add_executable` calls in *module* declare *target*.

    Regex, not `str.count`: cmake-format wraps a long call so the name can
    land on the line AFTER `add_executable(`, and a substring count silently
    reads that as zero -- which looks like the bug being fixed.
    """
    text = (root / "native" / "src" / module / "CMakeLists.txt").read_text(
        encoding="utf-8"
    )
    return len(re.findall(rf"add_executable\(\s*{re.escape(target)}\b", text))


class TestTheCollocatedModuleObject:
    """gh-1055 — the blocker."""

    def test_the_pair_is_emitted_once(self, tmp_path):
        root = _collocated(tmp_path)
        assert _adds(root, "agc", "test_agc_core") == 1
        assert _adds(root, "agc", "bench_agc_core") == 1

    @pytest.mark.skipif(
        subprocess.run(["which", "cmake"], capture_output=True).returncode
        != 0,
        reason="cmake not installed",
    )
    def test_the_project_actually_configures(self, tmp_path):
        """The symptom, end to end. A count is not a build.

        Skipped rather than removed when cmake is absent; the count assertion
        above still runs everywhere.
        """
        root = _collocated(tmp_path)
        r = subprocess.run(
            ["cmake", "-S", str(root), "-B", str(root / "build")],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stdout + r.stderr

    def test_a_function_only_module_still_gets_its_pair(self, tmp_path):
        """The guard against over-fixing.

        Seven of doppler's nine modules are genuinely function-only and get
        exactly what gh-1034 intended. Suppressing the pair everywhere would
        satisfy gh-1055 by deleting the feature.
        """
        root = _collocated(tmp_path)
        _quiet(module_run, root, "util")
        _quiet(
            function_run,
            root,
            "clamp",
            "util",
            params=[("x", "double")],
            return_type="double",
        )
        assert _adds(root, "util", "test_util_core") == 1
        assert _adds(root, "util", "bench_util_core") == 1


class TestJmDoesNotCollideWithItself:
    """gh-1057 — the gap under it."""

    def test_a_collocated_module_object_produces_no_collision(self, tmp_path):
        cfg = C.load(_collocated(tmp_path))
        assert T.collisions(cfg) == {}

    def test_every_emitted_name_is_unique(self, tmp_path):
        """The invariant, stated directly."""
        emitted = T.emitted(C.load(_collocated(tmp_path)))
        assert len(emitted) == len(set(emitted)), sorted(emitted)

    def test_collisions_can_actually_report_one(self):
        """The detector is not vacuous.

        `collisions()` returning `{}` is only meaningful if it CAN return
        something -- which is exactly what the old `set` could never do.
        """
        assert T.collisions({"project": {"name": "d"}}) == {}
        doubled = T.emitted({"project": {"name": "d"}}) * 2
        counts: dict[str, int] = {}
        for n in doubled:
            counts[n] = counts.get(n, 0) + 1
        assert {n: c for n, c in counts.items() if c > 1}, (
            "the counting rule itself cannot see a repeat"
        )

    def test_a_module_object_has_no_extension_target(self, tmp_path):
        """Why five of the nine were an over-count, not a defect.

        A module object shares the module's `.so`; only the module emits
        `Python3_add_library`. Counting the object's name as a second
        emission would make the gate fire on five correct projects.
        """
        root = _collocated(tmp_path)
        text = (root / "native/src/agc/CMakeLists.txt").read_text(
            encoding="utf-8"
        )
        assert text.count("Python3_add_library(agc ") == 1
        assert T.emitted(C.load(root)).count("agc") == 1

    def test_from_manifest_still_reserves_what_it_used_to(self, tmp_path):
        """gh-1046's callers must not lose names.

        `jm app` picks a fresh target off this set; narrowing it silently
        would reintroduce the collision gh-1046 closed.
        """
        root = _collocated(tmp_path)
        claimed = T.from_manifest(C.load(root))
        for name in (
            "agc",
            "agc_core",
            "test_agc_core",
            "bench_agc_core",
            "demo_lib",
            "demo_lib_static",
        ):
            assert name in claimed, name
