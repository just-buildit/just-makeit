"""gh-2465: a manifest reorder the author's definition does not share.

GATE: `jm apply` refuses a declaration rendered into a parameter order its sacred definition does not have, for every declaration kind, and writes nothing.
GATE: `jm status --check` reports it as ORDER and fails, including on a tree an older jm already applied.
GATE: the example in a header's file comment passing the parameter names in another order is advisory, never counted.

Swap two same-typed ``[[...]]`` blocks in the manifest and `apply` used to
rewrite the prototype in the sacred ``_core.h`` (with gh-632's advisory
warning) while the definition kept the old order. Both parameters being
``double``, C compiled it silently, `status --check` exited 0, and every
caller following the header got the two values swapped.

The class is every declaration jm renders from the manifest against a
definition the author owns, so the cases below are one per place a
parameter order comes from: an object's ``state``, its ``init_params``, a
method's ``params``, a module function's ``params`` (its definition in its
own ``.c``), an ``inline`` one (defined in the header; the binding calls the
new order), and a module object's constructor. `_ctorsig.order_drift` names
none of them: it reads every prototype in the replay's headers against every
definition in the author's C.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from _jmrun import run_cli  # noqa: E402

#: (id, scaffold commands, manifest file, table, function, defining file)
#: Every case declares two `double` entries named `a` then `b`.
_KINDS = [
    (
        "state",
        [
            [
                "object",
                "amp",
                "--state",
                "a:double:1.0",
                "--state",
                "b:double:2.0",
            ]
        ],
        "objects/amp.toml",
        "amp.state",
        "p_amp_create",
        "native/src/amp/amp_core.c",
    ),
    (
        "init_params",
        [
            [
                "object",
                "amp",
                "--init-param",
                "a:double:1.0",
                "--init-param",
                "b:double:2.0",
            ]
        ],
        "objects/amp.toml",
        "amp.init_params",
        "p_amp_create",
        "native/src/amp/amp_core.c",
    ),
    (
        "method",
        [
            ["object", "amp", "--state", "g:double:1.0"],
            [
                "method",
                "amp",
                "mix",
                "--param",
                "a:double",
                "--param",
                "b:double",
                "--return-type",
                "double",
            ],
        ],
        "objects/amp.toml",
        "amp.methods.params",
        "p_amp_mix",
        "native/src/amp/amp_core.c",
    ),
    (
        "function",
        [
            ["module", "mm"],
            [
                "function",
                "fn",
                "--module",
                "mm",
                "--param",
                "a:double",
                "--param",
                "b:double",
                "--return-type",
                "double",
            ],
        ],
        "modules/mm.toml",
        "module.mm.functions.params",
        "p_fn",
        "native/src/mm/fn.c",
    ),
    (
        "inline_function",
        [
            ["module", "mm"],
            [
                "function",
                "fi",
                "--module",
                "mm",
                "--inline",
                "--param",
                "a:double",
                "--param",
                "b:double",
                "--return-type",
                "double",
            ],
        ],
        "modules/mm.toml",
        "module.mm.functions.params",
        "p_fi",
        "native/inc/p/mm/mm_core.h",
    ),
    (
        "module_object",
        [
            ["module", "mm"],
            [
                "object",
                "mo",
                "--module",
                "mm",
                "--state",
                "a:double:1.0",
                "--state",
                "b:double:2.0",
            ],
        ],
        "objects/mo.toml",
        "mo.state",
        "p_mo_create",
        "native/src/mo/mo_core.c",
    ),
]
_IDS = [k[0] for k in _KINDS]


def _project(base: Path, cmds) -> Path:
    r = run_cli("new", "p", cwd=base)
    assert r.returncode == 0, r.stderr
    root = base / "p"
    for cmd in cmds:
        r = run_cli(*cmd, cwd=root)
        assert r.returncode == 0, r.stderr
    return root


def _swap(path: Path, table: str) -> None:
    """Swap the `a` and `b` entries of *table* in the manifest *path*."""
    text = path.read_text(encoding="utf-8")
    # One block per table header, up to the next header or the end.
    blocks = re.split(r"(?m)^(?=\[)", text)
    at = [
        i
        for i, b in enumerate(blocks)
        if b.startswith(f"[[{table}]]\n")
        and re.search(r'^name = "[ab]"$', b, re.M)
    ]
    assert len(at) == 2 and at[1] == at[0] + 1, (table, text)
    i, j = at
    first, second = blocks[i].rstrip("\n"), blocks[j].rstrip("\n")
    tail = "\n" if j == len(blocks) - 1 else "\n\n"
    blocks[i], blocks[j] = second + "\n\n", first + tail
    path.write_text("".join(blocks), encoding="utf-8")


def _definition(root: Path, rel: str, fn: str) -> str:
    """The parameter list *rel* defines *fn* with, whitespace collapsed."""
    text = (root / rel).read_text(encoding="utf-8")
    m = re.search(rf"\b{fn}\s*\(([^;{{}}]*)\)\s*\{{", text)
    assert m, f"{rel} defines no {fn}"
    return " ".join(m.group(1).split())


def _snapshot(root: Path) -> dict:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.parts
    }


def _status_json(root: Path) -> dict:
    r = run_cli("status", "--json", cwd=root)
    return json.loads(r.stdout)


@pytest.mark.parametrize("kind", _KINDS, ids=_IDS)
def test_apply_refuses_a_reorder_the_definition_lacks(tmp_path, kind):
    _id, cmds, manifest, table, fn, defining = kind
    root = _project(tmp_path, cmds)
    assert "double a, double b" in _definition(root, defining, fn)
    _swap(root / manifest, table)
    before = _snapshot(root)
    r = run_cli("apply", cwd=root)
    assert r.returncode != 0, r.stdout + r.stderr
    # Names the definition, both orders, and the way out.
    assert f"{defining}:" in r.stderr, r.stderr
    assert f"defines {fn}(" in r.stderr and "double a, double b" in r.stderr
    assert "double b, double a" in r.stderr, r.stderr
    assert "gh-2465" in r.stderr, r.stderr
    # A refused apply writes nothing: the header keeps the old prototype.
    assert _snapshot(root) == before


@pytest.mark.parametrize("kind", _KINDS, ids=_IDS)
def test_status_reports_order_on_a_tree_an_older_jm_applied(tmp_path, kind):
    """The issue's exact state: header rewritten, definition left behind."""
    from just_makeit import _apply

    _id, cmds, manifest, table, fn, defining = kind
    root = _project(tmp_path, cmds)
    _swap(root / manifest, table)
    # What 0.118.0's apply did: rewrite every declaration, refuse nothing.
    _apply.run(root, refuse_order=False)
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "ORDER (" in r.stdout, r.stdout
    assert f"{defining}:" in r.stdout, r.stdout
    (d,) = _status_json(root)["param_order_drift"]
    assert (d["function"], d["path"]) == (fn, defining), d
    # A method's leading state pointer comes along; the order is the end.
    assert d["definition"].endswith("double a, double b"), d
    assert d["manifest"].endswith("double b, double a"), d


@pytest.mark.parametrize("kind", _KINDS, ids=_IDS)
def test_reordering_the_definition_resolves_it(tmp_path, kind):
    """The refusal's first way out works, and nothing is left reported."""
    _id, cmds, manifest, table, fn, defining = kind
    root = _project(tmp_path, cmds)
    _swap(root / manifest, table)
    path = root / defining
    text = path.read_text(encoding="utf-8")
    new = re.sub(
        rf"(\b{fn}\s*\([^;{{}}]*?)double a, double b(\)\s*\{{)",
        r"\1double b, double a\2",
        text,
        count=1,
    )
    assert new != text
    path.write_text(new, encoding="utf-8")
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _status_json(root)["param_order_drift"] == []


@pytest.mark.parametrize("kind", _KINDS, ids=_IDS)
def test_no_reorder_no_finding(tmp_path, kind):
    """An untouched project: no finding, and apply is a no-op."""
    _id, cmds, _manifest, _table, _fn, _defining = kind
    root = _project(tmp_path, cmds)
    before = _snapshot(root)
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _snapshot(root) == before
    assert _status_json(root)["param_order_drift"] == []


def test_types_or_a_rename_are_not_a_reorder():
    """The compiler's case and the harmless one stay out of it."""
    from just_makeit._ctorsig import reordered

    assert reordered("double b, double a", "double a, double b")
    # Qualifiers do not change which argument binds where.
    assert reordered(
        "const double *b, const double *a", "double *JM_RESTRICT a, double *b"
    )
    # Different types: a compile error, already warned (gh-632).
    assert not reordered("int b, double a", "double a, int b")
    # A rename binds positionally the same.
    assert not reordered("double a, double c", "double a, double b")
    assert not reordered("void", "void")


def test_example_in_another_order_is_advisory(tmp_path):
    """Reorder the manifest AND the definition: the header's example, the
    author's text, still passes the old order -- named, never counted."""
    _id, cmds, manifest, table, fn, defining = _KINDS[0]
    root = _project(tmp_path, cmds)
    _swap(root / manifest, table)
    path = root / defining
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace("(double a, double b)", "(double b, double a)", 1),
        encoding="utf-8",
    )
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "p_amp_create(a, b), declared (b, a)" in r.stdout, r.stdout
    (ex,) = _status_json(root)["create_example_drift"]
    assert ex["order"] == {"passed": "a, b", "declared": "b, a"}
