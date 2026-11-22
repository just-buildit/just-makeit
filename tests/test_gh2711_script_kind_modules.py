"""gh-2711: `jm script` replays a ``kind`` module whole.

A handle, capsule or composer module is declared in the manifest alone: no
CLI verb spells ``kind``, ``backing`` or any of the sub-tables the kind
reads (``source``, ``segment``, ``methods``, ``create_args``, ...). `jm
script` emitted a plain ``jm module <name>`` for it, carrying the few keys
that verb has flags for, so the replayed project held an ordinary empty
module where the composer had been -- with no NOTE to say so.

The script now re-declares such a module's whole table through the
manifest's own writer (``C._dump_generic``), the shape it already uses for
``[[enum]]``, and closes with the ``jm apply`` that writes its glue.

GATE: for every module kind jm generates (`_keys.MODULE_KEYS_BY_KIND`, not a
      hand list), a project declaring one, beside the component it composes,
      round-trips through `jm script`: the replayed manifest's module table
      equals the original's, and the replay materialises the module's
      binding.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli, script_round_trip
from just_makeit import _config as C
from just_makeit import _incpath as INC
from just_makeit import _keys
from just_makeit import _textio

#: One declaration per kind, each spelling sub-tables no `jm module` flag
#: can: the keys `jm script` dropped. Keyed by kind so the completeness
#: test below can hold this to jm's own list of kinds.
TABLES = {
    "handle": """
[module.lamp]
kind = "handle"
backing = "lamp"
type_name = "Lamp"
handle_type = "lamp_t"
header = "kp/lamp/lamp.h"
create_fn = "lamp_open"
close_fn = "lamp_close"
capsule = "lamp"
doc = "A lamp."

[[module.lamp.create_args]]
name = "level"
type = "int"

[[module.lamp.methods]]
name = "bump"
fn = "lamp_bump"
""",
    "capsule": """
[module.gadget]
kind = "capsule"
backing = "gadget"
doc = "A gadget."

[[module.gadget.init_params]]
name = "rate"
type = "double"

[[module.gadget.properties]]
name = "rate"
type = "double"
writable = true
""",
    "composer": """
[module.win]
kind = "composer"
backing = "win"
composes = ["wfm"]
extra_link_libs = ["backing_core"]

[module.win.source]
object = "wfm"
struct = "wsrc_t"
type_name = "Src"
fields = [{ name = "bw", type = "double", default = "1e6" }]

[module.win.segment]
type_name = "Seg"
struct = "wseg_t"
sources = "multi"
fields = [{ name = "fs", type = "double", default = "1e6" }]
""",
}

#: The kinds jm generates, read from the vocabulary `_keys` validates them
#: by -- a new kind lands there, and then fails the test below until it
#: has a table here.
KINDS = sorted(_keys.MODULE_KEYS_BY_KIND)


def _ok(root: Path, *argv: str) -> None:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (argv, (r.stdout + r.stderr)[-3000:])


def test_every_kind_has_a_table():
    assert sorted(TABLES) == KINDS


def _project(where: Path, kind: str) -> "tuple[Path, str]":
    """A project built by running jm: component `wfm`, then *kind*'s
    module table appended by hand, as the manifest is the only place a
    kind module can be declared. Returns the root and the module's id."""
    root = where / "kp"
    _ok(where, "new", "kp")
    _ok(root, "object", "wfm", "--arg-type", "float")
    toml = root / C.FILENAME
    _textio.write_text(toml, toml.read_text(encoding="utf-8") + TABLES[kind])
    cfg = C.load(root)
    (mod,) = [m for m in C.modules(cfg) if C.module_kind(cfg, m) == kind]
    return root, mod


def _round_trip(root: Path, mod: str, where: Path) -> None:
    """Script *root*, replay it under *where*, and require the replay to
    be the same project, *mod*'s binding included."""
    r = run_cli("script", cwd=root)
    assert r.returncode == 0, r.stderr
    assert f"just-makeit module {mod}" not in r.stdout

    before, after = script_round_trip(root, where)
    # The module first, for a failure that names what was dropped.
    assert after["module"][mod] == before["module"][mod]
    assert after == before
    replayed = where / root.name
    assert (INC.src_path(replayed, mod) / f"{mod}_ext.c").is_file()


@pytest.mark.parametrize("kind", KINDS)
def test_kind_module_round_trips(kind, tmp_path):
    root, mod = _project(tmp_path, kind)
    _round_trip(root, mod, tmp_path / "replay")


def test_a_handle_consumer_replays_after_its_table(tmp_path):
    """A component's `object` init-param naming a handle module is refused
    until that module's table exists, so the table is replayed where the
    module stood -- ahead of every `jm object` line."""
    root, mod = _project(tmp_path, "handle")
    _ok(root, "object", "user", "--init-param", f"src:object:{mod}")
    _round_trip(root, mod, tmp_path / "replay")
