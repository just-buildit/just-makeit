"""gh-2843 item 2: a member verb keeps a file `status_allow` names.

`[project] status_allow` lists files the author maintains by hand. `jm
apply` never rewrites one (gh-441), but the member verbs wrote through
their own route: `jm object b --module m` rewrote the module's
CMakeLists (dropping an author's line), its aggregator and its `.pyi`, and
a standalone object's verbs rewrote its `_ext.c` and `.pyi`; `jm add`,
which deletes the component's files and re-applies, rebuilt every one.

Now the one write guard every member verb asks
(`_config.may_write_module_file`, gh-2116) has a second clause: an
EXISTING file `status_allow` names is kept, with a ``keep`` line beside the
verb's ``update`` lines. A file the verb creates is nobody's yet, and the
sacred ``_core.h`` / ``_core.c`` still take the declaration a verb was asked
to add.

GATE: with every file allowed, each member verb -- module and standalone
      -- changes no file that `jm apply` of the same manifest leaves alone;
      the issue's repro keeps each allowed file byte for byte and still
      updates one that is not; a file the verb creates is written; and the
      sacred header still takes a new method's declaration.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _textio

SACRED = ("_core.h", "_core.c")


def _ok(root: Path, *argv: str) -> str:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (argv, (r.stdout + r.stderr)[-3000:])
    return r.stdout


def _tree(root: Path) -> "dict[str, bytes]":
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "build" not in p.parts
    }


def _allow(root: Path, patterns: "list[str]") -> None:
    cfg = C.load(root)
    cfg["project"]["status_allow"] = patterns
    C.save(root, cfg)


@pytest.fixture(scope="module")
def base(tmp_path_factory) -> Path:
    """Module `m` with object `k` and function `g`; standalone `a`."""
    d = tmp_path_factory.mktemp("base")
    _ok(d, "new", "p")
    root = d / "p"
    _ok(root, "module", "m")
    _ok(root, "object", "k", "--module", "m")
    _ok(root, "object", "a")
    _ok(root, "function", "g", "--module", "m", "--param", "x:double",
        "--return-type", "double")  # fmt: skip
    _ok(root, "warning", "a", "--condition", "c0", "--message", "cold")
    return root


_M = ("--module", "m")
_D = ("--arg-type", "double", "--return-type", "double")

#: Every member verb, on a module member and on a standalone object.
VERBS = {
    "object-in-module": ("object", "b", *_M),
    "object-standalone": ("object", "s"),
    "method-module": ("method", "k", "run2", *_M, *_D),
    "method-standalone": ("method", "a", "run2", *_D),
    "method-varargs-module": ("method", "k", "call", *_M, "--varargs"),
    "method-varargs-standalone": ("method", "a", "call", "--varargs"),
    "property-module": ("property", "k", "gain", "--type", "double", *_M),
    "property-standalone": ("property", "a", "gain", "--type", "double"),
    "warning-module": ("warning", "k", "--condition", "c1", "--message",
                       "hot", *_M),
    "warning-standalone": ("warning", "a", "--condition", "c1", "--message",
                           "hot"),
    "error-standalone": ("error", "a", "--category", "ValueError",
                         "--message", "bad"),
    "function": ("function", "h", *_M, "--param", "x:double",
                 "--return-type", "double"),
    "view": ("view", "k", "V", *_M, "--create-fn", "k_mk_v"),
    "add-state": ("add", "--object", "a", "--state", "z:double:0",
                  "--force"),
    "remove-function": ("remove", "function", "g", *_M, "--force"),
    "remove-warning": ("remove", "warning", "c0", "--object", "a",
                       "--force"),
}  # fmt: skip


def _manifests(root: Path) -> "dict[str, bytes]":
    return {
        k: v
        for k, v in _tree(root).items()
        if k == C.FILENAME or k.startswith(("objects/", "modules/"))
    }


@pytest.mark.parametrize("verb", sorted(VERBS))
def test_a_verb_keeps_what_apply_keeps(base, tmp_path, verb):
    """Derived from `apply`, not listed: what the verb changes under
    ``status_allow = ["*"]`` is a subset of what `apply` changes for the
    same manifest."""
    root = tmp_path / "v"
    shutil.copytree(base, root)
    _allow(root, ["*"])
    before = _tree(root)
    out = _ok(root, *VERBS[verb])
    after = _tree(root)

    applied = tmp_path / "a"
    shutil.copytree(base, applied)
    _allow(applied, ["*"])
    for rel, data in _manifests(root).items():
        (applied / rel).parent.mkdir(parents=True, exist_ok=True)
        (applied / rel).write_bytes(data)
    _ok(applied, "apply")
    by_apply = _tree(applied)

    def changed(t: "dict[str, bytes]") -> "set[str]":
        return {
            k
            for k in before
            if k in t
            and t[k] != before[k]
            and not k.endswith(".toml")
            and not k.endswith(SACRED)
        }

    assert changed(after) <= changed(by_apply), (
        sorted(changed(after) - changed(by_apply)),
        out,
    )


def test_the_issues_repro_keeps_each_allowed_file(base, tmp_path):
    root = tmp_path / "p"
    shutil.copytree(base, root)
    cml = root / "native" / "src" / "m" / "CMakeLists.txt"
    _textio.write_text(
        cml, cml.read_text(encoding="utf-8") + 'message(STATUS "mine")\n'
    )
    # A glob, as an author writes one: it names the aggregator, and the
    # new object's fragment too.
    _allow(root, ["native/src/m/CMakeLists.txt", "native/src/m/m_ext*.c",
                  "src/p/m/m.pyi"])  # fmt: skip
    kept = ["native/src/m/CMakeLists.txt", "native/src/m/m_ext.c",
            "src/p/m/m.pyi"]  # fmt: skip
    before = _tree(root)

    out = _ok(root, "object", "b", *_M)

    after = _tree(root)
    for rel in kept:
        assert after[rel] == before[rel], rel
        assert f"keep    {root / rel}" in out, out
    # A file the list does not name is still jm's.
    init = "src/p/m/__init__.py"
    assert after[init] != before[init] and "B" in after[init].decode()
    # And a file the verb creates is nobody's yet: written, though the
    # glob names it.
    assert "native/src/m/m_ext_b.c" not in before
    assert "native/src/m/m_ext_b.c" in after


def test_the_sacred_header_still_takes_the_declaration(base, tmp_path):
    root = tmp_path / "p"
    shutil.copytree(base, root)
    _allow(root, ["*"])
    _ok(root, "method", "a", "run2", *_D)
    core_h = (root / "native/inc/p/a/a_core.h").read_text(encoding="utf-8")
    assert "run2" in core_h, core_h


def test_a_new_standalone_objects_files_are_written(base, tmp_path):
    root = tmp_path / "p"
    shutil.copytree(base, root)
    _allow(root, ["*"])
    _ok(root, "object", "s")
    assert (root / "native/src/s/s_ext.c").is_file()
    assert (root / "src/p/s.pyi").is_file()
