"""gh-2101, gh-1895: `jm bind` on every tree a header can be bound in.

`jm bind <comp>` renders ``<comp>_ext.c`` and the ``.pyi`` from the header
alone, for a header the manifest does not declare -- and for one with no
manifest beside it at all. Two of those trees broke it:

- gh-1895: with no manifest, `bind` read the legacy (pre-schema-8) header
  path, and compared the header's symbols with the bare name, so a
  ``jm new``-scaffolded ``native/`` tree -- prefixed, and ``c_prefix``-ed
  by default -- was refused twice over; a ``--no-c-prefix`` one moved into
  the legacy path bound, and spelled the OTHER layout's ``#include``.
- gh-2101: ``--check`` read the ``_ext.c`` on disk without asking whether
  it was there, so a header not bound yet was a ``FileNotFoundError``
  traceback instead of a finding.

The trees are every combination of the two facts a header's project
decides -- its layout (prefixed or legacy) and its ``c_prefix`` (the
default or none) -- with and without the manifest, and with and without an
``_ext.c``. A scaffold is the oracle: what `jm new` + `jm apply` wrote is
what `bind` must write back, byte for byte, from the header alone.

GATE: on each tree, `jm bind` writes exactly the scaffold's binding;
      ``--check`` passes when it is there and, when it is not, exits 1 on
      one ``error:`` line naming the file and the command that writes it.
      With no manifest and no way to tell which header is the component's
      -- none, both layouts, another component's stem -- each mode refuses
      on one ``error:`` line. Every refusal leaves the tree byte-identical.
      A header's authored Doxygen binds the same with or without one.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest import mock

import pytest
from _jmrun import run_cli

from just_makeit import _config as C
from just_makeit import _incpath as INC

#: ``jm new`` flags, and the schema to scaffold at (None: the current one).
#: The legacy layout is the last schema before the prefixed one.
KINDS = {
    "prefixed": ((), None),
    "prefixed-no-c-prefix": (("--no-c-prefix",), None),
    "legacy": ((), INC.PREFIXED_SCHEMA - 1),
    "legacy-no-c-prefix": (("--no-c-prefix",), INC.PREFIXED_SCHEMA - 1),
}

EXT_C = "native/src/g/g_ext.c"

_SCAFFOLDS: "dict[str, Path]" = {}


def _tree(root: Path) -> "dict[str, bytes]":
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def _scaffold(base: Path, kind: str) -> Path:
    """``jm new p --object g`` of *kind*, built once per kind."""
    if kind not in _SCAFFOLDS:
        flags, schema = KINDS[kind]
        where = base / kind
        where.mkdir(parents=True)
        with mock.patch.object(
            C, "CURRENT_SCHEMA", schema or C.CURRENT_SCHEMA
        ):
            r = run_cli("new", "p", "--object", "g", *flags, cwd=where)
        assert r.returncode == 0, r.stdout + r.stderr
        _SCAFFOLDS[kind] = where / "p"
    return _SCAFFOLDS[kind]


@pytest.fixture
def tree(tmp_path_factory, tmp_path):
    """A copy of one kind's scaffold, made into one case's tree."""
    base = tmp_path_factory.getbasetemp() / "gh2101-scaffolds"

    def make(kind: str, manifest: bool, ext: bool) -> Path:
        src = _scaffold(base, kind)
        root = tmp_path / _id((kind, manifest, ext))
        shutil.copytree(src, root)
        assert INC.prefixed(root) == kind.startswith("prefixed")
        if manifest:
            # `bind` refuses a component the manifest declares (gh-2072):
            # undeclared, the scaffold is the hand-written header it is for.
            cfg = C.load(root)
            del cfg["g"]
            C.save(root, cfg)
        else:
            (root / C.FILENAME).unlink()
            for d in ("objects", "modules"):
                shutil.rmtree(root / d, ignore_errors=True)
        if not ext:
            (root / EXT_C).unlink()
        return root

    return make


def _refused(root: Path, argv, *route: str) -> None:
    """*argv* exits 1 on one ``error:`` line naming every part of *route*,
    with no traceback, and writes nothing."""
    before = _tree(root)
    r = run_cli(*argv, cwd=root)
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert r.returncode == 1 and len(errors) == 1, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    missing = [part for part in route if part not in errors[0]]
    assert not missing, f"the error does not name {missing}: {errors[0]}"
    assert _tree(root) == before, f"the refused {list(argv)} wrote"


_CASES = [
    (kind, manifest, ext)
    for kind in KINDS
    for manifest in (True, False)
    for ext in (True, False)
]


def _id(case) -> str:
    kind, manifest, ext = case
    return "-".join(
        (kind, "manifest" if manifest else "bare", "ext" if ext else "no-ext")
    )


@pytest.mark.parametrize("case", _CASES, ids=_id)
def test_bind_writes_the_scaffolds_binding(tree, case):
    root = tree(*case)
    scaffold = _tree(_SCAFFOLDS[case[0]])
    before = _tree(root)
    r = run_cli("bind", "g", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    # The binding and the stub, as the scaffold had them; nothing else moved.
    assert _tree(root) == {**before, EXT_C: scaffold[EXT_C]}


@pytest.mark.parametrize("case", _CASES, ids=_id)
def test_check_reports_without_writing(tree, case):
    root = tree(*case)
    _, _, ext = case
    if not ext:
        _refused(root, ("bind", "g", "--check"), EXT_C, "`jm bind g`")
        return
    before = _tree(root)
    r = run_cli("bind", "g", "--check", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _tree(root) == before
    # A stale binding is the other finding, on the same one line.
    with (root / EXT_C).open("a", encoding="utf-8") as fh:
        fh.write("/* edited */\n")
    _refused(root, ("bind", "g", "--check"), EXT_C, "`jm bind g`")


def _layouts(root: Path) -> "tuple[dict, dict]":
    """The owner whose layout holds `g`'s header in *root*, then the other."""
    held, other = sorted(
        INC.layouts("p"),
        key=lambda o: not INC.core_h(root, "g", o).is_file(),
    )
    assert INC.core_h(root, "g", held).is_file()
    return held, other


def _copy_header(root: Path, comp: str, owner: dict) -> None:
    """`g`'s header, copied to *comp*'s place in *owner*'s layout."""
    dst = INC.core_h(root, comp, owner)
    dst.parent.mkdir(parents=True)
    shutil.copy(INC.core_h(root, "g", _layouts(root)[0]), dst)


def _both_layouts(root: Path) -> None:
    _copy_header(root, "g", _layouts(root)[1])


def _no_header(root: Path) -> None:
    INC.core_h(root, "g", _layouts(root)[0]).unlink()


#: With no manifest, a tree that does not say which header is `g`'s.
_UNTELLABLE = {"both-layouts": _both_layouts, "no-header": _no_header}


@pytest.mark.parametrize("check", ((), ("--check",)), ids=("bind", "check"))
@pytest.mark.parametrize("why", sorted(_UNTELLABLE))
def test_bare_tree_that_cannot_say_is_refused(tree, why, check):
    root = tree("prefixed", manifest=False, ext=True)
    _UNTELLABLE[why](root)
    paths = [INC.core_rel("g", o) for o in INC.layouts("p")]
    _refused(root, ("bind", "g", *check), *paths)


@pytest.mark.parametrize("check", ((), ("--check",)), ids=("bind", "check"))
@pytest.mark.parametrize("kind", sorted(KINDS))
def test_bare_tree_refuses_another_components_header(tree, kind, check):
    """A stem is read back to a prefix only when it ends in the name asked
    for: `g`'s header under `h/` declares `g`, not `h`."""
    root = tree(kind, manifest=False, ext=True)
    _copy_header(root, "h", _layouts(root)[0])
    _refused(root, ("bind", "h", *check), "'h'")


#: An authored ``create()`` brief, in place of the one jm scaffolds.
AUTHORED = "Scale every sample by a fixed gain."


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_bare_tree_reads_the_headers_docs(tree, kind):
    """The header's own Doxygen reaches the binding with no manifest too.

    A manifest that does not declare `g` says nothing about it beyond the
    project's layout and prefix, which a bare tree says itself -- so the
    binding is the same with or without one, and carries the brief.
    """
    written = []
    for manifest in (True, False):
        root = tree(kind, manifest, ext=False)
        header = INC.core_h(root, "g", _layouts(root)[0])
        text = header.read_text(encoding="utf-8")
        assert text.count("@brief Create a g instance.") == 1
        header.write_text(
            text.replace("@brief Create a g instance.", f"@brief {AUTHORED}"),
            encoding="utf-8",
        )
        r = run_cli("bind", "g", cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
        files = _tree(root)
        written.append({k: files[k] for k in (EXT_C, "src/p/g.pyi")})
    with_manifest, bare = written
    assert AUTHORED in with_manifest[EXT_C].decode("utf-8")
    assert bare == with_manifest
