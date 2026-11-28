"""gh-2116: no command writes what a `no_generate` module owns.

A `no_generate` module is the author's: `jm apply` gives it an
``add_subdirectory`` line and writes nothing else of it -- not its
objects' ``_core.c`` / ``_core.h``, not its binding, ``__init__.py``,
``.pyi`` or tests. Two things broke that:

- `jm apply` replayed the members of such a module's objects (methods,
  properties, warnings, errors, views) into a scratch tree it had never
  scaffolded those objects in, and exited 1 on ``object 'o' not found in
  module 'mod'``. Two `apply` passes then read the same objects -- the
  ``impl`` patch, the ``destroy`` promotion, the accessor declarations
  offered to a module object's header, and `_csym`'s property accessors,
  whose names, owned by no render, made the author's own declaration of
  one a ``c_prefix`` collision.
- every member verb -- `jm method`, `property`, `warning`, `error`,
  `record`, `view`, `function`, and `object --module` -- rewrote the
  module's hand-written files, and `jm perf` its objects' headers.

GATE: every command `_cli.COMMANDS` classifies MUTATING -- read from the
      one classification, so a command added later fails
      `test_every_mutating_command_has_a_case` until it has one here -- is
      run on a project whose module ``mod`` (object ``o``, a method, a
      property, a warning, an error and a function) its author then made
      `no_generate` and wrote in. Each case either

      - REFUSES: exits non-zero, prints one ``error:`` line naming
        `no_generate`, and leaves the whole tree byte-identical; or
      - SUCCEEDS without touching the module: every file the module owns is
        byte-identical, `jm apply` then exits 0 and leaves them so too, and
        a second `apply` changes nothing at all.

      `jm remove` of a member is held to the same, ratcheted as a strict
      xfail: `_remove.py` belongs to another lane of the wave that fixed
      this, which fixes it there and deletes the mark.
"""

from __future__ import annotations

import ast
import re
import shutil
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _cli
from just_makeit import _config as C

#: What the author wrote in each of the module's files, after declaring it
#: `no_generate`. The byte-identical check covers every owned file; these
#: make the loss of an edit visible in a failure's diff too.
AUTHOR_C = "/* gh-2116: written by the author */\n"
AUTHOR_PY = "# gh-2116: written by the author\n"

#: The files the author's edit goes in, relative to the project root.
EDITED = {
    "native/src/o/o_core.c": AUTHOR_C,
    "native/inc/p/o/o_core.h": AUTHOR_C,
    "native/src/mod/mod_ext_o.c": AUTHOR_C,
    "native/src/mod/mod_ext.c": AUTHOR_C,
    "native/src/mod/CMakeLists.txt": AUTHOR_PY,
    "src/p/mod/__init__.py": AUTHOR_PY,
    "src/p/mod/mod.pyi": AUTHOR_PY,
}

#: Why `jm remove` of a member is a strict xfail: a ratchet, deleted by
#: the fix in `_remove.py`.
_REMOVE_WHY = (
    "gh-2116: `jm remove` of a `no_generate` module's member rewrites the"
    " module's files; `_remove.py` is another lane's"
)

#: Each MUTATING command -> the cases run on the shape. Held to
#: `_cli.COMMANDS` by `test_every_mutating_command_has_a_case`.
CASES: "dict[str, tuple]" = {
    "adopt": (("adopt", "o"), ("adopt", "--module", "mod")),
    "add": (
        ("add", "--object", "o", "--state", "y:double:0", "--force"),
        ("add", "--object", "o", "--param", "k:int:4", "--force"),
    ),
    "app": (("app", "--target", "c", "--object", "o", "--name", "tool"),),
    "apply": (("apply",),),
    "bind": (("bind", "o"),),
    "ci": (("ci",),),
    "config": (("config", "version", "0.2.0"),),
    "error": (
        (
            "error",
            "o",
            "--category",
            "RuntimeError",
            "--message",
            "bad2",
            "--module",
            "mod",
        ),
    ),  # fmt: skip
    "function": (
        (
            "function",
            "g",
            "--module",
            "mod",
            "--param",
            "x:double",
            "--return-type",
            "double",
        ),
    ),  # fmt: skip
    "method": (
        ("method", "o", "m2", "--module", "mod"),
        ("method", "o", "m2"),
    ),
    "migrate-to-fragments": (("migrate-to-fragments",),),
    "module": (("module", "other"),),
    "object": (("object", "o2", "--module", "mod"),),
    "perf": (("perf",),),
    "property": (
        ("property", "o", "p2", "--type", "double", "--module", "mod"),
    ),
    "record": (("record", "o", "sample", "--type", "float _Complex"),),
    "regenerate": (("regenerate", "o", "--force"),),
    "remove": (
        ("remove", "method", "m", "--object", "o", "--force"),
        ("remove", "property", "lvl", "--object", "o", "--force"),
        ("remove", "function", "f", "--module", "mod", "--force"),
    ),
    "split-objects": (("split-objects",),),
    "upgrade": (("upgrade",),),
    "view": (
        ("view", "o", "O2", "--module", "mod", "--create-fn", "o_create"),
    ),
    "warning": (
        (
            "warning",
            "o",
            "--condition",
            "gain",
            "--message",
            "hot2",
            "--module",
            "mod",
        ),
    ),  # fmt: skip
}

#: Each jm module that calls `_regenerate.refuse_hand_written_member` -> the
#: verb whose cases must REFUSE, not merely leave the module alone: a
#: member they declared would sit in the manifest with nothing to read it.
#: Held to the source by `test_every_member_refusal_has_a_verb`.
MEMBER_VERBS: "dict[str, str]" = {
    "just_makeit._error": "error",
    "just_makeit._function": "function",
    "just_makeit._method": "method",
    "just_makeit._object": "object",
    "just_makeit._property": "property",
    "just_makeit._recorddecl": "record",
    "just_makeit._view": "view",
    "just_makeit._warning": "warning",
}

#: A file the module owns: anything in its own or its objects' source,
#: header or Python directories, and their C tests and benchmarks.
_OWNED = re.compile(
    r"^(native/src/(mod|o)/|native/inc/p/(mod|o)/|src/p/mod/"
    r"|native/(tests|benchmarks)/(test|bench)_(mod|o)_)"
)


def _ok(root: Path, *argv: str) -> None:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (argv, (r.stdout + r.stderr)[-3000:])


@pytest.fixture(scope="module")
def shape(tmp_path_factory) -> Path:
    """Module ``mod`` with object ``o`` and one of each member, which the
    author then declared `no_generate` -- with a reason, as `status`
    requires -- and wrote in. Not applied since: that is a case."""
    base = tmp_path_factory.mktemp("gh2116")
    _ok(base, "new", "p")
    root = base / "p"
    for argv in (
        ("module", "mod"),
        ("object", "o", "--module", "mod"),
        ("method", "o", "m", "--module", "mod"),
        ("property", "o", "lvl", "--type", "double", "--module", "mod"),
        ("warning", "o", "--condition", "gain", "--message", "hot",
         "--module", "mod"),
        ("error", "o", "--category", "ValueError", "--message", "bad",
         "--module", "mod"),
        ("function", "f", "--module", "mod", "--param", "x:double",
         "--return-type", "double"),
    ):  # fmt: skip
        _ok(root, *argv)
    cfg = C.load(root)
    cfg["module"]["mod"]["no_generate"] = "true"
    cfg["module"]["mod"]["no_generate_reason"] = "hand-written by gh-2116"
    # Written by hand since, each the input of an `apply` pass that patches
    # an object's sacred files: the `impl` lift into `step()`, the `int`
    # destructor, and an accessor declaration offered to the header.
    o = cfg["o"]
    o["impl"] = "return x;\n"
    o["destroy"] = {"returns": "int"}
    o.setdefault("properties", []).append({"name": "p9", "type": "double"})
    C.save(root, cfg)
    for rel, line in EDITED.items():
        with (root / rel).open("a", encoding="utf-8", newline="\n") as f:
            f.write(line)
    return root


def _tree(root: Path) -> "dict[str, bytes]":
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _changed(before: dict, after: dict, owned: bool = False) -> list:
    return sorted(
        rel
        for rel in set(before) | set(after)
        if before.get(rel) != after.get(rel)
        and (not owned or _OWNED.match(rel))
    )


def test_the_shape_owns_what_it_edited(shape):
    """The ownership pattern covers every edited file, and the shape holds
    the author's edits -- so the checks below are about real files."""
    tree = _tree(shape)
    for rel, line in EDITED.items():
        assert _OWNED.match(rel), rel
        assert tree[rel].decode().endswith(line), rel


def test_every_mutating_command_has_a_case():
    """A MUTATING command added to jm fails here until it has a case."""
    mutating = {c for c, k in _cli.COMMANDS.items() if k == _cli.MUTATING}
    assert set(CASES) == mutating, (
        f"no case: {sorted(mutating - set(CASES))};"
        f" not a mutating command: {sorted(set(CASES) - mutating)}"
    )


def _member_refusal_callers() -> "set[str]":
    """Every jm module whose code calls ``refuse_hand_written_member``."""
    src = Path(C.__file__).resolve().parent
    out = set()
    for path in sorted(src.rglob("*.py")):
        rel = path.relative_to(src.parent).with_suffix("").parts
        modname = ".".join(rel)
        if modname.startswith(("just_makeit.templates", "just_makeit.ex")):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if name == "refuse_hand_written_member":
                    out.add(modname)
    return out


def test_every_member_refusal_has_a_verb():
    """A new caller of the member refusal fails here until its verb is in
    `MEMBER_VERBS`, so its cases are held to refusing."""
    assert set(MEMBER_VERBS) == _member_refusal_callers()
    assert set(MEMBER_VERBS.values()) <= set(CASES)


def _params() -> list:
    """Every case, the ratcheted ones marked strict xfail."""
    return [
        pytest.param(
            case,
            id=" ".join(case),
            marks=(
                [pytest.mark.xfail(strict=True, reason=_REMOVE_WHY)]
                if verb == "remove"
                else []
            ),
        )
        for verb, cases in CASES.items()
        for case in cases
    ]


@pytest.mark.parametrize("argv", _params())
def test_a_command_leaves_a_no_generate_module_alone(shape, tmp_path, argv):
    root = tmp_path / "p"
    shutil.copytree(shape, root, symlinks=True)
    before = _tree(root)

    r = run_cli(*argv, cwd=root)
    out = (r.stdout + r.stderr)[-3000:]
    after = _tree(root)
    if argv[0] in MEMBER_VERBS.values():
        assert r.returncode != 0, f"{list(argv)} declared a member: {out}"
    if r.returncode != 0:
        errors = [
            ln for ln in r.stderr.splitlines() if ln.startswith("error:")
        ]
        assert len(errors) == 1, (argv, out)
        assert "`no_generate`" in errors[0], (argv, errors[0])
        changed = _changed(before, after)
        assert not changed, f"{list(argv)} refused and changed {changed}"
    else:
        changed = _changed(before, after, owned=True)
        assert not changed, (
            f"{list(argv)} rewrote the `no_generate` module's {changed}"
        )

    a = run_cli("apply", cwd=root)
    assert a.returncode == 0, (argv, (a.stdout + a.stderr)[-3000:])
    applied = _tree(root)
    changed = _changed(before, applied, owned=True)
    assert not changed, (
        f"`jm apply` after {list(argv)} rewrote the `no_generate` module's"
        f" {changed}"
    )
    again = run_cli("apply", cwd=root)
    assert again.returncode == 0, (again.stdout + again.stderr)[-3000:]
    changed = _changed(applied, _tree(root))
    assert not changed, f"a second `jm apply` changed {changed}"
