"""gh-2854: a standalone component named for a module's parent package.

A standalone component ``dsp`` imports as ``p.dsp`` (``p/dsp.so``). A dotted
module ``dsp.filt`` -- or a module with ``package = "dsp"`` -- makes
``p/dsp/`` a package, and the package wins the import: ``from p.dsp import
Dsp`` raised ``ImportError`` on a tree every verb and ``status --check``
accepted. gh-2142's one namespace rule (`_config.namespace_errors`) now
refuses the pair, in either order, at the verb and at load.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _jmrun import run_cli


def _tree(root: Path) -> "dict[Path, bytes]":
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _project(tmp_path: Path, *steps: "tuple[str, ...]") -> Path:
    r = run_cli("new", "p", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    root = tmp_path / "p"
    for step in steps:
        r = run_cli(*step, cwd=root)
        assert r.returncode == 0, (step, r.stdout + r.stderr)
    return root


_CLASHES = {
    "module-then-object": (
        [("module", "dsp.filt")],
        ("object", "dsp"),
        "p.dsp",
    ),
    "object-then-module": (
        [("object", "dsp")],
        ("module", "dsp.filt"),
        "p.dsp",
    ),
    "deep-module": ([("module", "a.b.c")], ("object", "a"), "p.a"),
    "package-key": (
        [("module", "wfm_reader", "--package", "wfm")],
        ("object", "wfm"),
        "p.wfm",
    ),
}


@pytest.mark.parametrize("case", sorted(_CLASHES))
def test_the_pair_is_refused_before_it_writes(tmp_path, case):
    first, last, name = _CLASHES[case]
    root = _project(tmp_path, *first)
    before = _tree(root)
    r = run_cli(*last, cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"claim the Python name '{name}'" in " ".join(r.stderr.split())
    assert _tree(root) == before


_ACCEPTED = {
    # A member of the dotted module lives inside its package.
    "member-of-the-module": [
        ("module", "dsp.filt"),
        ("object", "x", "--module", "dsp.filt"),
    ],
    # The LEAF name is free at the top: p/filt.so beside p/dsp/filt/.
    "leaf-named-standalone": [("module", "dsp.filt"), ("object", "filt")],
    # A module in another module's package: doppler's `wfm_reader` shape.
    "module-in-a-module-package": [
        ("module", "wfm"),
        ("module", "wfm_reader", "--package", "wfm"),
    ],
    # A MEMBER named for the parent package lives in its own module.
    "member-named-like-the-package": [
        ("module", "dsp.filt"),
        ("module", "other"),
        ("object", "dsp", "--module", "other"),
    ],
}


@pytest.mark.parametrize("case", sorted(_ACCEPTED))
def test_what_shares_no_python_name_is_accepted(tmp_path, case):
    root = _project(tmp_path, *_ACCEPTED[case])
    s = run_cli("status", "--check", cwd=root)
    assert s.returncode == 0, s.stdout + s.stderr


def test_a_hand_edited_manifest_is_refused_at_load(tmp_path):
    root = _project(tmp_path, ("module", "dsp.filt"), ("object", "x"))
    frag = root / "objects" / "x.toml"
    text = frag.read_text(encoding="utf-8")
    assert text.count("[x]") == 1
    frag.rename(root / "objects" / "dsp.toml")
    (root / "objects" / "dsp.toml").write_text(
        text.replace("[x]", "[dsp]").replace("[[x.", "[[dsp."),
        encoding="utf-8",
    )
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "claim the Python name 'p.dsp'" in " ".join(
        (r.stdout + r.stderr).split()
    )


def test_a_flat_clash_is_reported_once(tmp_path):
    # A flat module named for the component is gh-2142's stem clash; the
    # Python-name rule does not report it a second time.
    root = _project(tmp_path, ("module", "filt"))
    r = run_cli("object", "filt", cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "claim the Python name" not in r.stderr, r.stderr
