"""gh-1666: `jm upgrade` respells the C EXPRESSIONS a manifest holds, not only
its bodies and types.

gh-1653 taught the `c_prefix` respell to reach the author C that lives in
the manifest -- `*_impl` bodies and a `type` -- but jm splices more manifest
strings into generated C verbatim. doppler's

    out_size = "kaiser_num_taps(1, atten_db, fpass / 2.0, fstop / 2.0) | 1"

survived the upgrade, the regenerated `filter_ext.c` called the bare
`kaiser_num_taps` (now `dp_kaiser_num_taps`) and failed to compile, and
`apply`'s existing-tree refusal did not name it either.

The fix declares the C-bearing keys once, `_csym.MANIFEST_C_KEYS` (bodies,
expressions, types), and both the respell and the refusal read it. The
unit half here is parametrized over that declaration, so a key added to it
later is covered with no edit here; the end-to-end half builds the project
by running jm and reads the `_ext.c` jm renders from the respelled manifest.

GATE: after `c_prefix` + `jm upgrade`, a module function's `out_size` and
      a property's `expr` that call / name a derived symbol are respelled in
      the manifest AND in the `_ext.c` `apply` renders from it; `apply`
      refuses the tree before the upgrade naming both fragments; and every
      `MANIFEST_C_KEYS` key is respelled and refused alike.
"""

from __future__ import annotations

import pytest

import _gh1653_fixture as FX
from _jmrun import run_cli
from just_makeit import _csym
from just_makeit import _keys
from just_makeit import _textio
from just_makeit._function import run as function_run
from just_makeit._new import run as new_run

P = FX.PREFIX

#: The two generated bindings whose C comes from the manifest's expressions.
EXT = ("native/src/filt/filt_ext.c", "native/src/lo/lo_ext.c")


def _ok(*args, cwd):
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout + r.stderr)
    return r


def _build(where):
    """A module `filt` whose `design()` sizes its output by calling the
    sibling function `ntaps()` -- doppler's `kaiser_num_taps` shape -- and a
    standalone `lo` with a property whose `expr` names `lo_state_t`."""
    root = where / "q"
    new_run("q", root, modules=["filt"], c_prefix=None, fragments=True)
    function_run(
        root,
        "ntaps",
        "filt",
        params=[("n", "int", False, "")],
        return_type="int",
    )
    function_run(
        root,
        "design",
        "filt",
        params=[("n", "int", False, "")],
        return_type="void",
        out_type="float",
        variable_output=True,
        out_size="ntaps(n) | 1",
    )
    _ok("object", "lo", "--state", "gain:float:1.0", cwd=root)
    _ok(
        "property",
        "lo",
        "size",
        "--type",
        "size_t",
        "--expr",
        "sizeof(lo_state_t)",
        cwd=root,
    )
    return root


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    root = _build(tmp_path_factory.mktemp("g1666"))
    before = {rel: (root / rel).read_text() for rel in EXT}
    FX.set_prefix(root)
    refused = run_cli("apply", cwd=root)
    upgraded = run_cli("upgrade", cwd=root)
    # The upgrade's C-file walk respells the existing `_ext.c` too, so the
    # manifest's stale expression is latent until jm renders from it again.
    # Deleting the bindings makes the manifest their only source.
    for rel in EXT:
        (root / rel).unlink()
    applied = run_cli("apply", cwd=root)
    return root, before, refused, upgraded, applied


def test_the_fixture_renders_the_expressions_it_declares(tree):
    """Armed: before the prefix, the bindings spell the bare names."""
    _root, before, *_ = tree
    assert "(ntaps(n) | 1)" in before[EXT[0]], before[EXT[0]]
    assert "sizeof(lo_state_t)" in before[EXT[1]], before[EXT[1]]


def test_apply_refuses_the_expressions_before_the_upgrade(tree):
    _root, _before, refused, *_ = tree
    assert refused.returncode == 1, refused.stdout
    err = refused.stderr
    assert "modules/filt.toml still spells the unprefixed `ntaps`" in err, err
    assert "objects/lo.toml still spells the unprefixed `lo_state_t`" in err


def test_the_upgrade_respells_the_manifests_expressions(tree):
    root, _before, _refused, upgraded, _applied = tree
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    mod = (root / "modules" / "filt.toml").read_text()
    assert f'out_size = "{P}_ntaps(n) | 1"' in mod, mod
    obj = (root / "objects" / "lo.toml").read_text()
    assert f'expr = "sizeof({P}_lo_state_t)"' in obj, obj


def test_apply_renders_the_prefixed_expressions(tree):
    root, _before, _refused, _upgraded, applied = tree
    assert applied.returncode == 0, applied.stdout + applied.stderr
    filt = (root / EXT[0]).read_text()
    assert f"(npy_intp)({P}_ntaps(n) | 1)" in filt, filt
    lo = (root / EXT[1]).read_text()
    assert f"sizeof({P}_lo_state_t)" in lo, lo
    assert "respelled" not in run_cli("upgrade", cwd=root).stdout


# -- the declaration, key by key --------------------------------------------

NAMES = {"lo_state_t": f"{P}_lo_state_t", "ntaps": f"{P}_ntaps"}


def _known_keys():
    """Every key `_keys` recognises on any manifest table."""
    out = set()
    for v in vars(_keys).values():
        if isinstance(v, frozenset) and all(isinstance(k, str) for k in v):
            out |= v
    return out


def test_every_declared_key_is_a_manifest_key():
    """A typo in the declaration would respell and refuse nothing."""
    assert set(_csym.MANIFEST_C_KEYS) <= _known_keys(), (
        set(_csym.MANIFEST_C_KEYS) - _known_keys()
    )


@pytest.mark.parametrize("key", _csym.MANIFEST_C_KEYS)
def test_each_c_key_is_respelled_and_refused_alike(key, tmp_path):
    """One declaration, both directions: the respell rewrites what the
    refusal names, for every key -- and an author-named key beside it is
    left alone by both."""
    line = f'{key} = "(ntaps(1), (lo_state_t *)0)"\n'
    named = 'create_fn = "ntaps"\n'
    text = f"[t]\n{named}{line}"
    got = _csym.respell_manifest(text, NAMES, {})
    want = f'{key} = "({P}_ntaps(1), ({P}_lo_state_t *)0)"\n'
    assert got == f"[t]\n{named}{want}", got
    _textio.write_text(tmp_path / "just-makeit.toml", text)
    found = _csym._unrenamed(tmp_path, NAMES, {}, cfg={})
    assert found == {"just-makeit.toml": ["lo_state_t", "ntaps"]}, found
