"""gh-1656: `jm upgrade` respells a manifest's ``replace`` table.

``replace = { "<old>" = "<new>" }`` rewrites the ``impl`` body before jm
splices it into the step: every KEY is text matched against the body, every
VALUE is C that lands in it. gh-1653 taught the ``c_prefix`` respell the
manifest's C-bearing ``<key> = "<string>"`` values, but a ``replace`` table's
strings sit under quoted keys (or a ``[<comp>.replace]`` header) that
`_csym.MANIFEST_C_VALUE` does not match. So after ``c_prefix`` + `jm upgrade`:

- a VALUE naming a derived symbol kept the old spelling, and the next render
  from the manifest spliced it into C that no longer declares it;
- a KEY naming a derived symbol stopped matching a body lifted from an
  ``impl_file`` the upgrade DID respell, so the substitution was silently
  skipped;

and `apply`'s refusal named neither. The fix is one reading,
`_csym.manifest_c_spans` (the keyed values plus every ``replace`` key and
value, `_csym.replace_pairs`), which the respell and the refusal both take.
A key stays put when its body comes from a file the upgrade leaves alone
(`_csym.unfollowed_bodies`): it is still matched against the old spelling.

This extends tests/test_gh1653_upgrade_manifest_c.py, as the issue asks, in
a file of its own because it builds. The issue's own repro called a plain
property's getter from the step, which does not build even WITHOUT a prefix
(the inline step precedes the getter's prototype in the header) -- so these
fixtures name the derived TYPE instead, and a pre-prefix build proves the
fixture sound before anything is blamed on the upgrade.

GATE: after ``c_prefix`` + `jm upgrade`, a ``replace`` value (inline table)
      and a ``replace`` key over an in-project ``impl_file`` body
      (``[<comp>.replace]`` table) are respelled; `apply` refused both
      before; the sacred headers rendered again from the manifest BUILD and
      their C tests pass; and a key whose body file is not respelled keeps
      its spelling.
"""

from __future__ import annotations

import subprocess

import pytest

import _gh1653_fixture as FX
from _jmrun import run_cli
from just_makeit import _csym
from just_makeit import _textio
from just_makeit._new import run as new_run

P = FX.PREFIX

LO_TOML = """[lo]
arg_type = "float"
return_type = "float"
impl = "return ((const LO_T *)state)->gain * x;"
replace = { "LO_T" = "lo_state_t" }

[[lo.state]]
name = "gain"
type = "float"
default = "1.0"
"""

HI_TOML = """[hi]
arg_type = "float"
return_type = "float"
impl_file = "legacy/hi_body.c::hi_body"

[hi.replace]
"GAIN_OF(hi_state_t, state)" = "((const hi_state_t *)state)->gain"

[[hi.state]]
name = "gain"
type = "float"
default = "2.0"
"""

#: The body `hi` lifts: GAIN_OF exists nowhere, so the key MUST match it.
HI_BODY = """float
hi_body(const hi_state_t *state, float x)
{
    return GAIN_OF(hi_state_t, state) * x;
}
"""

SACRED = ("native/inc/q/lo/lo_core.h", "native/inc/q/hi/hi_core.h")


def _build(root, tag):
    """Configure, build and ctest *root*; ``(step, rc, tail)`` per step,
    stopping at the first failure. Recorded, never asserted here: a fixture
    that fails reports as a setup ERROR naming no test (gh-1430)."""
    b = root / f"b-{tag}"
    steps = []
    for cmd in (
        ["cmake", "-S", ".", "-B", str(b), "-DBUILD_PYTHON=OFF"],
        ["cmake", "--build", str(b)],
        ["ctest", "--test-dir", str(b), "--output-on-failure"],
    ):
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        steps.append((cmd[:2], r.returncode, (r.stdout + r.stderr)[-3000:]))
        if r.returncode:
            break
    return steps


def _ok(steps):
    for what, rc, out in steps:
        assert rc == 0, (what, out)


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    root = tmp_path_factory.mktemp("g1656") / "q"
    new_run("q", root, c_prefix=None, fragments=True)
    (root / "objects").mkdir(exist_ok=True)
    (root / "legacy").mkdir()
    _textio.write_text(root / "objects" / "lo.toml", LO_TOML)
    _textio.write_text(root / "objects" / "hi.toml", HI_TOML)
    _textio.write_text(root / "legacy" / "hi_body.c", HI_BODY)
    first = run_cli("apply", cwd=root)
    before = _build(root, "before")
    rendered = (root / SACRED[0]).read_text()
    FX.set_prefix(root)
    refused = run_cli("apply", cwd=root)
    upgraded = run_cli("upgrade", cwd=root)
    # The C-file walk respells the existing headers too, so a stale
    # manifest is latent until jm renders from it again; deleting the
    # sacred headers makes the manifest their only source.
    for rel in SACRED:
        (root / rel).unlink()
    applied = run_cli("apply", cwd=root)
    after = _build(root, "after") if applied.returncode == 0 else []
    return root, (first, rendered), before, refused, upgraded, applied, after


def test_the_fixture_builds_before_the_prefix(tree):
    """Armed: the substitutions land, and the tree builds, with no prefix
    -- so a failure after the upgrade is the upgrade's."""
    _root, (first, rendered), before, *_ = tree
    assert first.returncode == 0, first.stdout + first.stderr
    _ok(before)
    assert "((const lo_state_t *)state)->gain * x" in rendered, rendered


def test_apply_refuses_the_replace_tables_before_the_upgrade(tree):
    refused = tree[3]
    assert refused.returncode == 1, refused.stdout
    err = refused.stderr
    assert "objects/lo.toml still spells the unprefixed `lo_state_t`" in err
    assert "objects/hi.toml still spells the unprefixed `hi_state_t`" in err


def test_the_upgrade_respells_both_sides_of_the_replace_tables(tree):
    root, _first, _before, _refused, upgraded, *_ = tree
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    lo = (root / "objects" / "lo.toml").read_text()
    assert f'replace = {{ "LO_T" = "{P}_lo_state_t" }}' in lo, lo
    hi = (root / "objects" / "hi.toml").read_text()
    assert (
        f'"GAIN_OF({P}_hi_state_t, state)" = '
        f'"((const {P}_hi_state_t *)state)->gain"'
    ) in hi, hi


def test_the_tree_rendered_again_from_the_manifest_builds(tree):
    root, *_rest, applied, after = tree
    assert applied.returncode == 0, applied.stdout + applied.stderr
    _ok(after)
    assert (
        f"((const {P}_hi_state_t *)state)->gain * x"
        in (root / SACRED[1]).read_text()
    )


def test_a_key_over_a_body_the_upgrade_leaves_alone_keeps_its_spelling(
    tmp_path,
):
    """The body comes from a file outside the walk, so it still spells
    `lo_state_t`: the key must too, or it stops matching. The value is
    spliced into jm's C, which moved, so it moves."""
    names = {"lo_state_t": f"{P}_lo_state_t"}
    text = (
        '[lo]\nimpl_file = "../vendor/old.c::lo_k"\n'
        'replace = { "(lo_state_t *)" = "(const lo_state_t *)" }\n'
    )
    got = _csym.respell_manifest(text, names, {}, tmp_path, set())
    assert f'"(lo_state_t *)" = "(const {P}_lo_state_t *)"' in got, got
