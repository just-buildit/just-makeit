"""gh-1583 part 3: `jm upgrade` moves a schema-7 project to the prefixed layout.

Schema 8 keeps a project's headers under ``native/inc/<pkg>/`` and spells
every include of one ``"<pkg>/..."``. `jm upgrade` (7 -> 8) moves the whole
``native/inc`` one level down and respells each reference that RESOLVES to a
moved file: C/C++ ``#include`` lines, manifest strings, and ``native/inc/...``
paths in CMake files.

Two oracles, because they catch different things:

- **upgrade + apply == a fresh scaffold**, byte for byte, over the shapes jm
  scaffolds. The fresh schema-8 tree is built by a path that never runs the
  migration, so it cannot share its mistakes. `apply` regenerates what jm
  owns, so this oracle is blind to hand-written content by design --
- **hand-written content**, which only the migration can get right: a
  header the author wrote, a sacred file including it, a manifest
  ``header =``, a CMake ``-I``, a vendored library's own ``"config.h"`` (left
  alone), a quoted include that finds a DIFFERENT file beside itself (left
  alone), and one directly in the include root that finds the same file both
  ways (respelled to the canonical form).

GATE: `jm upgrade` from schema 7 leaves a tree identical to a fresh schema-8
      scaffold after `apply`, and moves every header under native/inc/<pkg>/
      respelling exactly the references that resolve to a moved file.
"""

from __future__ import annotations

import filecmp
from pathlib import Path
from unittest import mock

import pytest

from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _incpath as INC

SHAPES = {
    "object": [("object", "osc", "--state", "gain:double:1.0")],
    "module+function": [
        ("module", "filt"),
        (
            "object",
            "fir",
            "--module",
            "filt",
            "--arg-type",
            "float _Complex",
            "--return-type",
            "float _Complex",
        ),
        (
            "function",
            "energy",
            "--module",
            "filt",
            "--param",
            "x:float[]",
            "--return-type",
            "double",
        ),
    ],
    "named-after-the-package": [("object", "proj", "--state", "g:double:2.0")],
    "perf": [
        ("object", "lo", "--state", "f:double:1.0"),
        ("perf",),
    ],
}


def _scaffold(base: Path, steps, schema: int) -> Path:
    base.mkdir(parents=True)
    with mock.patch.object(C, "CURRENT_SCHEMA", schema):
        r = run_cli("new", "proj", cwd=base)
        assert r.returncode == 0, r.stdout + r.stderr
        for argv in steps:
            r = run_cli(*argv, cwd=base / "proj")
            assert r.returncode == 0, (argv, r.stdout + r.stderr)
    return base / "proj"


def _files(root: Path) -> "set[str]":
    return {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_an_upgraded_project_is_a_fresh_one(tmp_path, shape):
    old = _scaffold(tmp_path / "old", SHAPES[shape], 7)
    assert not INC.prefixed(old)
    r = run_cli("upgrade", cwd=old)
    assert r.returncode == 0, r.stdout + r.stderr
    assert INC.prefixed(old)
    r = run_cli("apply", cwd=old)
    assert r.returncode == 0, r.stdout + r.stderr
    new = _scaffold(tmp_path / "new", SHAPES[shape], C.CURRENT_SCHEMA)

    a, b = _files(old), _files(new)
    assert a == b, (sorted(a - b), sorted(b - a))
    differ = sorted(f for f in a if not filecmp.cmp(old / f, new / f, False))
    assert differ == []
    assert run_cli("status", "--check", cwd=old).returncode == 0


#: What `jm upgrade` printed, per fixture project.
_OUTPUT: "dict[Path, str]" = {}


@pytest.fixture
def hand(tmp_path) -> Path:
    """A schema-7 project carrying hand-written headers and references."""
    root = _scaffold(tmp_path / "h", SHAPES["object"], 7)
    inc = root / "native" / "inc"  # the legacy layout, spelled on purpose
    (inc / "vendor").mkdir()
    (inc / "vendor" / "v.h").write_text(
        '#include "util.h"\n#include "clib_common.h"\n', encoding="utf-8"
    )
    # beside v.h: a DIFFERENT util.h from the one at the include root
    (inc / "vendor" / "util.h").write_text("/* vendor util */\n", "utf-8")
    (inc / "util.h").write_text("/* project util */\n", encoding="utf-8")
    # directly in the include root: "vendor/v.h" finds the same file both ways
    (inc / "helpers.h").write_text('#include "vendor/v.h"\n', "utf-8")
    core_c = root / "native" / "src" / "osc" / "osc_core.c"
    core_c.write_text(
        '#include "vendor/v.h"\n#include <stdio.h>\n'
        + core_c.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    third = root / "native" / "src" / "third"
    third.mkdir()
    (third / "config.h").write_text("/* vendored */\n", encoding="utf-8")
    (third / "lib.c").write_text('#include "config.h"\n', encoding="utf-8")
    cmake = root / "native" / "src" / "third" / "CMakeLists.txt"
    cmake.write_text(
        "target_include_directories(third PRIVATE "
        "${CMAKE_SOURCE_DIR}/native/inc/vendor)\n",
        encoding="utf-8",
    )
    manifest = root / "just-makeit.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[extras]\nheader = "vendor/v.h"\nother = "not/a/header.h"\n',
        encoding="utf-8",
    )
    # a header CMake configures at build time: its template moves, so the
    # include of what it produces must follow (doppler's version.h shape).
    (inc / "gen").mkdir()
    (inc / "gen" / "ver.h.in").write_text(
        "#define V @PROJECT_VERSION@\n", "utf-8"
    )
    (third / "ver.c").write_text('#include "gen/ver.h"\n', encoding="utf-8")
    # ANOTHER project nested inside this one (doppler's examples/downstream-jm
    # shape): its "clib_common.h" is its own, not this project's.
    down = root / "examples" / "down"
    (down / "native" / "inc").mkdir(parents=True)
    (down / "native" / "inc" / "clib_common.h").write_text("/**/\n", "utf-8")
    (down / "just-makeit.toml").write_text(
        '[project]\nname = "down"\nschema = "7"\n', encoding="utf-8"
    )
    (down / "use.c").write_text('#include "clib_common.h"\n', "utf-8")
    r = run_cli("upgrade", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    _OUTPUT[root] = r.stdout
    return root


def test_every_header_moved_under_the_package(hand):
    hdr = INC.header_root(hand)
    assert hdr == hand / "native" / "inc" / "proj"
    assert (hdr / "vendor" / "v.h").is_file()
    assert (hdr / "osc" / "osc_core.h").is_file()
    assert sorted(p.name for p in (hand / "native" / "inc").iterdir()) == [
        "proj"
    ]


def test_a_sacred_include_of_a_hand_header_is_respelled(hand):
    core_c = (hand / "native/src/osc/osc_core.c").read_text(encoding="utf-8")
    assert '#include "proj/vendor/v.h"' in core_c
    assert "#include <stdio.h>" in core_c


def test_the_manifest_header_is_respelled_and_nothing_else(hand):
    text = (hand / "just-makeit.toml").read_text(encoding="utf-8")
    assert 'header = "proj/vendor/v.h"' in text
    assert 'other = "not/a/header.h"' in text


def test_a_cmake_path_into_the_include_root_follows_the_move(hand):
    text = (hand / "native/src/third/CMakeLists.txt").read_text("utf-8")
    assert "${CMAKE_SOURCE_DIR}/native/inc/proj/vendor)" in text


def test_a_vendored_librarys_own_include_is_untouched(hand):
    assert (hand / "native/src/third/lib.c").read_text("utf-8") == (
        '#include "config.h"\n'
    )


def test_an_include_that_finds_a_different_file_beside_it_is_kept(hand):
    v = (INC.header_root(hand) / "vendor" / "v.h").read_text("utf-8")
    assert '#include "util.h"\n' in v  # its own neighbour, not the root's
    assert '#include "proj/clib_common.h"' in v


def test_a_nested_project_is_named_and_left_to_its_own_upgrade(hand):
    down = hand / "examples" / "down"
    assert (down / "use.c").read_text("utf-8") == '#include "clib_common.h"\n'
    assert (down / "native" / "inc" / "clib_common.h").is_file()
    assert "skip    examples/down/" in _OUTPUT[hand]


def test_an_include_of_a_configured_header_follows_its_template(hand):
    assert (hand / "native/src/third/ver.c").read_text("utf-8") == (
        '#include "proj/gen/ver.h"\n'
    )


def test_a_root_header_takes_the_canonical_spelling(hand):
    h = (INC.header_root(hand) / "helpers.h").read_text("utf-8")
    assert h == '#include "proj/vendor/v.h"\n'


def test_a_tree_already_under_its_package_is_not_moved_again(tmp_path):
    """A prefixed tree whose manifest says an older schema -- a hand edit,
    or bench_upgrade's old premise -- keeps its layout; moving it would nest
    every header twice."""
    root = _scaffold(tmp_path / "p", SHAPES["object"], C.CURRENT_SCHEMA)
    before = _files(root)
    cfg = C.load(root)
    C.set_schema_version(cfg, 7)
    C.save(root, cfg)
    r = run_cli("upgrade", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "headers already under the package" in r.stdout
    assert _files(root) == before
    assert not (INC.header_root(root) / "proj").exists()
