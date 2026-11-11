"""gh-2608: a renamed ``reexports`` key takes its old import with it.

Renaming ``[module.X.reexports] cic_design`` to ``_cic_design`` (doppler's
#3004) made `jm apply` add ``from ._cic_design import ...`` and keep
``from .cic_design import ...``: the old module was gone, so the package no
longer imported, and `status --check` agreed with the apply that left it.
Nothing in the statement tells jm's from an author's (gh-342), so jm keeps
a record, ``[module.X] applied_reexports``, of the submodules whose import
it rendered (`_config.record_applied`, gh-2459's rule): a submodule on the
record that the manifest no longer declares loses its jm-shaped statement.
A line from before the record existed is never touched.

Two defects in the same code ride along:

* `_config`'s writers took a ``reexports`` key starting with ``_`` for
  runtime state, so a command that rewrote the table deleted
  ``_cic_design`` from the manifest. `_config.is_runtime_key` is the one
  answer `_sync`, `_dump` (via `_declared`) and `_strip_private` ask.
* gh-2616: a standalone object's changed ``class_name`` kept the old
  ``from .<comp> import Old`` in the package ``__init__.py`` and added one
  more ``from .<comp> import New`` on every apply (`_init._splice_init_py`
  read only the first statement).

Sabotage (scripts/sabotage.py) each fix and a test here goes red.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

from _jmrun import run_cli
from just_makeit import _config as C


def _jm(*args: str, cwd: Path, ok: bool = True):
    r = run_cli(*args, cwd=cwd)
    if ok:
        assert r.returncode == 0, (
            f"jm {' '.join(args)}\n{r.stdout}\n{r.stderr}"
        )
    return r


def _scaffold(root: Path, name: str) -> Path:
    _jm("new", name, cwd=root)
    proj = root / name
    _jm("module", "sig", cwd=proj)
    _jm("object", "mix", "--module", "sig", cwd=proj)
    return proj


def _set_reexports(proj: Path, sub: str) -> None:
    frag = proj / "modules" / "sig.toml"
    text = re.sub(
        r"\n\[module\.sig\.reexports\]\n[^\n]*\n",
        "\n",
        frag.read_text(encoding="utf-8"),
    )
    frag.write_text(
        text + f'\n[module.sig.reexports]\n{sub} = ["a"]\n', encoding="utf-8"
    )


def _imports(proj: Path, pkg: str) -> None:
    """Import ``<pkg>.sig`` from the tree, its extension stood in for by a
    plain module: the subject is the ``__init__.py``'s own statements."""
    sig = proj / "src" / pkg / "sig"
    (sig / "sig.py").write_text("class Mix:\n    pass\n", encoding="utf-8")
    sys.path.insert(0, str(proj / "src"))
    try:
        mod = importlib.import_module(f"{pkg}.sig")
        assert mod.a == 1
    finally:
        sys.path.remove(str(proj / "src"))
        for name in [m for m in sys.modules if m.split(".")[0] == pkg]:
            del sys.modules[name]


def test_renamed_key_drops_its_old_import(tmp_path: Path) -> None:
    pkg = "rnm2608"
    proj = _scaffold(tmp_path, pkg)
    init = proj / "src" / pkg / "sig" / "__init__.py"
    _set_reexports(proj, "cic_design")
    _jm("apply", cwd=proj)
    assert "from .cic_design import a  # noqa: E402" in init.read_text()
    assert C.applied_reexports(C.load(proj), "sig") == ["cic_design"]

    _set_reexports(proj, "_cic_design")
    (proj / "src" / pkg / "sig" / "_cic_design.py").write_text("a = 1\n")
    # The drift reporter sees the stale statement, through its replay.
    r = _jm("status", "--check", cwd=proj, ok=False)
    assert r.returncode != 0
    assert f"src/{pkg}/sig/__init__.py" in r.stdout

    _jm("apply", cwd=proj)
    text = init.read_text()
    assert "from .cic_design" not in text, text
    assert "from ._cic_design import a  # noqa: E402" in text
    assert C.applied_reexports(C.load(proj), "sig") == ["_cic_design"]
    assert _jm("status", "--check", cwd=proj).returncode == 0
    _imports(proj, pkg)


def test_a_line_from_before_the_record_is_untouched(tmp_path: Path) -> None:
    proj = _scaffold(tmp_path, "old2608")
    init = proj / "src" / "old2608" / "sig" / "__init__.py"
    _set_reexports(proj, "cic_design")
    _jm("apply", cwd=proj)
    frag = proj / "modules" / "sig.toml"
    # As an older jm left it: the import, and no record of rendering it.
    frag.write_text(
        re.sub(
            r"^applied_reexports = [^\n]*\n",
            "",
            frag.read_text(encoding="utf-8"),
            flags=re.M,
        ),
        encoding="utf-8",
    )
    assert "applied_reexports" not in frag.read_text()
    _set_reexports(proj, "_cic_design")
    _jm("apply", cwd=proj)
    text = init.read_text()
    assert "from .cic_design import a  # noqa: E402" in text
    assert "from ._cic_design import a  # noqa: E402" in text


def test_a_private_reexport_key_survives_a_save(tmp_path: Path) -> None:
    proj = _scaffold(tmp_path, "sv2608")
    _set_reexports(proj, "_cic_design")
    # A command that rewrites the module's table, through `_sync`.
    _jm(
        "function",
        "fn",
        "--module",
        "sig",
        "--param",
        "x:double",
        "--return-type",
        "double",
        cwd=proj,
    )
    cfg = C.load(proj)
    assert "_cic_design" in C.module_reexports(cfg, "sig")
    cfg["module"]["sig"]["reexports"]["other"] = ["z"]
    C.save(proj, cfg)
    assert set(C.module_reexports(C.load(proj), "sig")) == {
        "_cic_design",
        "other",
    }
    # And the whole-manifest writer, `_dump`, keeps it while it drops the
    # runtime state beside it.
    cfg["module"]["sig"]["_scratch"] = 1
    back = tomllib.loads(C._dump(cfg))
    assert "_cic_design" in back["module"]["sig"]["reexports"]
    assert "_scratch" not in back["module"]["sig"]


@pytest.mark.parametrize("new", ["Renamed", "_Priv"])
def test_a_changed_class_name_converges(tmp_path: Path, new: str) -> None:
    # gh-2616
    _jm("new", "cn2608", cwd=tmp_path)
    proj = tmp_path / "cn2608"
    _jm("object", "priv", cwd=proj)
    frag = proj / "objects" / "priv.toml"
    frag.write_text(
        re.sub(
            r"^\[priv\]\n",
            f'[priv]\nclass_name = "{new}"\n',
            frag.read_text(encoding="utf-8"),
            count=1,
            flags=re.M,
        ),
        encoding="utf-8",
    )
    init = proj / "src" / "cn2608" / "__init__.py"
    _jm("apply", cwd=proj)
    _jm("apply", cwd=proj)
    text = init.read_text()
    stmts = re.findall(r"^from \.priv import [^\n]*$", text, re.M)
    assert stmts == [f"from .priv import {new}  # noqa: E402"], text
    assert '"Priv"' not in text, text
    assert _jm("status", "--check", cwd=proj).returncode == 0


def test_splice_reads_every_statement_of_the_component(
    tmp_path: Path,
) -> None:
    # gh-2616: a name in the SECOND `from .<comp>` statement is present; the
    # splice must not add it again on every apply.
    from just_makeit._init import _splice_init_py

    init = tmp_path / "__init__.py"
    src = (
        "from .priv import A  # noqa: E402\n"
        "from .priv import B  # noqa: E402\n\n"
        '__all__ = ["A", "B"]\n'
    )
    init.write_text(src, encoding="utf-8")
    assert _splice_init_py(init, "priv", "B") is False
    assert init.read_text(encoding="utf-8") == src


@pytest.mark.parametrize("first,then", [("sig", "sink"), ("sink", "sig")])
def test_a_reexport_moved_to_a_package_sibling_stays(
    tmp_path: Path, first: str, then: str
) -> None:
    # The record names `h` for the module that USED to re-export it; the
    # sibling sharing its package re-exports it now, so the line is live.
    pkg = f"mv2608{first}"
    proj = _scaffold(tmp_path, pkg)
    _jm("module", "sink", cwd=proj)
    _jm("object", "snk", "--module", "sink", cwd=proj)
    mods = proj / "modules"
    with (mods / "sink.toml").open("a") as f:
        f.write('package = "sig"\n')
    base = {m: (mods / f"{m}.toml").read_text() for m in ("sig", "sink")}

    def hold(mod: str) -> None:
        for m, text in base.items():
            extra = f'\n[module.{m}.reexports]\nh = ["a"]\n'
            (mods / f"{m}.toml").write_text(text + (extra if m == mod else ""))

    hold(first)
    _jm("apply", cwd=proj)
    hold(then)
    _jm("apply", cwd=proj)
    text = (proj / "src" / pkg / "sig" / "__init__.py").read_text()
    assert re.findall(r"^from \.h import a  # noqa: E402$", text, re.M) == [
        "from .h import a  # noqa: E402"
    ], text
    assert _jm("status", "--check", cwd=proj).returncode == 0
