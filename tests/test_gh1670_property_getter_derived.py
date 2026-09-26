"""gh-1670: a property's getter is a DERIVED name, declared by jm or not.

A ``field = true`` property has no getter jm declares -- the binding reads
``self->handle-><field>`` -- yet its docstring is looked up at
``<stem>_get_<prop>`` (`_docstring.property_doc`), so an author documents it
by declaring that getter in the sacred header. doppler's `FIR.num_taps` is
this shape.

`_csym.renames` read only the names the replay's headers declare, and the
replay declares no field getter. So after ``c_prefix`` + `jm upgrade` the
author's ``fir_get_num_taps`` kept its bare spelling while the doc lookup
(correctly) asked for ``zz_fir_get_num_taps``: the stub fell back to
"Num taps.", and `apply`'s unprefixed-name refusal never named it.

The fix: every property's getter is in `_csym.renames`
(`_csym.property_getters`), spelled by the one `_csym.property_getter` the
doc lookup reads, so the respell, the refusal and the doc lookup agree; and
`_csym.collisions` owns an undeclared getter where its component's own
files are, so the sacred header that declares it is not refused.

GATE: after ``c_prefix`` + `jm upgrade` + `jm apply`, a field property's
      author getter is respelled, its prose survives in the stub and the
      runtime binding, the rename table names it, and `apply` refused the
      tree before the upgrade naming it.
"""

from __future__ import annotations

import pytest

import _gh1653_fixture as FX
from _jmrun import run_cli
from just_makeit import _csym
from just_makeit import _textio
from just_makeit._new import run as new_run

P = FX.PREFIX
HEADER = "native/inc/pp/fir/fir_core.h"
STUB = "src/pp/filter/filter.pyi"
BINDING = "native/src/filter/filter_ext_fir.c"
PROSE = "Number of tap coefficients supplied at creation."


def _ok(*args, cwd):
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout + r.stderr)
    return r


def _num_taps_doc(stub: str) -> str:
    """The docstring line under ``def num_taps`` in *stub*."""
    lines = stub.splitlines()
    at = next(i for i, x in enumerate(lines) if "def num_taps(" in x)
    return lines[at + 1].strip()


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    """The issue's trigger, built by running jm; the getter is the one line
    an author writes by hand."""
    root = tmp_path_factory.mktemp("g1670") / "pp"
    new_run("pp", root, modules=["filter"], c_prefix=None)
    _ok(
        "object",
        "fir",
        "--module",
        "filter",
        "--no-state",
        "--no-step",
        "--class-name",
        "FIR",
        cwd=root,
    )
    _ok(
        "property",
        "fir",
        "num_taps",
        "--module",
        "filter",
        "--type",
        "size_t",
        "--field",
        cwd=root,
    )
    h = root / HEADER
    text = h.read_text()
    at = text.rindex("#ifdef __cplusplus\n}")
    getter = (
        f"/** @brief {PROSE} */\n"
        "size_t fir_get_num_taps(const fir_state_t *state);\n"
    )
    _textio.write_text(h, text[:at] + getter + text[at:])
    _ok("apply", cwd=root)
    before = (root / STUB).read_text()
    FX.set_prefix(root)
    refused = run_cli("apply", cwd=root)
    upgraded = run_cli("upgrade", cwd=root)
    applied = run_cli("apply", cwd=root)
    return root, before, refused, upgraded, applied


def test_the_fixture_documents_the_property_from_its_getter(tree):
    """Armed: before the prefix, the author's getter IS the doc source."""
    _root, before, *_ = tree
    assert _num_taps_doc(before) == f'"""{PROSE}"""', before


def test_apply_refuses_the_bare_getter_before_the_upgrade(tree):
    _root, _before, refused, *_ = tree
    assert refused.returncode == 1, refused.stdout
    assert HEADER in refused.stderr, refused.stderr
    assert "fir_get_num_taps" in refused.stderr, refused.stderr


def test_the_upgrade_respells_the_getter_and_names_it(tree):
    root, _before, _refused, upgraded, _applied = tree
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    assert f"fir_get_num_taps\t{P}_fir_get_num_taps\n" in upgraded.stdout
    header = (root / HEADER).read_text()
    assert f"size_t {P}_fir_get_num_taps(const {P}_fir_state_t" in header


def test_the_prose_survives_on_both_faces(tree):
    root, _before, _refused, _upgraded, applied = tree
    assert applied.returncode == 0, applied.stdout + applied.stderr
    stub = (root / STUB).read_text()
    assert _num_taps_doc(stub) == f'"""{PROSE}"""', stub
    assert PROSE in (root / BINDING).read_text()


@pytest.mark.parametrize(
    "prop",
    [
        {"name": "n", "field": True},
        {"name": "n", "expr": "state->n"},
        {"name": "n", "type": "size_t"},
    ],
    ids=["field", "expr", "plain"],
)
def test_every_property_kind_derives_its_getter(prop):
    """The class, not the instance: whatever backs a property, its doc is
    read at the getter, so the getter is derived."""
    cfg = {
        "project": {"name": "p", "c_prefix": P},
        "fir": {"properties": [prop]},
    }
    assert _csym.property_getters(cfg) == {f"{P}_fir_get_n": f"{P}_fir"}
