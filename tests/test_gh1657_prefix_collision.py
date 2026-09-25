"""gh-1657: a ``c_prefix`` whose derived name the author's C already declares
is refused, by `apply` and by `jm upgrade`, before either writes.

With ``c_prefix = "zz"``, component ``lo``'s method ``scale`` derives
``zz_lo_scale``. An author who already wrote a ``zz_lo_scale`` wrapper around
``lo_scale`` has a second, different function under that name, and the
upgrade's respell cannot tell them apart: it turns the wrapper's
``return lo_scale (state, x);`` into ``return zz_lo_scale (state, x);`` -- a
function calling itself. As a ``static inline`` in a header that is a
compile error; as an ordinary ``.c`` definition it compiles, silently, into
unbounded recursion. doppler's ``dp_syncword.h`` and ``dp_ber_test.h`` were
both.

Collision is DECLARED in a C file that is not one of the name's OWNING files
-- those jm's own render (the replay) declares it in. A tree an older jm
half-moved (its C respelled, its manifest not) declares the new names only
where jm does, and must still upgrade and build.

GATE: with an author wrapper declaring ``zz_lo_scale`` -- static inline in a
      header, or plain in a ``.c`` -- `apply` and `upgrade` exit 1, naming
      the file, the line, the name and ``lo``'s method ``scale``, and leave
      the tree byte-identical; a half-moved tree upgrades and builds.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _csym
from just_makeit._new import run as new_run

P = "zz"

MIXER_TOML = '''[mixer]
arg_type = "float _Complex"
return_type = "float _Complex"
mutable = "true"
depends_on = [{ name = "lo", link = true }]

create_impl = """
obj->osc = lo_create(2.0f);
if (!obj->osc) { free(obj); return NULL; }
"""
destroy_impl = """
lo_destroy(state->osc);
"""

[[mixer.state]]
name = "osc"
type = "lo_state_t *"
opaque = true
'''

#: The author's wrapper, declaring the name `zz` derives from `lo.scale`.
#: The header puts preprocessor lines above it, which `_file_scope` shortens,
#: so a line number counted in the wrong string would be off.
WRAPPERS = {
    "inline": (
        "native/inc/q/lo_wrap.h",
        "#ifndef Q_LO_WRAP_H\n"
        "#define Q_LO_WRAP_H\n"
        '#include "q/lo/lo_core.h"\n'
        "/* A wrapper the author wrote before any prefix existed. */\n"
        "static inline float\n"
        "zz_lo_scale (lo_state_t *state, float x)\n"
        "{\n"
        "  return lo_scale (state, x);\n"
        "}\n"
        "#endif\n",
        6,
    ),
    "plain": (
        "native/src/lo/lo_wrap.c",
        '#include "q/lo/lo_core.h"\n'
        "\n"
        "float\n"
        "zz_lo_scale (lo_state_t *state, float x)\n"
        "{\n"
        "  return lo_scale (state, x);\n"
        "}\n",
        4,
    ),
}


def _ok(*args, cwd):
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout + r.stderr)
    return r


def _bare(where: Path) -> Path:
    """A bare project, built by running jm: `lo` with a method `scale`, a
    `mixer` holding `lo` opaquely (its manifest spells `lo_state_t *`), and
    a module function -- one of each kind of owning file."""
    root = where / "q"
    new_run("q", root, c_prefix=None, fragments=True)
    _ok("object", "lo", "--state", "gain:float:2.0", cwd=root)
    _ok(
        "method",
        "lo",
        "scale",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        cwd=root,
    )
    (root / "objects" / "mixer.toml").write_text(MIXER_TOML, newline="\n")
    _ok("module", "m", cwd=root)
    _ok("function", "calc", "--module", "m", cwd=root)
    _ok("apply", cwd=root)
    return root


def _set_prefix(root: Path) -> None:
    toml = root / C.FILENAME
    text = toml.read_text(encoding="utf-8")
    toml.write_text(
        text.replace("[project]\n", f'[project]\nc_prefix = "{P}"\n', 1),
        newline="\n",
    )


def _snapshot(root: Path) -> "dict[str, bytes]":
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


@pytest.fixture(scope="module")
def bare(tmp_path_factory):
    return _bare(tmp_path_factory.mktemp("g1657"))


def _copy(bare: Path, where: Path) -> Path:
    import shutil

    root = where / "q"
    shutil.copytree(bare, root)
    return root


@pytest.mark.parametrize("verb", ["upgrade", "apply"])
@pytest.mark.parametrize("variant", sorted(WRAPPERS))
def test_a_colliding_prefix_is_refused_and_nothing_is_written(
    bare, tmp_path, variant, verb
):
    root = _copy(bare, tmp_path)
    rel, text, line = WRAPPERS[variant]
    (root / rel).write_text(text, newline="\n")
    _set_prefix(root)
    before = _snapshot(root)
    r = run_cli(verb, cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"{rel}:{line} already declares `{P}_lo_scale`" in r.stderr, (
        r.stderr
    )
    assert "component `lo`'s method `scale`" in r.stderr, r.stderr
    assert "choose another c_prefix" in r.stderr, r.stderr
    after = _snapshot(root)
    changed = sorted(
        k
        for k in before.keys() | after.keys()
        if before.get(k) != after.get(k)
    )
    assert changed == [], changed
    # The respell this refusal pre-empts: the wrapper calling itself.
    assert "return lo_scale (state, x);" in (root / rel).read_text()


def _half_moved(bare: Path, where: Path) -> Path:
    """The tree an older jm leaves: its C respelled onto the prefix, its
    manifest not. `jm upgrade` moves the C; the mixer's manifest `type` is
    then held at its bare spelling, whichever jm ran, so the tree is
    half-moved by construction."""
    root = _copy(bare, where)
    _set_prefix(root)
    _ok("upgrade", cwd=root)
    frag = root / "objects" / "mixer.toml"
    text = frag.read_text(encoding="utf-8")
    frag.write_text(
        text.replace(f'type = "{P}_lo_state_t *"', 'type = "lo_state_t *"'),
        newline="\n",
    )
    assert 'type = "lo_state_t *"' in frag.read_text()
    return root


def test_a_half_moved_tree_is_no_collision_and_builds(bare, tmp_path):
    """The new names are declared -- but only in their owning files:
    `lo_core.h` / `.c`, `mixer_core.h`, the module function's `.c`."""
    root = _half_moved(bare, tmp_path)
    # Not vacuous: the tree already declares the new names, in the files
    # jm owns them in -- which is exactly what a collision looks like
    # anywhere else.
    owning = {
        "native/inc/q/lo/lo_core.h": {f"{P}_lo_scale", f"{P}_lo_state_t"},
        "native/src/m/calc.c": {f"{P}_calc"},
    }
    for rel, want in owning.items():
        got = _csym.declared((root / rel).read_text(encoding="utf-8"))
        assert want <= got, (rel, sorted(got))
    r = run_cli("upgrade", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    b = root / "b"
    for cmd in (
        ["cmake", "-S", ".", "-B", str(b), "-DBUILD_PYTHON=OFF"],
        ["cmake", "--build", str(b)],
        ["ctest", "--test-dir", str(b), "--output-on-failure"],
    ):
        p = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        assert p.returncode == 0, (cmd, (p.stdout + p.stderr)[-3000:])


def test_the_declaring_line_is_counted_past_preprocessor_lines():
    text = "#ifndef G\n#define G\n#include <x.h>\nint zz_f (void);\n#endif\n"
    assert _csym._line_of(text, "zz_f") == 4
