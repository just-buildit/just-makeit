"""gh-1883: a default a field cannot hold exactly passes its own tests.

``jm new fd --object g --state a:float:0.1`` produced a project whose
generated C test FAILED on the first ``make test``, with no edits. The
create and reset assignments store ``(float)0.1``, and the test asserted
``fd_g_get_a(obj) == 0.1`` -- that float against the DOUBLE ``0.1``, false
for every value a float cannot represent. Two sites restated the literal: the
initial-value check and the after-reset check.

gh-1067's shape -- an assertion that cannot hold -- for a float rather than a
bool, and that gate could not see it: it seeds every field with the type's
zero, which every float holds exactly. The Python face compared a ``float``
within ``_approx`` already; measuring the class found its complex branch
compared exactly, so a ``float _Complex`` given ``0.1 + 0.2 * I`` failed
``test_getter_setter`` and ``test_reset`` too.

Now the C face asserts the value the field holds, ``(<ctype>)(<default>)``
-- the conversion the assignment applied, so the check stays exact -- and
the Python face compares every rounding kind within ``_approx``.

GATE: one scaffold carrying a field of every float and complex type in the
vocabulary, each declared with a decimal default no binary float holds
exactly, built and run through ``jm test`` -- both suites. The types are
derived from ``_CTYPE_META``, not listed, so a new one is covered with no
edit here, and not from the fix's own ``_ROUNDING_KINDS``, so narrowing that
narrows nothing this gate checks.
"""

from __future__ import annotations

import re
import shutil
import struct

import pytest

from _jmrun import run_cli
from just_makeit import _types as T

#: A default, per kind, that no binary float holds exactly -- so a test
#: restating it compares a rounded value against an unrounded one.
_INEXACT = {"float": "0.1", "complex": "0.1 + 0.2 * I"}

#: Every scalar type a field rounds a decimal default in, from the vocabulary.
ROUNDING_TYPES = sorted(
    ct
    for ct, meta in T._CTYPE_META.items()
    if not ct.endswith("[]") and meta.get("kind") in _INEXACT
)


def _no_toolchain() -> "str | None":
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    return None


def test_the_vocabulary_has_both_kinds():
    """Never vacuous: both kinds present, and the default really rounds."""
    assert "float" in ROUNDING_TYPES, ROUNDING_TYPES
    assert "float _Complex" in ROUNDING_TYPES, ROUNDING_TYPES
    assert len(ROUNDING_TYPES) >= 5, ROUNDING_TYPES
    single = struct.unpack("f", struct.pack("f", 0.1))[0]
    assert single != 0.1


@pytest.mark.skipif(bool(_no_toolchain()), reason=str(_no_toolchain()))
def test_an_inexact_default_passes_both_generated_suites(tmp_path):
    """The issue's own trigger, per rounding type, through ``jm test``."""
    states: "list[str]" = []
    for i, ct in enumerate(ROUNDING_TYPES):
        kind = T._CTYPE_META[ct]["kind"]
        states += ["--state", f"v{i}:{ct}:{_INEXACT[kind]}"]
    r = run_cli("new", "p", "--object", "g", *states, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    proj = tmp_path / "p"

    # Armed: each field's default is asserted after construction AND after
    # reset, beside its round-trip. Were either default check dropped, the
    # build below would pass having asserted nothing about the defaults.
    c_test = (proj / "native/tests/test_g_core.c").read_text("utf-8")
    for i in range(len(ROUNDING_TYPES)):
        assert c_test.count(f"get_v{i}(obj) ==") == 3, (i, c_test)

    r = run_cli("test", cwd=proj)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    # Both faces ran: CTest's summary, then pytest's (or unittest's).
    assert "100% tests passed" in out, out
    assert re.search(r"\b\d+ passed\b|^OK$", out, re.M), out
