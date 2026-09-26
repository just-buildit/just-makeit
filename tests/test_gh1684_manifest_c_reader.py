"""gh-1684: the manifest's C reader reaches every spelling of a C string.

`_csym.manifest_c_spans` is the ONE reading of the C a manifest holds; the
`c_prefix` respell and `apply`'s refusal both take it, so a shape it misses
is missed by both, silently. Four were left by gh-1656 / gh-1671:

1. **An escaped quote in a basic string.** `_toml_string` ended the value at
   the first quote, so ``impl = "... \\"ab\\" ... lo_state_t ..."`` was read
   as ``... \\`` only. Fixed in the one pattern (a basic string takes
   escapes; a literal one does not), and the C is read DECODED
   (`_csym.value_c`) -- read raw, ``\\"ab\\"`` looks like a C string literal
   swallowing the code after it -- and respelled back into the raw text
   only where it changed (`_csym.respell_value`), keeping the author's
   escapes.
2. **A dotted-key ``replace``**, ``replace."HI_T" = "hi_state_t"``, and the
   top-level ``lo.replace`` forms: `replace_pairs` read only the inline
   table and ``[<comp>.replace]``.
3. **A multi-line inline table** (TOML 1.1): `tomli`, jm's reader on
   Python < 3.11, accepts one, but `replace_pairs` read jm's single-line
   layout only. Tested at the reader: `tomllib` (3.11+) cannot load the
   file, so an end-to-end version would run on some legs only.
4. **The `_Complex` respell froze no ``replace`` key.** The `c_prefix`
   respell keeps a key whose ``impl_file`` body it leaves alone
   (`unfollowed_bodies`); the complex respell passed no such set, so it
   respelled a key its own walk never matched against. It now passes the
   set from its own walk.

GATE: after ``c_prefix`` + `jm upgrade`, an ``impl`` holding an escaped
      quote before a derived name, and a dotted-key ``replace``, are
      respelled (escapes kept), `apply` refused both before, and the sacred
      headers rendered again from the manifest BUILD and pass ctest; a
      multi-line inline ``replace`` and a top-level dotted one are read; and
      the complex respell keeps a ``replace`` key over a body outside its
      walk while moving one over a body inside it.
"""

from __future__ import annotations

import subprocess

import pytest

import _gh1653_fixture as FX
from _jmrun import run_cli
from just_makeit import _csym
from just_makeit import _textio
from just_makeit import _upgrade
from just_makeit._new import run as new_run

P = FX.PREFIX

#: `sizeof("ab") - 3` is 0, so the step is unchanged -- but the escaped
#: quotes sit BEFORE the derived type, which is what the old reader lost.
LO_TOML = r"""[lo]
arg_type = "float"
return_type = "float"
impl = "return (float)(sizeof(\"ab\") - 3) + ((const lo_state_t *)state)->gain * x;"

[[lo.state]]
name = "gain"
type = "float"
default = "1.0"
"""

HI_TOML = """[hi]
arg_type = "float"
return_type = "float"
impl = "return ((const HI_T *)state)->gain * x;"
replace."HI_T" = "hi_state_t"

[[hi.state]]
name = "gain"
type = "float"
default = "2.0"
"""

SACRED = ("native/inc/q/lo/lo_core.h", "native/inc/q/hi/hi_core.h")


def _build(root, tag):
    """Configure, build and ctest *root*; ``(step, rc, tail)`` per step.
    Recorded, never asserted in the fixture (gh-1430)."""
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
    root = tmp_path_factory.mktemp("g1684") / "q"
    new_run("q", root, c_prefix=None, fragments=True)
    (root / "objects").mkdir(exist_ok=True)
    _textio.write_text(root / "objects" / "lo.toml", LO_TOML)
    _textio.write_text(root / "objects" / "hi.toml", HI_TOML)
    first = run_cli("apply", cwd=root)
    before = _build(root, "before")
    rendered = (root / SACRED[1]).read_text()
    FX.set_prefix(root)
    refused = run_cli("apply", cwd=root)
    upgraded = run_cli("upgrade", cwd=root)
    # The C-file walk respells the existing headers too; deleting them
    # makes the manifest their only source.
    for rel in SACRED:
        (root / rel).unlink()
    applied = run_cli("apply", cwd=root)
    after = _build(root, "after") if applied.returncode == 0 else []
    return root, (first, rendered), before, refused, upgraded, applied, after


def test_the_fixture_builds_before_the_prefix(tree):
    """Armed: the dotted `replace` lands and the tree builds unprefixed, so
    a failure after the upgrade is the upgrade's."""
    _root, (first, rendered), before, *_ = tree
    assert first.returncode == 0, first.stdout + first.stderr
    _ok(before)
    assert "((const hi_state_t *)state)->gain * x" in rendered, rendered


def test_apply_refuses_both_shapes_before_the_upgrade(tree):
    refused = tree[3]
    assert refused.returncode == 1, refused.stdout
    err = refused.stderr
    assert "objects/lo.toml still spells the unprefixed `lo_state_t`" in err
    assert "objects/hi.toml still spells the unprefixed `hi_state_t`" in err


def test_the_upgrade_respells_both_and_keeps_the_escapes(tree):
    root, _first, _before, _refused, upgraded, *_ = tree
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    lo = (root / "objects" / "lo.toml").read_text()
    assert (
        r'impl = "return (float)(sizeof(\"ab\") - 3) + '
        f'((const {P}_lo_state_t *)state)->gain * x;"'
    ) in lo, lo
    hi = (root / "objects" / "hi.toml").read_text()
    assert f'replace."HI_T" = "{P}_hi_state_t"' in hi, hi


def test_the_tree_rendered_again_from_the_manifest_builds(tree):
    root, *_rest, applied, after = tree
    assert applied.returncode == 0, applied.stdout + applied.stderr
    _ok(after)
    assert (
        f"((const {P}_hi_state_t *)state)->gain * x"
        in (root / SACRED[1]).read_text()
    )


# -- 3. the reader, over the shapes no 3.11+ parser loads ---------------------


@pytest.mark.parametrize(
    "text",
    [
        '[lo]\nreplace = {\n  "LO_T" = "lo_state_t", # why\n'
        '  N = "lo_n",\n}\n',
        'lo.replace = { "LO_T" = "lo_state_t", N = "lo_n" }\n',
        'lo.replace."LO_T" = "lo_state_t"\nlo.replace.N = "lo_n"\n',
    ],
    ids=["multi-line-inline", "top-level-inline", "top-level-dotted"],
)
def test_every_spelling_of_a_replace_table_is_read(text):
    assert [
        (t, text[slice(*k)], text[slice(*v)])
        for t, k, v in sorted(_csym.replace_pairs(text), key=lambda r: r[1])
    ] == [("lo", "LO_T", "lo_state_t"), ("lo", "N", "lo_n")]


def test_a_literal_string_takes_no_escape():
    """`'C:\\'` ends at its quote: only a BASIC string takes escapes."""
    text = "a = 'C:\\'\nimpl = \"lo_create(1);\"\n"
    assert [text[a:b] for a, b in _csym.manifest_c_spans(text)] == [
        "lo_create(1);"
    ]


# -- 4. the complex respell keeps what its walk did not move -----------------


def test_the_complex_respell_freezes_a_key_over_an_unwalked_body(tmp_path):
    """`legacy/` is outside the complex walk, so that body keeps `float
    complex` and its key must too; `native/src/` is inside it, so that
    key moves with its body. Each value is C spliced into jm's render, so
    both move."""
    (tmp_path / "legacy").mkdir()
    (tmp_path / "native" / "src").mkdir(parents=True)
    body = "float\nk(float complex x)\n{\n    return 0;\n}\n"
    _textio.write_text(tmp_path / "legacy" / "old.c", body)
    _textio.write_text(tmp_path / "native" / "src" / "new.c", body)
    _textio.write_text(
        tmp_path / "just-makeit.toml",
        '[project]\nname = "q"\n\n'
        '[lo]\nimpl_file = "legacy/old.c::k"\n'
        'replace = { "(float complex)" = "(float complex)" }\n\n'
        '[hi]\nimpl_file = "native/src/new.c::k"\n'
        'replace = { "(float complex)" = "(float complex)" }\n',
    )
    _upgrade._repair_complex_spelling(tmp_path)
    got = (tmp_path / "just-makeit.toml").read_text()
    lo, hi = got.split("[hi]")
    assert '"(float complex)" = "(float _Complex)"' in lo, got
    assert '"(float _Complex)" = "(float _Complex)"' in hi, got


def test_a_top_level_dotted_impl_file_freezes_its_replace_key(tmp_path):
    """`lo.impl_file` at the top level names table `lo`, as `lo.replace`
    does -- so the key over that unrespelled body keeps its spelling."""
    names = {"lo_state_t": f"{P}_lo_state_t"}
    text = (
        'lo.impl_file = "../vendor/old.c::lo_k"\n'
        'lo.replace = { "(lo_state_t *)" = "(const lo_state_t *)" }\n'
    )
    got = _csym.respell_manifest(text, names, {}, tmp_path, set())
    assert f'"(lo_state_t *)" = "(const {P}_lo_state_t *)"' in got, got
