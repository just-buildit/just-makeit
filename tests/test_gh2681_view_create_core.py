"""A view's constructor may live in its own module's core (gh-2681).

A view in another module (gh-2516) used to have its ``create_fn`` scaffolded
into its PARENT's core, whatever module the class lived in. A general
component with an application's view therefore carried the application's
constructor: its name in the general header, and a body in the general core
calling the module that already depends on it. Declaring the function where
it belongs was refused as a ``c_prefix`` collision, since the replay put it
in the parent's header and the author's copy was "another" declaration.

``[[<obj>.views]] create_core = "<object>"`` (``jm view --create-core``)
names an object of the view's module whose core declares and defines it.

The fixture is that shape: ``spectral`` holds the general ``corr``;
``acquire`` holds ``acq``, which depends on ``corr`` with a link, and the
view ``Acquisition`` of ``corr`` whose constructor ``scr_acq_corr_search``
is spelled under ``acq``'s stem, the name doppler's ``dp_wlan_ofdm_create``
was refused for. Each half of the fix is a distinct failure here:

- the scaffold: ``corr``'s header or core still names the constructor, or
  ``acq``'s does not declare and define it;
- the replay: ``apply`` refuses the tree (the collision), rewrites it, or
  ``status --check`` reports drift;
- the readers: the class docstring is not the Doxygen in ``acq``'s header,
  the signature check does not read that header, or ``acq``'s link table
  does not name the constructor;
- the build: the extension does not import, or the view does not construct
  through the function ``acq``'s core defines.
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
from just_makeit import _ctorsig, _linkcheck  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._view import run as view_run  # noqa: E402

_NEEDS_BUILD = pytest.mark.skipif(
    shutil.which("cmake") is None or default_cc() is None,
    reason="the build gate needs cmake and a C compiler",
)

FN = "scr_acq_corr_search"
PROTO = f"scr_corr_state_t *{FN}(double gain);"
CORR_H = "native/inc/scr/corr/corr_core.h"
CORR_C = "native/src/corr/corr_core.c"
ACQ_H = "native/inc/scr/acq/acq_core.h"
ACQ_C = "native/src/acq/acq_core.c"

#: The constructor's Doxygen, written where the function is declared.
_DOC = """\
/**
 * @brief A correlator tuned for a wide search.
 *
 * @param gain Search gain, doubled.
 */
"""
#: Its body, in acq's core: it builds the PARENT's state through the
#: parent's own constructor, which only links because acq depends on corr.
_BODY = """\
{
    return scr_corr_create (gain * 2.0);
}
"""

_PRODUCT_TEST = '''\
"""gh-2681: a view whose constructor acq's core defines."""

from scr.acquire import Acquisition


def test_the_view_constructs_through_acqs_core():
    assert Acquisition(1.5).get_gain() == 3.0


def test_the_class_doc_derives_from_acqs_header():
    assert "A correlator tuned for a wide search." in Acquisition.__doc__
'''


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


def _fill(core_c: Path, fn: str, body: str) -> None:
    """Replace *fn*'s scaffolded stub body in *core_c* with *body*."""
    text = core_c.read_text(encoding="utf-8")
    head = f"{fn}("
    assert text.count(head) == 1, f"{fn} defined other than once"
    open_ = text.index("{", text.index(head))
    close = text.index("\n}\n", open_) + 3
    core_c.write_text(text[:open_] + body + text[close:], encoding="utf-8")


def _base(root: Path, *, link: bool = True) -> Path:
    """The two modules and their objects, before the view."""
    new_run("scr", root, modules=["spectral", "acquire"])
    object_run(
        root, "corr", "spectral", state_vars=[("gain", "double", "1.0")]
    )
    object_run(
        root,
        "acq",
        "acquire",
        state_vars=[("n", "double", "1.0")],
        depends_on=[{"name": "corr", "link": True}] if link else ["corr"],
    )
    return root


def _project(root: Path) -> Path:
    _base(root)
    view_run(root, "corr", "Acquisition", "acquire", FN, create_core="acq")
    acq_h = root / ACQ_H
    text = acq_h.read_text(encoding="utf-8")
    assert text.count(PROTO) == 1, text
    acq_h.write_text(text.replace(PROTO, _DOC + PROTO), encoding="utf-8")
    _fill(root / ACQ_C, FN, _BODY)
    applied = run_cli("apply", cwd=root)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    return root


def _tree(root: Path) -> "dict[str, bytes]":
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "build" not in p.relative_to(root).parts[:1]
    }


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _project(tmp_path_factory.mktemp("gh2681") / "scr")


class TestTheScaffold:
    def test_the_manifest_records_the_core(self, project: Path) -> None:
        (view,) = C.views(C.load(project), "corr")
        assert view["create_core"] == "acq"
        assert C.view_create_core(C.load(project), "corr", view) == "acq"

    def test_the_parents_core_never_names_it(self, project: Path) -> None:
        assert FN not in _read(project, CORR_H)
        assert FN not in _read(project, CORR_C)

    def test_acqs_core_declares_and_defines_it(self, project: Path) -> None:
        header = _read(project, ACQ_H)
        assert header.count(PROTO) == 1
        # After the include that puts the parent's state type in scope.
        inc = '#include "scr/corr/corr_core.h"'
        assert inc in header and header.index(inc) < header.index(PROTO)
        assert _read(project, ACQ_C).count(f"{FN}(") == 1


class TestTheManifestRebuildsIt:
    def test_status_is_clean_and_apply_writes_nothing(
        self, tmp_path: Path
    ) -> None:
        root = _project(tmp_path / "scr")
        status = run_cli("status", "--check", cwd=root)
        assert status.returncode == 0, status.stdout + status.stderr
        before = _tree(root)
        again = run_cli("apply", cwd=root)
        assert again.returncode == 0, again.stdout + again.stderr
        assert _tree(root) == before

    def test_script_replays_the_core(self, project: Path) -> None:
        out = run_cli("script", cwd=project)
        assert out.returncode == 0, out.stderr
        text = " ".join(out.stdout.split())
        assert f"--create-fn {FN} \\ --create-core acq" in text, out.stdout


class TestEveryReaderFindsIt:
    def test_the_signature_check_reads_acqs_header(
        self, tmp_path: Path
    ) -> None:
        root = _project(tmp_path / "scr")
        acq_h = root / ACQ_H
        text = acq_h.read_text(encoding="utf-8")
        acq_h.write_text(
            text.replace(PROTO, f"scr_corr_state_t *{FN}(float gain);"),
            encoding="utf-8",
        )
        (found,) = _ctorsig.drift(root, C.load(root))
        assert (found.fn, found.rel) == (FN, ACQ_H)
        assert found.declared == "float gain"

    def test_acqs_link_table_names_it(self, project: Path) -> None:
        table = _linkcheck.symbols_file(project, "acq", C.load(project))
        assert f"(jm_any_fn){FN}," in table.read_text(encoding="utf-8")
        corr = _linkcheck.symbols_file(project, "corr", C.load(project))
        assert FN not in corr.read_text(encoding="utf-8")

    def test_the_stub_documents_it_from_acqs_header(
        self, project: Path
    ) -> None:
        pyi = _read(project, "src/scr/acquire/acquire.pyi")
        assert "A correlator tuned for a wide search." in pyi


class TestRefused:
    def test_an_object_of_another_module(self, tmp_path: Path) -> None:
        root = _base(tmp_path / "scr")
        object_run(root, "win", "spectral")
        with pytest.raises(SystemExit):
            view_run(
                root, "corr", "Acquisition", "acquire", FN, create_core="win"
            )
        assert C.views(C.load(root), "corr") == []

    def test_an_object_not_linking_the_parent(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = _base(tmp_path / "scr", link=False)
        with pytest.raises(SystemExit):
            view_run(
                root, "corr", "Acquisition", "acquire", FN, create_core="acq"
            )
        err = capsys.readouterr().err
        assert 'depends_on = [{ name = "corr", link = true }]' in err
        assert FN not in _read(root, ACQ_H)

    def test_the_parent_named_is_the_default(self, tmp_path: Path) -> None:
        root = _base(tmp_path / "scr")
        view_run(
            root, "corr", "Acquisition", "acquire", FN, create_core="corr"
        )
        (view,) = C.views(C.load(root), "corr")
        assert "create_core" not in view
        assert PROTO in _read(root, CORR_H)


@_NEEDS_BUILD
def test_the_view_builds_and_constructs_through_acqs_core(
    tmp_path: Path,
) -> None:
    """The real `jm build` then `jm test`, run by the project's pytest."""
    root = _project(tmp_path / "scr")
    tests = root / "src/scr/acquire/tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "__init__.py").write_text("", encoding="utf-8")
    (tests / "test_gh2681_view.py").write_text(_PRODUCT_TEST, encoding="utf-8")
    pp = root / "pyproject.toml"
    body = pp.read_text(encoding="utf-8")
    old = 'command = "make just-build"\n'
    assert body.count(old) == 1
    pp.write_text(
        body.replace(old, old + "repair = false\n"), encoding="utf-8"
    )
    built = run_cli("build", cwd=root)
    assert built.returncode == 0, built.stdout + built.stderr
    ran = run_cli("test", cwd=root)
    output = ran.stdout + ran.stderr
    assert ran.returncode == 0, output
    for name in (
        "test_the_view_constructs_through_acqs_core",
        "test_the_class_doc_derives_from_acqs_header",
    ):
        assert f"{name} PASSED" in output, output


def _declare_in_manifest(root: Path, **extra: str) -> None:
    """Add the view by editing the manifest, as a project that never runs
    `jm view` does, rather than through the verb."""
    m = root / "just-makeit.toml"
    text = m.read_text(encoding="utf-8")
    rows = "".join(f'{k} = "{v}"\n' for k, v in extra.items())
    entry = (
        '[[corr.views]]\nclass_name = "Acquisition"\nmodule = "acquire"\n'
        f'create_fn = "{FN}"\n{rows}\n'
    )
    assert text.count("[acq]\n") == 1
    m.write_text(text.replace("[acq]\n", entry + "[acq]\n"), encoding="utf-8")


def _drop_stub(core_c: Path, fn: str) -> None:
    """Delete jm's scaffolded definition of *fn* from *core_c*."""
    text = core_c.read_text(encoding="utf-8")
    start = text.index(f"scr_corr_state_t *\n{fn}(")
    end = text.index("\n}\n", start) + 3
    core_c.write_text(text[:start] + text[end:], encoding="utf-8")


class TestAManifestDeclaredView:
    """`apply` alone declares the constructor, not only `jm view`: the
    fragment calls it, so a stub with no prototype is an implicit
    declaration (an error from gcc 14 and clang 16)."""

    @pytest.mark.parametrize(
        ("extra", "header", "other"),
        [({}, CORR_H, ACQ_H), ({"create_core": "acq"}, ACQ_H, CORR_H)],
        ids=["parent", "create_core"],
    )
    def test_apply_declares_and_stubs_it(
        self, tmp_path: Path, extra: dict, header: str, other: str
    ) -> None:
        root = _base(tmp_path / "scr")
        _declare_in_manifest(root, **extra)
        applied = run_cli("apply", cwd=root)
        assert applied.returncode == 0, applied.stdout + applied.stderr
        assert _read(root, header).count(PROTO) == 1
        assert FN not in _read(root, other)
        status = run_cli("status", "--check", cwd=root)
        assert status.returncode == 0, status.stdout + status.stderr


class TestMovingAnExistingView:
    """A view that gains `create_core` after its constructor was scaffolded
    into the parent's core -- the migration a project makes."""

    def test_apply_refuses_until_the_parents_copy_moves(
        self, tmp_path: Path
    ) -> None:
        root = _base(tmp_path / "scr")
        _declare_in_manifest(root)
        assert run_cli("apply", cwd=root).returncode == 0
        m = root / "just-makeit.toml"
        text = m.read_text(encoding="utf-8")
        row = f'create_fn = "{FN}"\n'
        m.write_text(
            text.replace(row, row + 'create_core = "acq"\n'), encoding="utf-8"
        )
        refused = run_cli("apply", cwd=root)
        err = refused.stdout + refused.stderr
        assert refused.returncode != 0
        for rel in (CORR_H, CORR_C):
            assert f"{rel} still names `{FN}`" in err, err
        assert "Rename yours" not in err
        assert FN not in _read(root, ACQ_H)
        # Moved: the parent's copy deleted, `apply` puts it in acq's core.
        corr_h = root / CORR_H
        corr_h.write_text(
            _read(root, CORR_H).replace(PROTO + "\n", ""), encoding="utf-8"
        )
        _drop_stub(root / CORR_C, FN)
        applied = run_cli("apply", cwd=root)
        assert applied.returncode == 0, applied.stdout + applied.stderr
        assert _read(root, ACQ_H).count(PROTO) == 1
        assert _read(root, ACQ_C).count(f"{FN}(") == 1
        status = run_cli("status", "--check", cwd=root)
        assert status.returncode == 0, status.stdout + status.stderr

    def test_jm_view_refuses_a_stranded_copy(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = _base(tmp_path / "scr")
        corr_h = root / CORR_H
        text = corr_h.read_text(encoding="utf-8")
        inc = '#include "scr/clib_common.h"\n'
        corr_h.write_text(text.replace(inc, inc + PROTO + "\n"), "utf-8")
        with pytest.raises(SystemExit):
            view_run(
                root, "corr", "Acquisition", "acquire", FN, create_core="acq"
            )
        assert f"{CORR_H} still names `{FN}`" in capsys.readouterr().err
        assert C.views(C.load(root), "corr") == []


def test_removing_the_create_core_object_is_refused(project: Path) -> None:
    before = _tree(project)
    out = run_cli("remove", "object", "acq", "--force", cwd=project)
    assert out.returncode != 0
    assert "create_core of Acquisition" in out.stdout + out.stderr
    assert _tree(project) == before
