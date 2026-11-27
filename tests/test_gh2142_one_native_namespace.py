"""gh-2142: a module's cname and a component's name are one namespace.

`jm module X` refused a name a component already held, but nothing asked
the other order: `jm object filt` beside an existing `jm module filt` (or
`jm object a --module b` beside a module `a`) scaffolded into the module's
own `native/src/<cname>/`. In the flat layout gh-2459's occupied-directory
check happened to stop it, with a message about files rather than names;
in the nested layout the object's directory differs from the module's, so
`jm object a --module b` exited 0 and left a manifest `apply` and `status`
refused (the C symbol `A_CORE_H` derived twice). A hand-written manifest
holding the clash reached `apply`'s replay, which wrote the module, then
met the same occupied directory.

`_config.namespace_errors` is now the one rule: `load` and `save` ask it of
the whole manifest, and `jm module` / `jm object` ask it of the manifest as
it would read with their change, before anything is written.

GATE, for a flat and a dotted module, the object named for the module's
CNAME (read off `ModulePaths`, not listed), placed every way but as the
module's own collocated object -- standalone, and in another module:

- the CLI refuses it with one `error:` line naming the namespace, and the
  tree is byte-identical;
- the same manifest, assembled from two donor projects' fragments into a
  fresh `jm new`, is refused by `status --check` and `apply` the same way,
  the tree byte-identical;
- `jm module` refuses the other order and a second module sharing the
  cname, by the same rule.

The carve-out -- the collocated object still scaffolds, applies, BUILDS
and imports -- is `tests/test_gh1949_object_named_for_its_module.py`'s
build gate, on both its CLI and its `apply` path; `test_collocated_is_not_
refused` here is its fast half.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _incpath as INC
from just_makeit import load_manifest
from just_makeit._report import Refusal

#: A flat module and a dotted one: the two forms whose cname differs from
#: the id.
MODULES = ("filt", "dsp.filt")

#: Where the object goes, other than into its own module: standalone, or
#: into another module (named `other`, which no cname here equals).
PLACES = ("standalone", "other")


def _cname(module: str) -> str:
    return C.module_paths(module).cname


def _ok(root: Path, *argv: str) -> None:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (list(argv), (r.stdout + r.stderr)[-3000:])


def _tree(root: Path) -> "dict[str, bytes]":
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _refused(root: Path, *argv: str) -> None:
    """*argv* exits 1 with one `error:` line naming the namespace rule,
    and writes nothing."""
    before = _tree(root)
    r = run_cli(*argv, cwd=root)
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert r.returncode == 1, (list(argv), (r.stdout + r.stderr)[-3000:])
    assert len(errors) == 1, (list(argv), r.stderr[-3000:])
    assert "share one namespace" in errors[0], errors
    assert _tree(root) == before, list(argv)


def _object_argv(module: str, place: str) -> "list[str]":
    argv = ["object", _cname(module)]
    return argv + ["--module", "other"] if place == "other" else argv


def _project(base: Path, *cmds: "list[str]", layout: str = "flat") -> Path:
    """A fresh `jm new p` under *base*, declared in *layout*, then *cmds*."""
    base.mkdir()
    _ok(base, "new", "p")
    root = base / "p"
    manifest = root / C.FILENAME
    text = manifest.read_text(encoding="utf-8")
    assert text.count("[project]\n") == 1, text
    manifest.write_bytes(
        text.replace(
            "[project]\n", f'[project]\nlayout = "{layout}"\n'
        ).encode()
    )
    for argv in cmds:
        _ok(root, *argv)
    return root


@pytest.mark.parametrize("layout", INC.LAYOUTS)
@pytest.mark.parametrize("place", PLACES)
@pytest.mark.parametrize("module", MODULES)
def test_jm_object_refuses_a_module_cname(tmp_path, module, place, layout):
    """Every layout: under `nested` the object's directory is not the
    module's, so no occupied-directory check stood in for this one."""
    root = _project(
        tmp_path / "p", ["module", module], ["module", "other"], layout=layout
    )
    _refused(root, *_object_argv(module, place))


@pytest.mark.parametrize("layout", INC.LAYOUTS)
@pytest.mark.parametrize("place", PLACES)
@pytest.mark.parametrize("module", MODULES)
def test_a_manifest_holding_it_is_refused(tmp_path, module, place, layout):
    """The union of two valid projects' fragments: one declares the
    module, the other the object under the module's cname."""
    has_module = _project(tmp_path / "a", ["module", module])
    has_object = _project(
        tmp_path / "b", ["module", "other"], _object_argv(module, place)
    )
    root = _project(tmp_path / "fresh", layout=layout)
    for donor in (has_module, has_object):
        for path in C.manifest_files(donor)[1:]:
            dest = root / path.relative_to(donor)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
    _refused(root, "status", "--check")
    _refused(root, "apply")
    # Every reader, not only those two verbs -- `apply`'s replay would meet
    # the `jm object` refusal anyway -- so `load` is asked directly; and
    # `save`, the one place every manifest write passes through.
    before = _tree(root)
    with pytest.raises(Refusal, match="share one namespace"):
        load_manifest(root)
    clashing = C.load(has_object)
    clashing.setdefault("module", {})[module] = {"objects": []}
    with pytest.raises(Refusal, match="share one namespace"):
        C.save(root, clashing)
    assert _tree(root) == before


@pytest.mark.parametrize("module", MODULES)
def test_jm_module_refuses_the_other_order(tmp_path, module):
    root = _project(tmp_path / "p", ["object", _cname(module)])
    _refused(root, "module", module)


def test_jm_module_refuses_a_shared_cname(tmp_path):
    root = _project(tmp_path / "p", ["module", "dsp.filt"])
    _refused(root, "module", "dsp_filt")


@pytest.mark.parametrize("module", MODULES)
def test_collocated_is_not_refused(tmp_path, module):
    """The object of module X named for X's cname is X's own core
    (gh-1949): scaffolded and clean. The build is gh-1949's gate."""
    root = _project(
        tmp_path / "p",
        ["module", module],
        ["object", _cname(module), "--module", module],
    )
    _ok(root, "status", "--check")
