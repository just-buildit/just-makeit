"""gh-2437: `migrate-to-fragments` and `split-objects` keep every comment.

Both commands rebuilt each file they wrote from plain dicts with `_dump`:
every fragment, and the trimmed root manifest. That deleted every comment
in the manifest -- all 621 lines of doppler's, the ones on ``[project]``
keys (which stay in that file) included, and none reached
``modules/*.toml``. The comments are load-bearing: they record why a key
holds its value.

Both now cut the document with `_config.split_manifest`: each moved table
leaves with its own comments, byte for byte, and the root file is the
original document minus those tables. A comment belongs to the table whose
body it is in, except the run of comment lines directly above a header (no
blank line between), which belongs to that header.

GATE: a manifest with a comment in every position -- the file header,
      above and inline on ``[project]`` keys, above and inside a
      ``[module.X]`` (a dotted one too), on ``[[module.X.methods]]`` rows,
      above and inside an object table, above the ``[[enum]]`` rows that
      stay put, and one a blank line separates from the next header -- is
      migrated and split. Each comment names the table it belongs to; every
      one must survive exactly once, in the file that table went to, and
      `load` must give the same config before and after. The expectations
      are read off the comments, so a comment added to the fixture is
      checked without registering it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _config as C

#: Every comment says, after ``@``, the table it belongs to: ``root`` for
#: the manifest's own sections, a component name, or ``module.<id>``.
MANIFEST = """\
# @root file header: what this project is

# @root a second header paragraph

[project]
# @root above name
name = "p"  # @root inline on name
version = "0.1.0"
# @root above build, two lines,
# @root the second of them
build = "cmake"
schema = "9"  # @root inline on schema
# @root a trailing note in [project], a blank line before the next header

# @module.m above the module header
[module.m]  # @module.m inline on the module header
# @module.m inside the module
objects = ["mo"]

# @mo above the module's object
[mo]
arg_type = "float"  # @mo inline in the object
return_type = "float"

# @module.h above the handle module
[module.h]
kind = "handle"
backing = "h"
create_fn = "h_open"
close_fn = "h_close"
# @module.h above the first methods row
[[module.h.methods]]
name = "write"  # @module.h inline on a methods row
fn = "h_write"
returns = "int"

# @module.h above the second methods row
[[module.h.methods]]
name = "flush"
fn = "h_flush"
returns = "int"
# @module.h a methods row's last line

# @module.dsp.fx above a dotted module
[module."dsp.fx"]
objects = []

# @eng above an object table
[eng]
# @eng inside the object table
arg_type = "double"
return_type = "double"

[[eng.state]]
name = "gain"  # @eng inline on a state row
type = "double"
default = "1.0"

# @root above the enum rows, which stay in the root file
[[enum]]
name = "mode"  # @root inline on an enum row
values = ["slow", "fast"]

# @root above the second enum row
[[enum]]
name = "shape"
values = ["a", "b"]
"""

_TAG = re.compile(r"#\s*@(\S+)")


def _destination(owner: str, cmd: str) -> str:
    """The file a table goes to under *cmd*, as `load` reads the layout."""
    if owner == "root":
        return C.FILENAME
    if owner.startswith("module."):
        if cmd == "split-objects":
            return C.FILENAME
        return f"modules/{C.module_paths(owner[len('module.') :]).cname}.toml"
    return f"objects/{owner}.toml"


def _comments(text: str) -> "list[str]":
    """Every comment in *text*, stripped -- the whole-line ones and the
    inline ones (no value here holds a ``#``)."""
    return [
        line[line.index("#") :].strip()
        for line in text.splitlines()
        if "#" in line
    ]


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    root.mkdir()
    (root / C.FILENAME).write_text(MANIFEST, encoding="utf-8")
    return root


@pytest.mark.parametrize(
    "cmds",
    [
        ("migrate-to-fragments",),
        ("split-objects",),
        # The second run extends the `include` the first one wrote, in
        # place, and moves the modules out of a root file it already cut.
        ("split-objects", "migrate-to-fragments"),
    ],
    ids="+".join,
)
def test_every_comment_lands_once_where_its_table_went(cmds, tmp_path):
    root = _project(tmp_path)
    before = C.load(root)
    expected = _comments(MANIFEST)
    # The fixture arms the gate: every position named in the docstring is
    # present, and every comment names its owner.
    assert len(expected) == len(set(expected)) > 20
    assert all(_TAG.match(c) for c in expected), expected

    for cmd in cmds:
        r = run_cli(cmd, cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr

    files = {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
        for p in [root / C.FILENAME, *root.glob("*/*.toml")]
    }
    where: dict[str, list[str]] = {}
    for rel, text in files.items():
        for c in _comments(text):
            where.setdefault(c, []).append(rel)

    lost = [c for c in expected if c not in where]
    assert not lost, f"{cmd} deleted {len(lost)} comment(s): {lost}"
    twice = {c: f for c, f in where.items() if len(f) > 1}
    assert not twice, f"{cmd} wrote a comment twice: {twice}"
    misplaced = {
        c: (where[c][0], _destination(_TAG.match(c).group(1), cmd))
        for c in expected
        if where[c][0] != _destination(_TAG.match(c).group(1), cmd)
    }
    assert not misplaced, f"{cmd} filed a comment elsewhere: {misplaced}"
    assert set(where) == set(expected), set(where) - set(expected)

    assert C.load(root) == before, f"{cmd} changed the config"


def test_the_root_file_is_the_original_minus_the_moved_tables(tmp_path):
    """Nothing the root file keeps is rewritten: its lines are the
    original's, in order, with only the moved tables' lines gone and one
    ``include`` line (and the blank line after it) in."""
    root = _project(tmp_path)
    r = run_cli("migrate-to-fragments", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    kept = (root / C.FILENAME).read_text(encoding="utf-8").splitlines()
    inc = [line for line in kept if line.startswith("include = ")]
    assert inc == ['include = ["objects/*.toml", "modules/*.toml"]'], kept
    # After the file header, before every table.
    assert kept.index(inc[0]) < kept.index("[project]")
    assert kept.index(inc[0]) > kept.index("# @root a second header paragraph")
    it = iter(MANIFEST.splitlines())
    rest = [line for line in kept if line and line != inc[0]]
    assert all(line in it for line in rest), "root lines reordered/rewritten"
    # And every original line of a table that stays is still there.
    gone = [
        line
        for line in MANIFEST.splitlines()
        if "@root" in line and line not in kept
    ]
    assert not gone, gone
