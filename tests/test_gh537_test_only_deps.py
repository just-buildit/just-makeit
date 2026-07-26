"""gh-537: a dependency needed only to link the C test stays out of the .so.

``depends_on`` is additive by design (gh-254): a dependency lands on the
component's core, its test and bench, *and* the shipped artifact. That is right
for a real dependency. It is wrong for a component whose C test round-trips
through a sibling — doppler's reader writes the captures it then reads back, so
it must declare the writer, which then ships inside the reader module.

The cost is not the few KB. The manifest ends up asserting a dependency the
shipped artifact does not have, and the manifest is meant to be the project's
source of truth.

``{name = "wtr", test_only = true}`` keeps the dependency on the test and bench
link lines and off every surface the artifact is built from:

* the core's PUBLIC link line — from there it propagates straight back into the
  Python extension and ships after all, which would defeat the whole flag;
* the ``.so`` link line;
* the aggregate library's ``target_sources``;
* the object's public core header.
"""

import io
import contextlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _q(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


@pytest.fixture()
def proj(tmp_path):
    d = tmp_path / "p"
    _q(new_run, "p", d, [], [], modules=["wfm"])
    _q(object_run, d, "wtr", "wfm")
    _q(object_run, d, "rdr", "wfm")
    return d


def _declare(proj, entry):
    cfg = C.load(proj)
    cfg["rdr"]["depends_on"] = [entry]
    C.save(proj, cfg)
    # apply is additive: it materializes what is missing, so remove the two
    # artifacts under test to force a genuine re-render.
    (proj / "native" / "src" / "rdr" / "CMakeLists.txt").unlink()
    (proj / "native" / "inc" / "rdr" / "rdr_core.h").unlink()
    _q(apply_run, proj)
    return (
        (proj / "native" / "src" / "rdr" / "CMakeLists.txt").read_text(),
        (proj / "native" / "inc" / "rdr" / "rdr_core.h").read_text(),
    )


TEST_ONLY = {"name": "wtr", "test_only": True}
REAL_DEP = {"name": "wtr", "link": True}


class TestTestOnlyReachesTheTest:
    def test_test_and_bench_link_it(self, proj):
        cmake, _ = _declare(proj, TEST_ONLY)
        assert "PRIVATE rdr_core wtr_core" in cmake
        assert cmake.count("wtr_core") == 2  # test + bench, nothing else


class TestTestOnlyStaysOutOfTheArtifact:
    def test_not_on_the_cores_public_link(self, proj):
        """The load-bearing one: a PUBLIC link here propagates into the
        extension, so the dependency would ship regardless of the flag."""
        cmake, _ = _declare(proj, TEST_ONLY)
        assert "target_link_libraries(rdr_core PUBLIC" not in cmake

    def test_not_in_the_public_core_header(self, proj):
        _, header = _declare(proj, TEST_ONLY)
        assert "wtr" not in header

    def test_excluded_from_the_shipped_dep_list(self, proj):
        """`depends_on()` feeds header includes and the aggregate library's
        objects — the surfaces the artifact is built from."""
        _declare(proj, TEST_ONLY)
        cfg = C.load(proj)
        assert C.depends_on(cfg, "rdr") == []
        assert C.depends_link_libs(cfg, "rdr") == []

    def test_link_true_does_not_override_test_only(self, proj):
        """Contradictory flags resolve to the safe reading — silently honouring
        `link` is precisely how the dependency ends up in the .so."""
        cfg = C.load(proj)
        cfg["rdr"]["depends_on"] = [
            {"name": "wtr", "link": True, "test_only": True}
        ]
        assert C.depends_link_libs(cfg, "rdr") == []


class TestRealDepUnchanged:
    """A normal dependency must behave exactly as it did before gh-537."""

    def test_reaches_core_and_header(self, proj):
        cmake, header = _declare(proj, REAL_DEP)
        assert "target_link_libraries(rdr_core PUBLIC" in cmake
        assert "wtr/wtr_core.h" in header

    def test_still_in_the_shipped_dep_list(self, proj):
        _declare(proj, REAL_DEP)
        cfg = C.load(proj)
        assert C.depends_on(cfg, "rdr") == ["wtr"]
        assert C.depends_link_libs(cfg, "rdr") == ["wtr_core"]


class TestManifestRoundTrip:
    def test_key_survives_both_dumpers(self, proj):
        """Two `depends_on` dumpers exist and both write an explicit key list,
        so an unknown key is dropped silently — which would republish the
        dependency into the artifact on the next apply (the gh-580 lesson)."""
        _declare(proj, TEST_ONLY)
        text = C._dump({"rdr": C.load(proj)["rdr"]})
        assert "test_only = true" in text
        entry = C.tomllib.loads(text)["rdr"]["depends_on"][0]
        assert entry["test_only"] is True

    def test_survives_a_save_load_cycle(self, proj):
        _declare(proj, TEST_ONLY)
        assert C.depends_test_only_cores(C.load(proj), "rdr") == ["wtr_core"]
