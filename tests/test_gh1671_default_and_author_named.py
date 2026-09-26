"""gh-1671: a manifest `default` that is C moves with the prefix; an
author-named key naming a renamed symbol is refused.

1. **`default`.** A state field's `default` is C in the create/reset body
   (`state->size = sizeof(lo_state_t);`), and a scalar parameter's is its C
   local's initializer, so either can name a derived symbol. It was left out
   of `_csym.MANIFEST_C_KEYS` because an ENUM entry's `default` is a choice
   string -- a bare word (`"hann"`) the respell would read as an identifier.
   Read from the render sites: every one splices `default` into C verbatim
   unless the entry names an enum (an `enum` key, or a `type` spelled
   `enum:` / `string_enum:`). `_csym.c_default_spans` is that rule, read by
   the one `manifest_c_spans` the respell and the refusal share. An
   init-param's `default_raw` -- C by definition -- joins `C_EXPR_KEYS`.

2. **Author-named keys** (the user's decision on the issue): jm still never
   respells what the author named (`test_an_author_named_key_is_never_
   respelled` in the gh-1653 file stays green), but a `create_fn =
   "lo_create"` on a handle over component `lo` names a symbol the prefix
   renames. `apply` and `jm upgrade` now REFUSE it before writing, naming
   the file, the key and the spelling to use. The keys are ONE declaration,
   `_csym.AUTHOR_NAMED_KEYS`, held here to every `fn` / `*_fn` key `_keys`
   knows.

GATE: after ``c_prefix`` + `jm upgrade`, a state `default` naming a derived
      type is respelled and the tree rendered again from the manifest
      builds, while an enum's choice default is untouched; `apply` refused
      the stale default first; and a handle whose `create_fn` / `close_fn`
      name a renamed symbol is refused by `apply` and by `jm upgrade`
      (which writes nothing), and builds once the author spells them anew.
"""

from __future__ import annotations

import subprocess

import pytest

import _gh1653_fixture as FX
from _jmrun import run_cli
from just_makeit import _csym
from just_makeit import _keys
from just_makeit import _textio
from just_makeit._new import run as new_run

P = FX.PREFIX

LO_TOML = """[lo]
arg_type = "float"
return_type = "float"

[[lo.state]]
name = "gain"
type = "float"
default = "1.0"

[[lo.state]]
name = "size"
type = "size_t"
default = "sizeof(lo_state_t)"
"""

#: The handle's backing component: `create(gain)`, matching its create_args.
LO_BARE_TOML = LO_TOML.split('\n\n[[lo.state]]\nname = "size"')[0] + "\n"

HANDLE_TOML = """[module.h]
kind = "handle"
backing = "lo"
header = "q/lo/lo_core.h"
type_name = "Lo"
handle_type = "lo_state_t"
create_fn = "lo_create"
close_fn = "lo_destroy"
create_args = [{ name = "gain", type = "float", default = "1.0" }]
"""


def _cmake(root, tag):
    """Configure, build and ctest; ``(step, rc, tail)`` per step. Recorded,
    never asserted in a fixture (gh-1430)."""
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


def _project(where, lo=LO_TOML):
    root = where / "q"
    new_run("q", root, c_prefix=None, fragments=True)
    (root / "objects").mkdir(exist_ok=True)
    _textio.write_text(root / "objects" / "lo.toml", lo)
    return root


# -- 1. a C `default` -------------------------------------------------------


@pytest.fixture(scope="module")
def defaults(tmp_path_factory):
    root = _project(tmp_path_factory.mktemp("g1671d"))
    first = run_cli("apply", cwd=root)
    before = _cmake(root, "before")
    FX.set_prefix(root)
    refused = run_cli("apply", cwd=root)
    upgraded = run_cli("upgrade", cwd=root)
    # The C-file walk respells the existing core too, so a stale manifest is
    # latent until jm renders from it again.
    (root / "native" / "src" / "lo" / "lo_core.c").unlink()
    applied = run_cli("apply", cwd=root)
    after = _cmake(root, "after") if applied.returncode == 0 else []
    return root, first, before, refused, upgraded, applied, after


def test_the_default_fixture_builds_before_the_prefix(defaults):
    _root, first, before, *_ = defaults
    assert first.returncode == 0, first.stdout + first.stderr
    _ok(before)


def test_apply_refuses_a_stale_c_default(defaults):
    refused = defaults[3]
    assert refused.returncode == 1, refused.stdout
    assert (
        "objects/lo.toml still spells the unprefixed `lo_state_t`"
        in refused.stderr
    ), refused.stderr


def test_the_upgrade_respells_a_c_default(defaults):
    root, _f, _b, _r, upgraded, *_ = defaults
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    frag = (root / "objects" / "lo.toml").read_text()
    assert f'default = "sizeof({P}_lo_state_t)"' in frag, frag


def test_the_core_rendered_again_from_the_default_builds(defaults):
    root, *_rest, applied, after = defaults
    assert applied.returncode == 0, applied.stdout + applied.stderr
    _ok(after)
    core = (root / "native" / "src" / "lo" / "lo_core.c").read_text()
    assert f"state->size = sizeof({P}_lo_state_t);" in core, core


NAMES = {"hann": f"{P}_hann", "lo_state_t": f"{P}_lo_state_t"}


@pytest.mark.parametrize(
    "text",
    [
        'params = [{ name = "w", enum = "win", default = "hann" }]\n',
        '[[f.params]]\nname = "w"\ntype = "enum:win"\ndefault = "hann"\n',
        '[[f.params]]\nname = "w"\ntype = "string_enum:hann,box"\n'
        'default = "hann"\n',
    ],
    ids=["enum-key", "enum-ref-type", "string-enum-type"],
)
def test_an_enum_choice_default_is_never_respelled(text):
    """A module function `hann` beside an enum choice `hann`: the choice is
    a string, and the respell must not turn it into `zz_hann`."""
    assert _csym.respell_manifest(text, NAMES, {}) == text


@pytest.mark.parametrize(
    "text",
    [
        '[[lo.state]]\nname = "n"\ntype = "int"\ndefault = "hann(3)"\n',
        'params = [{ name = "k", type = "int", default = "hann(3)" }]\n',
        # A sibling param's enum is not this one's (the entry is the
        # inline table, not the list it sits in).
        'params = [{ name = "w", enum = "win", default = "box" },'
        ' { name = "k", type = "int", default = "hann(3)" }]\n',
        # A table-section entry: an inline table nested in its section is
        # another entry, and its `enum` is not this default's.
        '[[f.functions]]\nname = "g"\ndefault = "hann(3)"\n'
        'params = [{ name = "w", enum = "win", default = "box" }]\n',
        '[[lo.init_params]]\nname = "k"\ntype = "int"\n'
        'default_raw = "hann(3)"\n',
    ],
    ids=[
        "state",
        "inline-param",
        "beside-an-enum",
        "section-beside-a-nested-enum",
        "default_raw",
    ],
)
def test_a_c_default_is_respelled(text):
    assert "zz_hann(3)" in _csym.respell_manifest(text, NAMES, {})


# -- 2. an author-named key naming a renamed symbol -------------------------


@pytest.fixture(scope="module")
def handle(tmp_path_factory):
    root = _project(tmp_path_factory.mktemp("g1671h"), LO_BARE_TOML)
    (root / "modules").mkdir(exist_ok=True)
    _textio.write_text(root / "modules" / "h.toml", HANDLE_TOML)
    first = run_cli("apply", cwd=root)
    FX.set_prefix(root)
    core = (root / "native" / "src" / "lo" / "lo_core.c").read_text()
    refused_apply = run_cli("apply", cwd=root)
    refused_upgrade = run_cli("upgrade", cwd=root)
    core_after = (root / "native" / "src" / "lo" / "lo_core.c").read_text()
    # The author does what the refusal says: spells the names anew.
    frag = root / "modules" / "h.toml"
    _textio.write_text(
        frag,
        frag.read_text()
        .replace('"lo_create"', f'"{P}_lo_create"')
        .replace('"lo_destroy"', f'"{P}_lo_destroy"'),
    )
    upgraded = run_cli("upgrade", cwd=root)
    applied = run_cli("apply", cwd=root)
    tested = run_cli("test", cwd=root) if applied.returncode == 0 else None
    return (
        root,
        first,
        refused_apply,
        refused_upgrade,
        core == core_after,
        upgraded,
        applied,
        tested,
    )


def _refusal(stderr, key, old):
    return (
        f"modules/h.toml:{'7' if key == 'create_fn' else '8'}: `{key} = "
        f'"{old}"` names `{old}`, which [project] c_prefix = {P!r} renames'
        f" to `{P}_{old}`"
    ) in stderr


def test_apply_refuses_an_author_named_key_naming_a_renamed_symbol(handle):
    _root, first, refused, *_ = handle
    assert first.returncode == 0, first.stdout + first.stderr
    assert refused.returncode == 1, refused.stdout
    assert _refusal(refused.stderr, "create_fn", "lo_create"), refused.stderr
    assert _refusal(refused.stderr, "close_fn", "lo_destroy"), refused.stderr


def test_the_upgrade_refuses_it_before_writing(handle):
    _root, _f, _ra, refused, untouched, *_ = handle
    assert refused.returncode == 1, refused.stdout
    assert _refusal(refused.stderr, "create_fn", "lo_create"), refused.stderr
    assert untouched, "the refused upgrade respelled lo_core.c"


def test_spelled_anew_the_handle_upgrades_builds_and_tests(handle):
    root, *_rest, upgraded, applied, tested = handle
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert tested is not None and tested.returncode == 0, (
        tested.stdout[-3000:] + tested.stderr[-3000:]
    )
    frag = (root / "modules" / "h.toml").read_text()
    assert f'handle_type = "{P}_lo_state_t"' in frag, frag


def test_every_fn_key_jm_knows_is_author_named():
    """Registration-free over `_keys`: a new `fn` / `*_fn` key names a
    function the author wrote, so it must be in the one declaration."""
    known = set()
    for value in vars(_keys).values():
        if isinstance(value, (frozenset, set, tuple)):
            for k in value:
                k = k[0] if isinstance(k, tuple) and k else k
                if isinstance(k, str):
                    known.add(k)
    fns = {k for k in known if k == "fn" or k.endswith("_fn")}
    assert fns, "the scan found no fn keys: it is not reading _keys"
    missing = fns - set(_csym.AUTHOR_NAMED_KEYS)
    assert not missing, missing
