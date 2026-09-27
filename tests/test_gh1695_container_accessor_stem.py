"""gh-1695: a container property's accessors take the ONE symbol stem.

A container property (gh-543: ``type = "list" | "dict" | "tuple"``) calls
accessors jm derives from the component: ``<stem>_num_<prop>``,
``<stem>_<prop>_key`` / ``_value``, and for a codec property (gh-554) the
``<stem>_<prop>_entry`` cursor and its ``<stem>_<prop>_t`` struct.

The count accessor was derived from the RAW component name
(``f"{component}_num_{pname}"``, in the now-removed
`_context/_methods.container_fn_names`) while its siblings took the stem, so
under ``[project] c_prefix``:

1. a fresh project rendered ``rc_num_stages`` beside ``zz_rc_stages_value``,
   and `jm upgrade` onto a prefix had no row for it -- the name stayed bare
   and `apply` / `status --check` were clean;
2. once the author spelled the implementation ``zz_rc_num_stages``, the
   render (still bare) found nothing defining ``rc_num_stages`` and `apply`
   scaffolded a ``return 0`` placeholder beside the real one -- a count of
   zero, which reads as a valid empty list.

The fix is one derivation, `_csym.container_accessors`, read by the header
and stub writer (`_property`), the binding (`_context/_methods`), the codec
decode (`_codec`) and the rename table (`_csym.property_accessors`, which
also covers the accessors no render declares: a codec's cursor and struct,
and a view's properties). With the render and the rename table agreeing,
`apply`'s existing unprefixed-name refusal (gh-1591) is what stops the
scaffold beside an old spelling: it refuses before anything is written.

Built by running jm, never hand-written (gh-1181); the one hand edit is the
old spelling an earlier jm's upgrade left in the author's C.

GATE: a prefixed project's container accessors are spelled from the stem
      in the header, the stub and the binding; `jm upgrade` onto a prefix
      respells them and names them; an author file still spelling the old
      name is refused by `apply`, which scaffolds nothing beside it; and the
      rename table carries the accessors no render declares.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import _gh1653_fixture as FX
from _jmrun import run_cli
from just_makeit import _csym
from just_makeit import _textio
from just_makeit._docsync import _code_mask
from just_makeit._new import run as new_run

P = FX.PREFIX
HEADER = "native/inc/pk/rc/rc_core.h"
CORE_C = "native/src/rc/rc_core.c"
BINDING = "native/src/conv/conv_ext_rc.c"
#: The derived accessors the two properties below call.
ACCESSORS = (
    "num_stages",
    "stages_value",
    "num_bank",
    "bank_key",
    "bank_value",
)


def _ok(*args, cwd):
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout + r.stderr)
    return r


def _build(root: Path, c_prefix):
    """doppler's RateConverter shape: a module object with a ``list`` of
    strings and a ``dict`` of sizes, both on derived accessors. Returns the
    root and the closing `apply`'s result, which the caller asserts: a
    render that disagrees with itself is refused THERE, and a fixture that
    asserted it would report the bug as a setup error, not a failure."""
    new_run("pk", root, modules=["conv"], c_prefix=c_prefix)
    _ok("object", "rc", "--module", "conv", cwd=root)
    _ok(
        "property",
        "rc",
        "stages",
        "--module",
        "conv",
        "--type",
        "list",
        "--value-type",
        "const char *",
        cwd=root,
    )
    _ok(
        "property",
        "rc",
        "bank",
        "--module",
        "conv",
        "--type",
        "dict",
        "--value-type",
        "size_t",
        cwd=root,
    )
    return root, run_cli("apply", cwd=root)


def _spelled(root: Path, name: str) -> "list[str]":
    """The generated / author C files under *root* spelling *name* in code
    (comments and strings masked) as a whole identifier."""
    word = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")
    return sorted(
        p.relative_to(root).as_posix()
        for p in (root / "native").rglob("*")
        if p.suffix in (".c", ".h")
        and word.search(_code_mask(p.read_text(encoding="utf-8")))
    )


def _placeholders(root: Path) -> int:
    return (root / CORE_C).read_text().count("/* placeholder */")


@pytest.fixture(scope="module")
def fresh(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("g1695a") / "pk", P)


@pytest.mark.parametrize("acc", ACCESSORS)
def test_a_prefixed_project_spells_every_accessor_from_the_stem(fresh, acc):
    """(1): the header, the stub and the binding all read the stem -- and
    so `apply` of the project jm just scaffolded is not refused."""
    root, built = fresh
    assert built.returncode == 0, built.stdout + built.stderr
    assert _spelled(root, f"rc_{acc}") == [], acc
    got = _spelled(root, f"{P}_rc_{acc}")
    assert HEADER in got and CORE_C in got and BINDING in got, (acc, got)


@pytest.fixture(scope="module")
def upgraded(tmp_path_factory):
    """A bare project moved onto a prefix, as the issue's doppler was."""
    root, built = _build(tmp_path_factory.mktemp("g1695b") / "pk", None)
    assert built.returncode == 0, built.stdout + built.stderr
    stubs = _placeholders(root)
    FX.set_prefix(root)
    refused = run_cli("apply", cwd=root)
    up = run_cli("upgrade", cwd=root)
    applied = run_cli("apply", cwd=root)
    check = run_cli("status", "--check", cwd=root)
    return root, stubs, refused, up, applied, check


def test_apply_refuses_the_bare_accessors_before_the_upgrade(upgraded):
    _root, _stubs, refused, *_ = upgraded
    assert refused.returncode == 1, refused.stdout
    for acc in ACCESSORS:
        assert f"`rc_{acc}`" in refused.stderr, (acc, refused.stderr)


@pytest.mark.parametrize("acc", ACCESSORS)
def test_the_upgrade_names_and_respells_every_accessor(upgraded, acc):
    root, _stubs, _refused, up, *_ = upgraded
    assert up.returncode == 0, up.stdout + up.stderr
    assert f"rc_{acc}\t{P}_rc_{acc}\n" in up.stdout, up.stdout
    assert _spelled(root, f"rc_{acc}") == [], acc
    assert HEADER in _spelled(root, f"{P}_rc_{acc}"), acc


def test_the_upgraded_tree_applies_clean_and_scaffolds_nothing(upgraded):
    root, stubs, _refused, _up, applied, check = upgraded
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert "scaffolded" not in applied.stdout, applied.stdout
    assert _placeholders(root) == stubs
    assert check.returncode == 0, check.stdout + check.stderr


def test_an_old_spelling_is_refused_not_scaffolded_beside(tmp_path):
    """(2): the tree an earlier jm's upgrade left -- the count accessor
    still bare in the author's header and ``_core.c`` -- is refused by
    `apply`, which writes no ``return 0`` placeholder next to it."""
    root, built = _build(tmp_path / "pk", None)
    assert built.returncode == 0, built.stdout + built.stderr
    FX.set_prefix(root)
    _ok("upgrade", cwd=root)
    _ok("apply", cwd=root)
    for rel in (HEADER, CORE_C):
        p = root / rel
        _textio.write_text(
            p, p.read_text().replace(f"{P}_rc_num_stages", "rc_num_stages")
        )
    before = (root / CORE_C).read_text()
    r = run_cli("apply", cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "`rc_num_stages`" in r.stderr, r.stderr
    assert "scaffolded" not in r.stdout, r.stdout
    assert (root / CORE_C).read_text() == before


def _cfg(props: list, views: "list | None" = None) -> dict:
    comp: dict = {"properties": props}
    if views:
        comp["views"] = views
    return {"project": {"name": "pk", "c_prefix": P}, "rc": comp}


def test_the_rename_table_carries_accessors_no_render_declares(tmp_path):
    """A codec property's cursor and struct are the author's to declare,
    under the names jm derives, and a view's container property derives
    them too -- none is in a replay's headers, so `renames` reads them from
    the manifest (`_csym.property_accessors`)."""
    codec = {"name": "frames", "type": "dict", "codec": "kw"}
    view = {
        "class_name": "Peek",
        "properties": [{"name": "t", "type": "list"}],
    }
    names = _csym.renames(tmp_path, _cfg([codec], [view]))
    for old in (
        "rc_num_frames",
        "rc_frames_key",
        "rc_frames_entry",
        "rc_frames_t",
        "rc_num_t",
        "rc_t_value",
    ):
        assert names.get(old) == f"{P}_{old}", (old, names)
    # A codec property has no value accessor; a list has no key.
    assert "rc_frames_value" not in names and "rc_t_key" not in names


def test_a_named_accessor_is_the_authors_and_never_renamed(tmp_path):
    """A declared name is used as written (gh-1671), even one spelled like
    a derived name -- so no bare twin of it is ever respelled onto it."""
    prop = {"name": "stages", "type": "list", "count_fn": f"{P}_rc_len"}
    names = _csym.renames(tmp_path, _cfg([prop]))
    assert "rc_len" not in names and "rc_num_stages" not in names, names
    assert names["rc_stages_value"] == f"{P}_rc_stages_value"
