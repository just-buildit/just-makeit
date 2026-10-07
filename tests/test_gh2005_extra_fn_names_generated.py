"""gh-2005: an extra row's ``fn`` naming a name jm generates is refused.

gh-1997 refused an ``[[<obj>.extra_methods]]`` row whose ``name`` shadows a
generated member and whose ``fn`` the core header declares -- but not one
whose ``fn`` is a function the generated BINDING defines. ``fn =
"Solo_reset"`` on object ``solo`` applied cleanly, and the compiler then
reported ``conflicting types for 'Solo_reset'``: the row's prototype is
``static PyObject *Solo_reset(PyObject *, PyObject *)`` and jm's own wrapper
takes ``SoloObject *``. A composer's ``[[module.X.extra_methods]]`` renders
through the same emitter and had no refusal at all.

The cases are not a list. Every file-scope function and initialised static
in the binding `apply` wrote is read off the TREE -- by a column-0 scan of
the house style, not by the code under test -- and each is declared as a
row's ``fn``. A wrapper jm starts generating later is a case the day it
exists.

GATE: for a standalone object, a module object (its translation unit holds
      the module's every fragment) and a composer, an ``fn`` equal to each
      name the binding declares exits 1 with one ``error:`` line per row,
      naming it, and the tree is byte-identical.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from _jmrun import run_cli

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _config as C  # noqa: E402
from test_composer_apply import _project as _composer_project  # noqa: E402

#: A definition in jm's house style starts its name at column 0, return type
#: on the line above (`Solo_reset(SoloObject *self, ...)`, `PyInit_m(void)`).
#: `__attribute__((unused))` stands at column 0 too, and is no definition.
_DEF_RE = re.compile(r"^([A-Za-z_]\w*)[ \t]*\(", re.M)
#: An initialised file-scope static: the type object, the method and getset
#: tables, the module definition.
_STATIC_RE = re.compile(
    r"^static\s+(?:const\s+)?(?:struct\s+)?\w+\s+\**(\w+)\s*(?:\[\])?\s*=",
    re.M,
)


def _jm(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, f"jm {' '.join(args)}:\n{r.stdout}{r.stderr}"
    return r.stdout


def _declared_names(files: "list[Path]") -> "set[str]":
    """What the binding on disk defines, read independently of jm."""
    names: set[str] = set()
    for f in files:
        text = f.read_text(encoding="utf-8")
        names |= set(_DEF_RE.findall(text)) | set(_STATIC_RE.findall(text))
    return {n for n in names if not n.startswith("__")}


def _snapshot(root: Path) -> "dict[str, bytes]":
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _refuses_each(root: Path, names: "set[str]", put_rows, where: str) -> None:
    """Declare one row per name, apply, and require each to be refused.

    One row per name in ONE apply: each is its own refusal, so the run
    prints exactly one ``error:`` line per row -- and nothing is written.
    """
    rows = [
        {"name": f"hand{i}", "fn": fn} for i, fn in enumerate(sorted(names))
    ]
    put_rows(rows)
    before = _snapshot(root)
    r = run_cli("apply", cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    unrefused = []
    for row in rows:
        label = f"{where} {row['name']!r}: fn {row['fn']!r}"
        if sum(label in ln for ln in errors) != 1:
            unrefused.append(row["fn"])
    assert not unrefused, (unrefused, r.stderr)
    assert len(errors) == len(rows), r.stderr
    assert _snapshot(root) == before


@pytest.fixture(scope="module")
def objects(tmp_path_factory) -> Path:
    """A standalone `solo`, and a module `m` holding `o` and `q`, each with
    members beyond the scaffold's -- a property, a method -- so the binding
    defines more than the lifecycle."""
    root = tmp_path_factory.mktemp("gh2005")
    _jm("new", "xm", cwd=root)
    p = root / "xm"
    _jm("object", "solo", "--state", "g:double:1.5", cwd=p)
    _jm("property", "solo", "bias", "--type", "double", cwd=p)
    _jm("module", "m", cwd=p)
    for obj, field in (("o", "g:double:1.5"), ("q", "h:double:2.0")):
        _jm("object", obj, "--module", "m", "--state", field, cwd=p)
    _jm("property", "o", "level", "--module", "m", "--type", "double",
        cwd=p)  # fmt: skip
    _jm("method", "q", "twice", "--module", "m", "--arg-type", "double",
        "--return-type", "double", cwd=p)  # fmt: skip
    _jm("apply", cwd=p)
    return p


@pytest.fixture
def project(objects: Path):
    """The shared project, its manifest restored after each test."""
    manifest = C.load(objects)
    yield objects
    C.save(objects, manifest)


def _put(root: Path, comp: str):
    def put(rows: "list[dict]") -> None:
        cfg = C.load(root)
        C.set_extra_methods(cfg, comp, rows)
        C.save(root, cfg)

    return put


class TestAnObject:
    def test_the_reported_trigger(self, project: Path):
        """The issue's own case: one row, one `error:` line."""
        _put(project, "solo")([{"name": "hand", "fn": "Solo_reset"}])
        before = _snapshot(project)
        r = run_cli("apply", cwd=project)
        assert r.returncode == 1, r.stdout + r.stderr
        errors = [
            ln for ln in r.stderr.splitlines() if ln.startswith("error:")
        ]
        assert errors == [
            "error: [[solo.extra_methods]] 'hand': fn 'Solo_reset' is"
            " already declared by the binding jm generates"
            " (native/src/solo/solo_ext.c), so the row's prototype for it"
            " conflicts with jm's own."
        ], r.stderr
        assert _snapshot(project) == before

    def test_standalone_every_name_its_binding_declares(self, project: Path):
        names = _declared_names([project / "native/src/solo/solo_ext.c"])
        # The floor that keeps the scan honest: an empty read is no pass.
        assert {
            "Solo_reset",
            "Solo_dealloc",
            "Solo_init",
            "Solo_getprop_bias",
            "SoloType",
            "PyInit_solo",
        } <= names, sorted(names)
        _refuses_each(
            project, names, _put(project, "solo"), "[[solo.extra_methods]]"
        )

    def test_module_object_every_name_its_unit_declares(self, project: Path):
        """`o`'s prototypes sit in the aggregator, above `o`'s fragment and
        before `q`'s, so a name only `q`'s fragment defines conflicts too."""
        names = _declared_names(sorted((project / "native/src/m").glob("*.c")))
        assert {
            "O_reset",
            "O_getprop_level",
            "Q_twice",
            "Q_dealloc",
            "QType",
            "PyInit_m",
        } <= names, sorted(names)
        _refuses_each(
            project, names, _put(project, "o"), "[[o.extra_methods]]"
        )

    def test_a_name_jm_does_not_generate_still_applies(self, project: Path):
        """The row's own prototype is not mistaken for the binding's."""
        _put(project, "o")([{"name": "hand", "fn": "O_hand"}])
        _jm("apply", cwd=project)


@pytest.fixture(scope="module")
def composer(tmp_path_factory) -> Path:
    """`test_composer_apply`'s composer, applied once."""
    root = tmp_path_factory.mktemp("gh2005c")
    _composer_project(root)
    _jm("apply", cwd=root)
    return root


class TestAComposer:
    MOD = "wfm_compose"

    def _put(self, root: Path):
        def put(rows: "list[dict]") -> None:
            cfg = C.load(root)
            cfg["module"][self.MOD]["extra_methods"] = rows
            C.save(root, cfg)

        return put

    def test_every_name_its_binding_declares(self, composer: Path):
        names = _declared_names(
            [composer / f"native/src/{self.MOD}/{self.MOD}_ext.c"]
        )
        assert {
            "Composer_dealloc",
            "Composer_close",
            "Synth_init",
            "Segment_add",
            "ComposerType",
            f"PyInit_{self.MOD}",
        } <= names, sorted(names)
        _refuses_each(
            composer,
            names,
            self._put(composer),
            f"[[module.{self.MOD}.extra_methods]]",
        )

    def test_a_row_with_no_fn_is_refused_not_a_traceback(self, composer):
        """gh-1190's rows had no refusal at all: no `fn` reached the
        emitter's `row["fn"]` as a KeyError."""
        self._put(composer)([{"name": "hand"}])
        before = _snapshot(composer)
        r = run_cli("apply", cwd=composer)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "Traceback" not in r.stderr, r.stderr
        assert (
            f"error: [[module.{self.MOD}.extra_methods]] 'hand': a row needs"
            " both `name` and `fn`." in r.stderr
        ), r.stderr
        assert _snapshot(composer) == before

    def test_a_name_jm_does_not_generate_still_applies(self, composer):
        """The row's own prototype is not mistaken for the binding's."""
        self._put(composer)([{"name": "draws", "fn": "Composer_draws"}])
        _jm("apply", cwd=composer)
