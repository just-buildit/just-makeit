"""The documented exception must be the one the binding actually raises.

Comparing the two doc faces to *each other* — which doppler's
`check_doc_face_parity.py` does, and `test_face_parity_gate.py` does here —
cannot catch the case where both faces agree and both are wrong. gh-864
produced that twice:

- ``__exit__`` said "releasing the X" on **both** faces over a body that
  finalizes and leaves the object alive;
- ``close`` and ``__exit__`` carry **no** ``Raises`` on either face over a
  body that raises ``ValueError`` (gh-869), so a parity gate reports parity.

The only reference that cannot be wrong about what a binding raises is the
**binding**. So this gate reads ``PyExc_<Class>`` out of the generated C and
requires the ``.pyi`` to name the same class.

Scope is deliberate. Not every ``PyExc_`` is a *declared* error: ``steps``
emits ``TypeError``/``ValueError`` for argument validation, and every wrapper
opens with the ``RuntimeError "destroyed"`` guard. Those are jm's own
plumbing, not the author's contract. The lifecycle surface — ``destroy`` and
its aliases, ``__exit__``, and the finalizer named by ``exit`` — is where
"the declared exception" is unambiguous, and it is exactly the surface gh-805
§H broke in four consecutive releases.

Both trees are checked, because "standalone works" is what gh-860 taught.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._method import run as method_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run

_DECLARED = "ValueError"

# Members that raise the author's declared exception. `close` is the
# finalizer named by `exit`; `destroy` is the teardown that inherits it;
# `__exit__` calls the finalizer and inherits its raise semantics.
_LIFECYCLE = ("close", "destroy", "__exit__")

# gh-869: these two emit the declared raise and document no `Raises` at all,
# so both faces agree there is no exception. Ratchet — may only shrink, and
# `test_the_ratchet_only_holds_real_gaps` fails if one stops being missing.
_KNOWN_UNDOCUMENTED = {"close", "__exit__"}

# Every wrapper opens with this; it is jm's plumbing, not a declared error.
_GUARD = re.compile(r'PyExc_RuntimeError,\s*"destroyed"')
_RAISE = re.compile(r"PyErr_(?:SetString|Format)\s*\(\s*PyExc_(\w+)")


def _raised_classes(ext_c: str) -> dict[str, set[str]]:
    """``{c_function_name: {declared exception classes}}`` from generated C.

    The ``"destroyed"`` guard is stripped first: it fires on a torn-down
    object regardless of what the author declared, so counting it would make
    every member look like it raises ``RuntimeError``.
    """
    out: dict[str, set[str]] = {}
    current = ""
    for line in ext_c.splitlines():
        m = re.match(r"^([A-Za-z_]\w*)\s*\(", line)
        if m:
            current = m.group(1)
        if _GUARD.search(line):
            continue
        hit = _RAISE.search(line)
        if hit and current:
            out.setdefault(current, set()).add(hit.group(1))
    return out


def _documented_raises(pyi: str) -> dict[str, set[str]]:
    """``{member: {classes named in its numpy Raises section}}``."""
    out: dict[str, set[str]] = {}
    tree = ast.parse(pyi)
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        for fn in cls.body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            doc = ast.get_docstring(fn) or ""
            if "Raises" not in doc:
                out[fn.name] = set()
                continue
            tail = doc.split("Raises", 1)[1].splitlines()
            names = {
                ln.strip()
                for ln in tail
                if ln.strip()
                and not ln.startswith((" ", "\t"))
                and ln.strip().endswith("Error")
            }
            out[fn.name] = names
    return out


def _build(root: Path, module: str | None) -> tuple[str, str]:
    """Scaffold one object; return ``(ext_c_text, pyi_text)``."""
    new_run("p", root, [], [])
    if module:
        module_run(root, module)
    object_run(root, "w", module, arg_type="float", return_type="float")
    method_run(
        root,
        "w",
        "close",
        module,
        "void",
        "int",
        False,
        [],
        status_return=True,
        error=_DECLARED,
        error_message="the capture has a hole",
    )
    cfg = C.load(root)
    C.set_destroy_spec(cfg, "w", {"returns": "int", "exit": "close"})
    C.save(root, cfg)
    apply_run(root)
    if module:
        ext = root / "native" / "src" / module / f"{module}_ext_w.c"
        pyi = root / "src" / "p" / module / f"{module}.pyi"
    else:
        ext = root / "native" / "src" / "w" / "w_ext.c"
        pyi = root / "src" / "p" / "w.pyi"
    return ext.read_text(encoding="utf-8"), pyi.read_text(encoding="utf-8")


@pytest.fixture(scope="module", params=["standalone", "module"])
def tree(request, tmp_path_factory):
    base = tmp_path_factory.mktemp(f"bodydoc_{request.param}")
    ext, pyi = _build(base, None if request.param == "standalone" else "m")
    return _raised_classes(ext), _documented_raises(pyi), request.param


class TestTheGateIsArmed:
    """It must be able to see both sides before its silence means anything."""

    def test_the_binding_actually_raises_the_declared_class(self, tree):
        raised, _, _ = tree
        emitting = {fn for fn, cs in raised.items() if _DECLARED in cs}
        assert emitting, (
            "no generated function raises the declared exception — the "
            "fixture stopped exercising the feature, so every assertion "
            "below would pass vacuously"
        )

    def test_the_stub_parsed(self, tree):
        _, documented, _ = tree
        assert documented, "no members parsed out of the .pyi"

    def test_the_guard_is_excluded(self, tree):
        # If the "destroyed" guard leaked in, every member would look like it
        # raises RuntimeError and the comparison below would be meaningless.
        raised, _, _ = tree
        assert not any("RuntimeError" in cs for cs in raised.values())


class TestDocumentedMatchesRaised:
    """For the lifecycle surface, the `.pyi` names what the C raises."""

    @pytest.mark.parametrize("member", _LIFECYCLE)
    def test_documented_class_is_the_raised_class(self, tree, member):
        raised, documented, _ = tree
        if member in _KNOWN_UNDOCUMENTED:
            pytest.skip(f"gh-869: {member} documents no Raises yet")
        c_fn = next(
            fn for fn in raised if fn.endswith(f"_{member.strip('_')}")
        )
        assert _DECLARED in raised[c_fn]
        assert documented[member] == {_DECLARED}, (
            f"{member}: the binding raises {sorted(raised[c_fn])} but the "
            f"stub documents {sorted(documented[member]) or 'nothing'}. "
            "Comparing the two doc faces cannot catch this — only the body "
            "can."
        )

    @pytest.mark.parametrize("member", sorted(_KNOWN_UNDOCUMENTED))
    def test_the_ratchet_only_holds_real_gaps(self, tree, member):
        # When gh-869 lands, these stop being empty and must leave the set,
        # or the ratchet rusts into a permanent allowlist.
        _, documented, _ = tree
        assert not documented.get(member), (
            f"{member} now documents Raises — remove it from "
            "_KNOWN_UNDOCUMENTED so the gate covers it."
        )
