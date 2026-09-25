"""gh-1653: `jm upgrade` reaches the author C it was blind to.

gh-1591 phase 3 respelled a project's C FILES onto its `c_prefix`. Two
places also hold the author's C and were left behind, so an upgraded tree
did not build:

- the MANIFEST: an `*_impl` body and a `type` naming a sibling's derived
  type, which jm copies into the C verbatim (doppler: 1 fragment of bodies,
  4 sibling types);
- a `JM_DEFINE_STEPS (<stem>, ...)` call, whose FIRST argument is the stem
  itself -- no derived identifier, so no rename map carried it -- and the
  `<stem>_step_batch` the macro pastes, which no render declares.

The same map and matcher as phase 3, over the same strings for `jm upgrade`
and for `apply`'s refusal (one detector). The fixture and the compiled half
of this gate: tests/_gh1653_fixture.py, tests/test_gh1653_upgrade_builds.py.

GATE: after `c_prefix` + `jm upgrade`, every manifest `*_impl` / `type`
      value and every `JM_DEFINE_STEPS` stem is respelled -- and nothing
      else: an author-named key, a comment, an author macro, a file stem, a
      Python name; a second upgrade prints nothing; and `apply` refuses the
      tree before the upgrade, naming the manifest and the macro.
"""

from __future__ import annotations

import pytest

import _gh1653_fixture as FX
from _jmrun import run_cli
from just_makeit import _csym
from just_makeit import _keys

P = FX.PREFIX


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    root = FX.build(tmp_path_factory.mktemp("g1653"))
    FX.set_prefix(root)
    refused = run_cli("apply", cwd=root)
    first = run_cli("upgrade", cwd=root)
    second = run_cli("upgrade", cwd=root)
    applied = run_cli("apply", cwd=root)
    return root, refused, first, second, applied


def test_the_manifests_c_is_respelled(tree):
    root, *_ = tree
    frag = (root / "objects" / "mixer.toml").read_text()
    assert f'type = "{P}_lo_state_t *"' in frag, frag
    assert f"{P}_lo_state_t *lo = {P}_lo_create(2.0f);" in frag, frag
    assert f"{P}_lo_destroy(state->osc);" in frag, frag


def test_a_comment_inside_an_impl_body_is_left_as_written(tree):
    root, *_ = tree
    frag = (root / "objects" / "mixer.toml").read_text()
    assert "/* lo_create: quoted in a comment */" in frag, frag


def test_the_macro_stem_and_step_batch_are_respelled(tree):
    root, *_ = tree
    c = (root / "native" / "src" / "lo" / "lo_core.c").read_text()
    assert f"JM_DEFINE_STEPS ({P}_lo, {P}_lo_state_t," in c, c
    h = next((root / "native" / "inc").rglob("lo_core.h")).read_text()
    assert f"{P}_lo_step_batch({P}_lo_state_t *state" in h, h
    assert FX.AUTHOR_MACRO in h, h


def test_the_bare_stem_moves_nowhere_else(tree):
    """`lo` alone is also a local, a file stem, a directory, a CMake target
    and a Python module: only the macro argument is the symbol stem."""
    root, *_ = tree
    frag = (root / "objects" / "mixer.toml").read_text()
    assert "obj->osc = lo;" in frag, frag
    assert (root / "native" / "src" / "lo" / "lo_core.c").is_file()
    c = (root / "native" / "src" / "lo" / "lo_core.c").read_text()
    assert '#include "q/lo/lo_core.h"' in c, c
    init = (root / "src" / "q" / "__init__.py").read_text()
    assert "from .lo import Lo" in init, init
    cm = (root / "native" / "src" / "lo" / "CMakeLists.txt").read_text()
    assert "add_library(lo_core OBJECT" in cm, cm


def test_the_upgrade_says_what_it_changed_and_is_idempotent(tree):
    root, _refused, first, second, applied = tree
    assert first.returncode == 0, first.stdout + first.stderr
    assert "objects/mixer.toml" in first.stdout, first.stdout
    assert "native/src/lo/lo_core.c" in first.stdout, first.stdout
    assert f"lo_step_batch\t{P}_lo_step_batch" in first.stdout, first.stdout
    assert "respelled" not in second.stdout, second.stdout
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert run_cli("status", "--check", cwd=root).returncode == 0


def test_apply_refused_what_the_upgrade_then_fixed(tree):
    """One detector: `apply` names the manifest and the macro call, the
    strings `jm upgrade` goes on to respell."""
    _root, refused, *_ = tree
    assert refused.returncode == 1, refused.stdout
    assert "objects/mixer.toml" in refused.stderr, refused.stderr
    assert "JM_DEFINE_STEPS(lo, ...)" in refused.stderr, refused.stderr


def test_an_author_named_key_is_never_respelled():
    names = {"lo_create": f"{P}_lo_create", "lo_step": f"{P}_lo_step"}
    text = (
        'create_fn = "lo_create"\nfn = "lo_step"\nimpl_file = "a.c::lo_step"\n'
    )
    assert _csym.respell_manifest(text, names, {"lo": f"{P}_lo"}) == text


def test_every_manifest_body_key_is_one_the_respell_reads():
    """Registration-free over `_keys`: a new `*impl` body key (not its
    `_file` companion) is C the upgrade must reach, so it must be here."""
    bodies = {
        k
        for k in _keys.OBJECT_KEYS
        if (k == "impl" or k.endswith("_impl")) and not k.endswith("_file")
    }
    assert bodies <= set(_csym.MANIFEST_C_KEYS), bodies - set(
        _csym.MANIFEST_C_KEYS
    )
