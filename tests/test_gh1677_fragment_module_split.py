"""gh-1677: `jm apply <fragment>` routes `module = "X"` wherever X lives.

`_apply._compose_fragment` checked a fragment's ``module = "X"`` against
the CENTRAL manifest alone (`C.load_manifest`) and then wired the object into
``[module.X].objects`` with a regex over that one file's text. Two members of
one class followed:

- the split layout -- `jm new`'s default -- declares ``[module.X]`` in
  ``modules/X.toml``, so the check refused a module the project has:
  ``[module.m] is not in just-makeit.toml. Defined modules: ['(none)']``;
- a dotted module id is spelled ``[module."dsp.filters"]``, which the regex
  never matched, so on a single-manifest project the wiring silently did
  nothing and `apply` exited 0 having built the object as a STANDALONE
  extension (``native/src/extra``, ``src/q/extra.pyi``).

Both now go through the one reader and the one writer every other verb
uses: the check reads the merged `C.load`, and the wiring is
`C.add_to_module` + `C.save`, which writes ``[module.X]`` back to whichever
file declares it. A refusal after composing undoes that file too.
"""

from __future__ import annotations

from pathlib import Path

from _jmrun import run_cli

from just_makeit import _config as C
from just_makeit import _textio

_EXTRA = (
    '[extra]\nmodule = "{mod}"\narg_type = "float"\nreturn_type = "float"\n'
)


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout + r.stderr)


def _project(tmp_path: Path, mod: str, *new_flags: str) -> Path:
    _ok("new", "q", *new_flags, cwd=tmp_path)
    root = tmp_path / "q"
    _ok("module", mod, cwd=root)
    return root


def _incoming(tmp_path: Path, mod: str, extra: str = "") -> Path:
    p = tmp_path / "extra.toml"
    _textio.write_text(p, _EXTRA.format(mod=mod) + extra)
    return p


def _is_module_object(root: Path, mod: str) -> None:
    """Wired into the module, and built as one of its objects -- not as a
    standalone extension of its own. (A module object keeps its core in
    ``native/src/extra/``; the standalone glue there is what must not be.)"""
    cname = C.module_paths(mod).cname
    assert C.module_objects(C.load(root), mod) == ["extra"]
    assert (root / "native" / "src" / cname / f"{cname}_ext_extra.c").is_file()
    assert not (root / "native" / "src" / "extra" / "extra_ext.c").exists()
    assert not (root / "src" / "q" / "extra.pyi").exists()


def test_split_layout_routes_into_the_module_fragment(tmp_path: Path) -> None:
    root = _project(tmp_path, "m")
    assert (root / "modules" / "m.toml").is_file()

    r = run_cli("apply", str(_incoming(tmp_path, "m")), cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    _is_module_object(root, "m")
    # Written where [module.m] is declared; the manifest grows no copy of
    # it, which `load` would refuse as a module declared twice.
    assert "extra" in (root / "modules" / "m.toml").read_text()
    assert "module" not in C.load_manifest(root)


def test_a_dotted_module_is_wired_not_built_standalone(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, "dsp.filters", "--no-fragments")

    r = run_cli("apply", str(_incoming(tmp_path, "dsp.filters")), cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    _is_module_object(root, "dsp.filters")


def test_an_undeclared_module_is_still_refused(tmp_path: Path) -> None:
    root = _project(tmp_path, "m")

    r = run_cli("apply", str(_incoming(tmp_path, "nope")), cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "[module.nope]" in r.stderr, r.stderr
    # The merged set, so the modules the project does have are named.
    assert "Defined modules: ['m']" in r.stderr, r.stderr
    assert not (root / "objects" / "extra.toml").exists()


def _snapshot(root: Path) -> "dict[str, bytes]":
    return {
        p.relative_to(root).as_posix(): (
            p.read_bytes() if p.is_file() else b"<dir>"
        )
        for p in sorted(root.rglob("*"))
        if "__pycache__" not in p.parts
    }


def test_a_refusal_after_composing_restores_the_module_fragment(
    tmp_path: Path,
) -> None:
    """gh-1660's guarantee, on the file this fix newly writes: composing
    wires ``modules/m.toml`` before the refusals are asked, so a refusal
    puts it back byte for byte."""
    root = _project(tmp_path, "m")
    _ok("apply", cwd=root)
    before = _snapshot(root)

    bad = (
        '\n[[extra.methods]]\nname = "go"\narg_type = "float"\n'
        'return_type = "notatype"\n'
    )
    r = run_cli("apply", str(_incoming(tmp_path, "m", bad)), cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "unsupported type" in r.stderr, r.stderr
    assert _snapshot(root) == before
