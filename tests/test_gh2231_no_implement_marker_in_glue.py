"""gh-2231: no file jm owns carries an `<<IMPLEMENT>>` marker.

`<<IMPLEMENT: ...>>` is the deliberate marker that tells the author "write
this body" (`_init.py`), and `jm remove`, `--impl` and `_method` read it in
the author's core files to tell a stub from finished code. A copy in
generated glue points at nothing to implement. A property whose getter is a
C call had one in its binding, directly above the finished call:

    /* <<IMPLEMENT: return the computed or stored value>> */
    return PyLong_FromSize_t(ring_get_pending(self->handle));

in the object's `_ext.c`, a module object's `_ext_<obj>.c` fragment and a
view's fragment, while the stub the author really fills in sat in `_core.c`.

Registration-free over both axes that matter. The SHAPES are the broad
fixture tree (`tests/_csym_fixtures.py`, built by running jm), which spans a
call-backed property on a standalone object, a module object and a view. The
FILES are every file in it, and which of them is jm's is answered by jm's
own ownership table (`_createonly.classify`), not by a path list here: a
marker is allowed only in a file whose kind is AUTHOR -- jm renders its
starting content and the author fills it in. An unclassified path is the
author's (a module function's stub), the direction `classify` already takes.

GATE: no file the ownership table calls jm's content -- whole (jm,
      reconciled, derived) or in managed blocks (partial) -- contains
      `<<IMPLEMENT`.
"""

from __future__ import annotations

import pytest

import _csym_fixtures as FX
from just_makeit import _createonly as CO

MARKER = b"<<IMPLEMENT"


@pytest.fixture(scope="module")
def roots(tmp_path_factory):
    return FX.build(tmp_path_factory.mktemp("gh2231"))


def _jm_owned(roots):
    """``(row, rel, kind, bytes)`` for every file jm's table owns."""
    for row, root in roots.items():
        for rel, data in FX.tree(root).items():
            rule = CO.classify(rel, root)
            if rule is None or rule.kind == CO.AUTHOR:
                continue
            yield row, rel, rule.kind, data


def test_the_sweep_reaches_a_property_getter_in_every_binding_kind(roots):
    """Armed: the fixture puts a property getter in each kind of file a
    binding is written to, so a marker emitted by the getter has somewhere
    to land. Derived from the files, not from a list of shapes."""
    kinds = {
        kind
        for _, rel, kind, data in _jm_owned(roots)
        if rel.endswith(".c") and b"_getprop_" in data
    }
    assert {CO.RECONCILED, CO.PARTIAL} <= kinds, kinds


def test_no_jm_owned_file_carries_an_implement_marker(roots):
    bad = []
    for row, rel, kind, data in _jm_owned(roots):
        for n, line in enumerate(data.splitlines(), 1):
            if MARKER in line:
                bad.append(f"{row}/{rel}:{n} ({kind}): {line.strip()!r}")
    assert bad == [], (
        "a file jm owns carries an author's fill-in marker; the marker "
        "belongs only in the author's starting content (`_core.c`, "
        "`_core.h`, a stub):\n" + "\n".join(bad)
    )
