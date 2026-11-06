"""A view may register its class in another module (gh-2516).

A view (gh-504) is a second Python class over one object's C core. Until
gh-2516 it could only live in its parent's module, so a project folding two
objects into one core could not keep the second class where its users import
it from (doppler's ``Acquisition`` in ``doppler.acquire`` over the
correlator's core in ``doppler.spectral``). A ``[[<obj>.views]]`` entry now
takes ``module = "<id>"``, and ``jm view --module`` names where the class
lives.

The fixture is that shape, end to end: module ``spectral`` holds ``win`` and
``corr`` (serializable, linking ``win`` and ``m``), and module ``acquire``
holds two views of ``corr``, ``Acquisition`` and ``Burst``, each with its own
``create_fn``. Each half of the fix is a distinct failure here:

- the fragment included by the wrong aggregator: the class is not in
  ``acquire``, or ``__module__`` is the parent's package;
- the parent's core, or what it links, not reaching ``acquire``'s
  extension: ``import`` fails on an undefined symbol (``corr``'s
  ``create_search`` calls ``win``'s C, so a missing transitive link is as
  fatal as a missing core);
- the stub or the re-export in the wrong module: the class is absent from
  ``acquire``'s ``.pyi`` / ``__init__``, or the replay disagrees with the
  verb and ``status --check`` reports drift.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _compilers import default_cc  # noqa: E402
from _jmrun import run_cli  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._view import run as view_run  # noqa: E402

_NEEDS_BUILD = pytest.mark.skipif(
    shutil.which("cmake") is None or default_cc() is None,
    reason="the build gate needs cmake and a C compiler",
)

#: The view constructors' bodies. Each builds the PARENT's state through
#: the parent's own constructor; ``create_search`` also calls ``win``'s
#: core, so ``acquire``'s extension imports only when ``corr``'s
#: ``depends_on`` reached its link line too.
_SEARCH_BODY = """\
{
    scr_win_state_t *w = scr_win_create (2.0);
    if (!w)
        return NULL;
    scr_win_destroy (w);
    return scr_corr_create (gain * 2.0);
}
"""
_BURST_BODY = """\
{
    return scr_corr_create ((double) reps);
}
"""
#: A view-only status method (gh-1418 / gh-2470 on a view): status 3 is
#: the row's exception, any other non-zero the blanket ValueError.
_ARM_BODY = """\
{
    (void) state;
    return x > 2 ? SCR_ARM_TOO_BIG : 0;
}
"""
#: The PARENT's `set_state`, whose status table a view's triplet shares.
_SET_STATE_BODY = """\
{
    const unsigned char *p = blob;
    double gain;
    memcpy (&gain, p, sizeof (gain));
    if (gain < 0.0)
        return SCR_STATE_BAD;
    state->gain = gain;
    return 0;
}
"""
#: The constants the status tables name, in the parent's sacred header,
#: which the view's fragment includes; and the view constructor's Doxygen,
#: from which its class docstring derives (gh-624) in another module too.
_HEADER_ADDITIONS = """
#define SCR_ARM_TOO_BIG 3
#define SCR_STATE_BAD 7
"""
_SEARCH_DOC = """\
/**
 * @brief A correlator tuned for a wide search.
 *
 * @param gain Search gain, doubled.
 */
"""

#: What the built classes must do, run by the project's own pytest under
#: `jm test` -- the product imported the way its users import it.
_PRODUCT_TEST = '''\
"""gh-2516: two views of spectral's `corr`, registered in acquire."""

import scr.acquire
from scr.acquire import Acquisition, Burst
from scr.spectral import Corr


def test_views_live_in_acquire():
    assert Acquisition.__module__ == "scr.acquire"
    assert Burst.__module__ == "scr.acquire"
    assert "Acquisition" in scr.acquire.__all__
    assert not hasattr(__import__("scr.spectral").spectral, "Acquisition")


def test_a_view_constructs_through_its_own_create_fn():
    a = Acquisition(1.5)
    assert a.get_gain() == 3.0
    b = Burst(reps=4)
    assert b.get_gain() == 4.0


def test_state_round_trips_across_the_shared_core():
    a = Acquisition(1.5)
    b = Burst(reps=7)
    b.set_state(a.get_state())
    assert b.get_gain() == 3.0
    c = Corr(0.0)
    c.set_state(a.get_state())
    assert c.get_gain() == 3.0
    assert a.state_bytes() == c.state_bytes()


def test_set_state_raises_the_parents_mapped_exception():
    a = Acquisition(1.5)
    try:
        a.set_state(Corr(-1.0).get_state())
    except OverflowError as e:
        assert "bad state" in str(e)
    else:
        raise AssertionError("set_state accepted a refused blob")


def test_a_view_status_method_raises_its_row():
    a = Acquisition(1.5)
    assert a.arm(1) is None
    try:
        a.arm(5)
    except OverflowError as e:
        assert "too big" in str(e)
    else:
        raise AssertionError("arm(5) did not raise")


def test_the_class_doc_derives_from_the_parents_header():
    assert "A correlator tuned for a wide search." in Acquisition.__doc__
'''


def _no_repair(root: Path) -> None:
    """Turn off the wheel's repair step, as gh-2127's build test does."""
    pp = root / "pyproject.toml"
    body = pp.read_text(encoding="utf-8")
    old = 'command = "make just-build"\n'
    assert body.count(old) == 1, "scaffold's [tool.just-buildit] changed"
    pp.write_text(
        body.replace(old, old + "repair = false\n"), encoding="utf-8"
    )


def _fill(core_c: Path, fn: str, body: str) -> None:
    """Replace *fn*'s scaffolded stub body in *core_c* with *body*."""
    text = core_c.read_text(encoding="utf-8")
    head = f"{fn}("
    start = text.index(head)
    open_ = text.index("{", start)
    close = text.index("\n}\n", open_) + 3
    assert text.count(head) == 1, f"{fn} defined more than once"
    core_c.write_text(text[:open_] + body + text[close:], encoding="utf-8")


def _project(root: Path) -> Path:
    """The gh-2516 shape: a view of spectral's `corr` registered in
    acquire, twice, through the real verbs."""
    new_run("scr", root, modules=["spectral", "acquire"])
    object_run(root, "win", "spectral", state_vars=[("n", "double", "1.0")])
    object_run(
        root,
        "corr",
        "spectral",
        state_vars=[("gain", "double", "1.0")],
        serializable=True,
        depends_on=[{"name": "win", "link": True}],
        extra_link_libs=["m"],
        set_state_status_errors=[
            {
                "status": "SCR_STATE_BAD",
                "error": "OverflowError",
                "message": "bad state",
            }
        ],
    )
    core_h = root / "native/inc/scr/corr/corr_core.h"
    text = core_h.read_text(encoding="utf-8")
    inc = '#include "scr/clib_common.h"\n'
    assert text.count(inc) == 1
    core_h.write_text(
        text.replace(inc, inc + _HEADER_ADDITIONS), encoding="utf-8"
    )
    view_run(root, "corr", "Acquisition", "acquire", "scr_corr_create_search")
    view_run(
        root,
        "corr",
        "Burst",
        "acquire",
        "scr_corr_create_burst",
        init_params=[{"name": "reps", "type": "int", "default": "1"}],
    )
    arm = run_cli(
        "method",
        "corr",
        "arm",
        "--view",
        "Acquisition",
        "--arg-type",
        "int",
        "--return-type",
        "int",
        "--status-return",
        "--status-error",
        "SCR_ARM_TOO_BIG:OverflowError:too big",
        cwd=root,
    )
    assert arm.returncode == 0, arm.stdout + arm.stderr
    text = core_h.read_text(encoding="utf-8")
    proto = "scr_corr_state_t *scr_corr_create_search(double gain);"
    assert text.count(proto) == 1
    core_h.write_text(
        text.replace(proto, _SEARCH_DOC + proto), encoding="utf-8"
    )
    core_c = root / "native/src/corr/corr_core.c"
    _fill(core_c, "scr_corr_create_search", _SEARCH_BODY)
    _fill(core_c, "scr_corr_create_burst", _BURST_BODY)
    _fill(core_c, "scr_corr_arm", _ARM_BODY)
    _fill(core_c, "scr_corr_set_state", _SET_STATE_BODY)
    # The header's new Doxygen reaches the bindings through `apply`, as
    # it does for a same-module view.
    applied = run_cli("apply", cwd=root)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    return root


def _tree(root: Path) -> "dict[str, bytes]":
    """Every file jm could write, by relative path."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "build" not in p.relative_to(root).parts[:1]
    }


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _project(tmp_path_factory.mktemp("gh2516") / "scr")


class TestPlacement:
    """Where each face of a cross-module view lands."""

    def test_the_manifest_records_the_module(self, project: Path) -> None:
        cfg = C.load(project)
        views = {v["class_name"]: v for v in C.views(cfg, "corr")}
        assert views["Acquisition"]["module"] == "acquire"
        assert [
            v["class_name"] for _, v in C.module_views(cfg, "acquire")
        ] == [
            "Acquisition",
            "Burst",
        ]
        assert C.module_views(cfg, "spectral") == []

    def test_the_fragment_is_the_view_modules(self, project: Path) -> None:
        acq = project / "native/src/acquire"
        spec = project / "native/src/spectral"
        assert (acq / "acquire_ext_acquisition.c").is_file()
        assert (acq / "acquire_ext_burst.c").is_file()
        assert not (spec / "spectral_ext_acquisition.c").exists()
        agg = (acq / "acquire_ext.c").read_text(encoding="utf-8")
        assert '#include "acquire_ext_acquisition.c"' in agg
        assert '#include "acquire_ext_burst.c"' in agg
        home = (spec / "spectral_ext.c").read_text(encoding="utf-8")
        assert "acquisition" not in home.lower()

    def test_the_core_links_once_with_what_it_links(
        self, project: Path
    ) -> None:
        cmake = (project / "native/src/acquire/CMakeLists.txt").read_text(
            encoding="utf-8"
        )
        for lib in ("corr_core", "win_core"):
            assert cmake.count(f"    {lib}\n") == 1, (lib, cmake)
        assert "    m\n" in cmake

    def test_the_stub_and_reexport_are_the_view_modules(
        self, project: Path
    ) -> None:
        pyi = (project / "src/scr/acquire/acquire.pyi").read_text("utf-8")
        assert "class Acquisition:" in pyi and "class Burst:" in pyi
        home = (project / "src/scr/spectral/spectral.pyi").read_text("utf-8")
        assert "class Acquisition" not in home
        init = (project / "src/scr/acquire/__init__.py").read_text("utf-8")
        assert "from .acquire import Acquisition, Burst" in init

    def test_no_core_is_scaffolded_for_the_view(self, project: Path) -> None:
        core_h = (project / "native/inc/scr/corr/corr_core.h").read_text(
            "utf-8"
        )
        assert "scr_corr_create_search(double gain);" in core_h
        assert not list(project.glob("native/**/acquisition*"))


class TestTheManifestRebuildsIt:
    """`apply` replays the verbs' tree, and replays it once."""

    def test_status_is_clean_and_apply_writes_nothing(
        self, tmp_path: Path
    ) -> None:
        root = _project(tmp_path / "scr")
        status = run_cli("status", "--check", cwd=root)
        assert status.returncode == 0, status.stdout + status.stderr
        first = run_cli("apply", cwd=root)
        assert first.returncode == 0, first.stdout + first.stderr
        before = _tree(root)
        second = run_cli("apply", cwd=root)
        assert second.returncode == 0, second.stdout + second.stderr
        assert _tree(root) == before
        status = run_cli("status", "--check", cwd=root)
        assert status.returncode == 0, status.stdout + status.stderr

    def test_script_replays_the_module(self, project: Path) -> None:
        out = run_cli("script", cwd=project)
        assert out.returncode == 0, out.stderr
        text = " ".join(out.stdout.split())
        assert (
            "view corr Acquisition \\ --module acquire \\ --create-fn"
            " scr_corr_create_search" in text
        ), out.stdout


class TestEveryReaderFindsIt:
    """The readers that enumerate a view's fragment, or its core's reach,
    look in the module that registers it."""

    def test_status_reports_a_drifted_fragment_in_the_view_module(
        self, tmp_path: Path
    ) -> None:
        root = _project(tmp_path / "scr")
        assert run_cli("apply", cwd=root).returncode == 0
        frag = root / "native/src/acquire/acquire_ext_acquisition.c"
        text = frag.read_text(encoding="utf-8")
        old = "Acquisition type for the acquire module."
        assert text.count(old) == 1
        frag.write_text(text.replace(old, "edited."), encoding="utf-8")
        out = run_cli("status", cwd=root)
        assert "native/src/acquire/acquire_ext_acquisition.c" in out.stdout, (
            out.stdout
        )

    def test_the_core_reaches_two_extension_modules(
        self, project: Path
    ) -> None:
        """`_procglobal` counts the view module's reach, so a
        `process_global` core's rendezvous and refusals apply to it."""
        from just_makeit import _procglobal as PG

        cfg = C.load(project)
        assert "corr_core" in PG.module_cores(cfg)["acquire"]
        shared = {s.core: s.modules for s in PG.shared_cores(cfg)}
        assert set(shared["corr_core"]) == {"spectral", "acquire"}

    def test_a_parent_change_reaches_the_view_module(
        self, tmp_path: Path
    ) -> None:
        """A method added to the parent is the views' method too, so its
        module is regenerated with the parent's."""
        root = _project(tmp_path / "scr")
        r = run_cli(
            "method",
            "corr",
            "scale",
            "--arg-type",
            "float",
            "--return-type",
            "float",
            cwd=root,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        pyi = (root / "src/scr/acquire/acquire.pyi").read_text("utf-8")
        assert pyi.count("def scale(") == 2, pyi
        status = run_cli("status", "--check", cwd=root)
        assert status.returncode == 0, status.stdout + status.stderr

    def test_a_retained_capsule_param_reaches_the_view_module(
        self, tmp_path: Path
    ) -> None:
        """A parent method keeping a capsule (gh-2327) is the views' method
        too: each view's own owner slot is in its fragment, in acquire."""
        root = _project(tmp_path / "scr")
        cfg = C.load(root)
        cfg["corr"].setdefault("methods", []).append(
            {
                "name": "attach",
                "return_type": "int",
                "status_return": True,
                "params": [
                    {
                        "name": "t",
                        "type": "tt_t *",
                        "capsule": "scr.tt",
                        "header": "scr/tt.h",
                        "retain": True,
                    }
                ],
            }
        )
        C.save(root, cfg)
        r = run_cli("apply", cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
        for name in ("acquisition", "burst"):
            body = (
                root / f"native/src/acquire/acquire_ext_{name}.c"
            ).read_text(encoding="utf-8")
            assert "_jm_retain_attach_t" in body, name
        status = run_cli("status", "--check", cwd=root)
        assert status.returncode == 0, status.stdout + status.stderr

    def test_removing_the_parent_leaves_no_view_behind(
        self, tmp_path: Path
    ) -> None:
        root = _project(tmp_path / "scr")
        r = run_cli("remove", "object", "corr", "--force", cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
        acq = root / "native/src/acquire"
        assert not list(acq.glob("acquire_ext_*.c"))
        agg = (acq / "acquire_ext.c").read_text(encoding="utf-8")
        assert "acquisition" not in agg.lower()
        init = (root / "src/scr/acquire/__init__.py").read_text("utf-8")
        assert "Acquisition" not in init
        status = run_cli("status", "--check", cwd=root)
        assert status.returncode == 0, status.stdout + status.stderr

    def test_removing_the_view_module_is_refused(self, tmp_path: Path) -> None:
        """Its views are named by the parent's manifest table, so the
        removal would leave entries naming nothing."""
        root = _project(tmp_path / "scr")
        r = run_cli("remove", "module", "acquire", "--force", cwd=root)
        assert r.returncode != 0
        assert "Acquisition (a view of 'corr')" in r.stderr
        assert (root / "native/src/acquire/acquire_ext.c").is_file()


class TestRefusals:
    """What `jm view --module` still refuses."""

    def test_a_class_name_taken_in_the_view_module(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "p"
        new_run("p", root, modules=["spectral", "acquire"])
        object_run(root, "corr", "spectral")
        object_run(root, "acq", "acquire")
        with pytest.raises(SystemExit):
            view_run(root, "corr", "Acq", "acquire", "p_corr_make")

    def test_an_unknown_module(self, tmp_path: Path) -> None:
        root = tmp_path / "p"
        new_run("p", root, modules=["spectral"])
        object_run(root, "corr", "spectral")
        r = run_cli(
            "view",
            "corr",
            "Acq",
            "--module",
            "nosuch",
            "--create-fn",
            "p_corr_make",
            cwd=root,
        )
        assert r.returncode != 0
        assert "module 'nosuch' not found" in r.stderr

    def test_one_parent_two_views_one_name(self, tmp_path: Path) -> None:
        """The fragment is tied back to its view by the lowercased class
        name among the parent's views, so it must be unique there too."""
        root = tmp_path / "p"
        new_run("p", root, modules=["spectral", "acquire"])
        object_run(root, "corr", "spectral")
        view_run(root, "corr", "Acq", "spectral", "p_corr_make")
        with pytest.raises(SystemExit):
            view_run(root, "corr", "Acq", "acquire", "p_corr_make2")


@_NEEDS_BUILD
class TestTheBuildRuns:
    def test_the_views_build_import_and_share_the_core(
        self, tmp_path: Path
    ) -> None:
        """The real `jm build` then `jm test`: the classes import from
        acquire, construct through their own `create_fn`, share the
        parent's methods, and round-trip state with the parent."""
        root = _project(tmp_path / "scr")
        tests = root / "src/scr/acquire/tests"
        tests.mkdir(parents=True, exist_ok=True)
        (tests / "test_gh2516_views.py").write_text(
            _PRODUCT_TEST, encoding="utf-8"
        )
        _no_repair(root)
        built = run_cli("build", cwd=root)
        assert built.returncode == 0, built.stdout + built.stderr
        ran = run_cli("test", cwd=root)
        output = ran.stdout + ran.stderr
        assert ran.returncode == 0, output
        for name in (
            "test_views_live_in_acquire",
            "test_a_view_constructs_through_its_own_create_fn",
            "test_state_round_trips_across_the_shared_core",
            "test_set_state_raises_the_parents_mapped_exception",
            "test_a_view_status_method_raises_its_row",
            "test_the_class_doc_derives_from_the_parents_header",
        ):
            assert f"{name} PASSED" in output, output
        assert "FAILED" not in output
