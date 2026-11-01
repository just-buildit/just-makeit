"""gh-2434: `migrate-to-fragments` on a doppler-sized project, made clean.

A dry run on doppler (45 `[module.X]` tables, 28 `[[enum]]`, one
`[codec]`) found three bugs, one per section here:

1. ``migrate-to-fragments --help`` migrated. Five commands never read their
   argv -- ``app`` and ``perf`` scaffolded on any argument -- and no command
   but ``adopt`` answered ``--help``. `_cli._main` now answers ``--help`` /
   ``-h`` for every command before its branch runs, from the command's own
   entries of the top-level usage, and a command that takes no argument
   refuses one (exit 2).
2. ``[[enum]]`` and ``[codec]`` were filed as ``objects/enum.toml`` and
   ``objects/codec.toml``: `_migrate` and `_split_objects` spelled their own
   copy of "the sections that are not components". They ask
   `_config.components` now, and no module outside `_config` may spell one.
3. After migrating, `status --check` reported the root ``CMakeLists.txt``
   and a shared package's ``__init__.py`` stale. A ``target_sources`` line
   for a core several components depend on follows the replay's order, so
   `_init.settle_aggregate_order` splices the root wiring from a replay as
   `apply` does; and `_object._merge_module_init` let a package sibling prune
   the names another module's ``reexports`` put in ``__all__``, so the file
   depended on module order (doppler's ``wfm`` lost 23 public names).

GATE: every command but a BUILD one answers ``--help`` / ``-h`` with its
      usage, exit 0, and leaves the tree byte-identical; any of them given a
      bogus argument fails and leaves the tree alone; migrating a project
      that declares every reserved section moves none of them; no module
      outside `_config` spells its own reserved-key tuple; and after
      migrating, `status --check` exits 0 and `apply` changes nothing.
"""

from __future__ import annotations

import ast
import hashlib
import re
import shutil
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _cli
from just_makeit import _config as C
from just_makeit._apply import walk

_SRC = Path(_cli.__file__).parent

#: Every command whose argv `_main` reads itself. A BUILD command hands its
#: argv on (`test` to pytest), so `--help` is its own parser's to answer.
_ANSWERED = sorted(c for c, k in _cli.COMMANDS.items() if k != _cli.BUILD)


def _snap(root: Path) -> "dict[str, str]":
    """Every file of the project's tree (`_apply.walk`, the walk `_undo`
    records), by relative path, to its content digest."""
    return {
        (base / n).relative_to(root).as_posix(): hashlib.sha1(
            (base / n).read_bytes()
        ).hexdigest()
        for base, names in walk(root)
        for n in names
    }


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout[-2000:], r.stderr[-2000:])


@pytest.fixture(scope="module")
def legacy(tmp_path_factory) -> Path:
    """A single-manifest project that every argument-free MUTATING command
    would change: `migrate-to-fragments` and `split-objects` have sections
    to move, `perf` is off, and `app` has an object to scaffold from."""
    base = tmp_path_factory.mktemp("gh2434")
    _ok("new", "p", "--no-fragments", "--object", "eng", cwd=base)
    root = base / "p"
    _ok("module", "m", cwd=root)
    _ok("object", "mo", "--module", "m", cwd=root)
    return root


@pytest.fixture
def proj(legacy: Path, tmp_path: Path) -> Path:
    root = tmp_path / "p"
    shutil.copytree(legacy, root)
    return root


# -- 1. arguments ------------------------------------------------------------


@pytest.mark.parametrize("flag", ["--help", "-h"])
@pytest.mark.parametrize("cmd", _ANSWERED)
def test_help_prints_the_usage_and_changes_nothing(cmd, flag, proj):
    before = _snap(proj)
    r = run_cli(cmd, flag, cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _snap(proj) == before, f"`jm {cmd} {flag}` changed the tree"
    first = r.stdout.splitlines()[1]
    assert r.stdout.startswith("Usage: just-makeit\n"), r.stdout
    assert first.split()[0].rstrip(",") == cmd, r.stdout


@pytest.mark.parametrize("cmd", _ANSWERED)
def test_a_bogus_argument_fails_and_changes_nothing(cmd, proj):
    before = _snap(proj)
    r = run_cli(cmd, "--gh2434-bogus", cwd=proj)
    assert r.returncode != 0, f"`jm {cmd} --gh2434-bogus` ran:\n{r.stdout}"
    assert _snap(proj) == before, f"`jm {cmd} --gh2434-bogus` wrote"


@pytest.mark.parametrize(
    "cmd", ["app", "perf", "migrate-to-fragments", "split-objects"]
)
def test_the_fixture_arms_the_gate(cmd, proj):
    """Each command that ignored its argv DOES change this fixture when run
    for real -- so the two tests above, unchanged trees and all, would see
    it run."""
    before = _snap(proj)
    _ok(cmd, cwd=proj)
    assert _snap(proj) != before


def test_an_argument_free_command_refuses_with_exit_2(proj):
    r = run_cli("migrate-to-fragments", "--dry-run", cwd=proj)
    assert r.returncode == 2, r.stdout + r.stderr
    assert r.stderr.count("error:") == 1, r.stderr
    assert "--dry-run" in r.stderr, r.stderr


# -- 2. reserved sections ----------------------------------------------------

_SECTIONS = """
[[enum]]
name = "mode"
values = ["slow", "fast"]

[codec.blue_keyword]
discriminant = "char"
scalar_collapse = true
[[codec.blue_keyword.entries]]
code = "A"
ctype = "char"
bytes = true
[[codec.blue_keyword.entries]]
code = "D"
ctype = "double"

[[group]]
name = "seq"
[[group.fields]]
name = "kind"
type = "int"
default = "0"

[template.f32_to_int]
params = ["elem", "Elem"]
module = "m"
arg_type = "float"
return_type = "{elem}"
class_name = "F32To{Elem}"

[[template.f32_to_int.instances]]
id = "f32_to_i16"
elem = "int16_t"
Elem = "I16"
"""


@pytest.fixture
def reserved(proj: Path) -> Path:
    """*proj* declaring every reserved section, in sync."""
    _ok("app", "--object", "eng", cwd=proj)
    toml = proj / C.FILENAME
    toml.write_text(toml.read_text() + _SECTIONS)
    _ok("apply", cwd=proj)
    present = set(C.load_manifest(proj))
    # `include` is the fragment list a migration WRITES; every other
    # reserved section is declared above, so a new one fails here first.
    assert set(C.RESERVED_SECTIONS) - {"include"} <= present, present
    return proj


@pytest.mark.parametrize("cmd", ["migrate-to-fragments", "split-objects"])
def test_no_reserved_section_is_filed_as_a_component(cmd, reserved):
    _ok(cmd, cwd=reserved)
    filed = [
        p.relative_to(reserved).as_posix()
        for d in ("objects", "modules")
        for p in (reserved / d).glob("*.toml")
        if p.stem in C.RESERVED_SECTIONS
    ]
    assert not filed, filed
    # Every one stays in the manifest -- but `[module.X]`, which
    # `migrate-to-fragments` moves to modules/ by design.
    kept = set(C.RESERVED_SECTIONS)
    if cmd == "migrate-to-fragments":
        kept.discard("module")
    manifest = C.load_manifest(reserved)
    assert kept <= set(manifest), set(manifest)
    # ...and `save` agrees: a mutating command moves nothing back.
    before = _snap(reserved)
    _ok("apply", cwd=reserved)
    assert _snap(reserved) == before


def test_save_files_a_new_group_in_the_manifest_only(tmp_path):
    """`save`'s routing asked its own copy of the list too, which lacked
    `group`: a `[[group]]` new to a split project was written to the
    manifest AND to objects/group.toml."""
    _ok("new", "p", "--object", "eng", cwd=tmp_path)
    root = tmp_path / "p"
    cfg = C.load(root)
    cfg["group"] = [{"name": "seq", "fields": [{"name": "k", "type": "int"}]}]
    C.save(root, cfg)
    assert not (root / "objects" / "group.toml").exists()
    assert C.load_manifest(root)["group"][0]["name"] == "seq"


def _reserved_tuples(path: Path) -> "list[str]":
    """Each literal collection of two or more strings in *path* that names
    `project` and nothing that is not a reserved section: a private copy of
    the list `_config.RESERVED_SECTIONS` declares."""
    reserved = set(C.RESERVED_SECTIONS)
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            continue
        words = [getattr(e, "value", None) for e in node.elts]
        if (
            len(words) >= 2
            and all(isinstance(w, str) for w in words)
            and "project" in words
            and set(words) <= reserved
        ):
            out.append(f"{path.name}:{node.lineno}: {tuple(words)}")
    return out


def test_no_module_spells_its_own_reserved_sections():
    found = [
        hit
        for path in sorted(_SRC.glob("*.py"))
        if path.name != "_config.py"
        for hit in _reserved_tuples(path)
    ]
    assert not found, (
        "ask _config.components() / RESERVED_SECTIONS instead:\n"
        + "\n".join(found)
    )


def test_the_scan_sees_a_private_copy(tmp_path):
    """The scan above is armed: the shape `_migrate` used to spell is one
    it reports."""
    f = tmp_path / "x.py"
    f.write_text('c = [k for k in m if k not in ("project", "module")]\n')
    assert _reserved_tuples(f)


# -- 3. what migrate leaves --------------------------------------------------


@pytest.fixture
def ordered(tmp_path: Path) -> Path:
    """A single-manifest project whose fragment load order differs from its
    declaration order in the two ways doppler's did: a core two components
    depend on, declared after one of them (and a module member depending on
    a standalone object); and a package `wfm` whose `reexports` line its
    sibling `wsib`, declared first, would merge after."""
    _ok("new", "p", "--no-fragments", cwd=tmp_path)
    root = tmp_path / "p"
    for args in (
        ("module", "wsib", "--package", "wfm"),
        ("module", "wfm"),
        ("module", "dsp.alpha"),
        ("object", "sobj", "--module", "wsib"),
        ("object", "wobj", "--module", "wfm"),
        ("object", "yy", "--module", "dsp.alpha"),
        ("object", "zdep"),
        ("object", "abase"),
        ("object", "mdep", "--module", "dsp.alpha"),
    ):
        _ok(*args, cwd=root)
    toml = root / C.FILENAME
    text = toml.read_text()
    for old, new in (
        ("[zdep]\n", '[zdep]\ndepends_on = ["abase", "wobj"]\n'),
        ("[mdep]\n", '[mdep]\ndepends_on = ["abase"]\n'),
        ("[module.wfm]\n", '[module.wfm]\nreexports = { helper = ["H"] }\n'),
    ):
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    toml.write_text(text)
    (root / "src" / "p" / "wfm" / "helper.py").write_text("H = 1\n")
    _ok("apply", cwd=root)
    _ok("status", "--check", cwd=root)
    return root


def test_migrate_leaves_nothing_for_apply(ordered):
    _ok("migrate-to-fragments", cwd=ordered)
    r = run_cli("status", "--check", cwd=ordered)
    assert r.returncode == 0, r.stdout
    before = _snap(ordered)
    _ok("apply", cwd=ordered)
    assert _snap(ordered) == before
    init = (ordered / "src" / "p" / "wfm" / "__init__.py").read_text()
    (names,) = re.findall(r"^__all__ = \[(.*)\]$", init, re.M)
    assert '"H"' in names, init
