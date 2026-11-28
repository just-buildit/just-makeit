"""gh-2854: a standalone component named for a module's parent package.

A standalone component ``dsp`` imports as ``p.dsp`` (``p/dsp.so``). A dotted
module ``dsp.filt`` -- or a module with ``package = "dsp"`` -- makes
``p/dsp/`` a package, and the package wins the import: ``from p.dsp import
Dsp`` raised ``ImportError`` on a tree every verb and ``status --check``
accepted. gh-2142's one namespace rule (`_config.namespace_errors`) now
refuses the pair, in either order, at the verb and at load.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli
from just_makeit import _config as C


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
    # Through `load` alone: `status --check` also saves, and `save` asks
    # the same rule, so it could not tell load's call from save's.
    root = _project(tmp_path, ("module", "dsp.filt"), ("object", "x"))
    frag = root / "objects" / "x.toml"
    text = frag.read_text(encoding="utf-8")
    assert text.count("[x]") == 1
    frag.unlink()
    (root / "objects" / "dsp.toml").write_text(
        text.replace("[x]", "[dsp]").replace("[[x.", "[[dsp."),
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as e:
        C.load(root)
    assert "claim the Python name 'p.dsp'" in " ".join(str(e.value).split())


def test_a_flat_clash_is_reported_once(tmp_path):
    # A flat module named for the component is gh-2142's stem clash; the
    # Python-name rule does not report it a second time.
    root = _project(tmp_path, ("module", "filt"))
    r = run_cli("object", "filt", cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "claim the Python name" not in r.stderr, r.stderr


# jm's own packages (`_config.PACKAGE_DIRS`): `src/<pkg>/tests/` and
# `benchmarks/`, and the same in every module's package.
_RESERVED = {
    "standalone-tests": ([], ("object", "tests")),
    "standalone-benchmarks": ([], ("object", "benchmarks")),
    "module-tests": ([], ("module", "tests")),
    "module-in-a-modules-tests": (
        [("module", "filt")],
        ("module", "filt.tests"),
    ),
    "module-packaged-in-benchmarks": (
        [("module", "filt")],
        ("module", "q", "--package", "filt/benchmarks"),
    ),
}


@pytest.mark.parametrize("case", sorted(_RESERVED))
def test_jms_own_packages_are_reserved(tmp_path, case):
    first, last = _RESERVED[case]
    root = _project(tmp_path, *first)
    before = _tree(root)
    r = run_cli(*last, cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "jm's own package" in " ".join(r.stderr.split()), r.stderr
    assert _tree(root) == before


@pytest.mark.skipif(
    not shutil.which("cmake") or default_cc() is None,
    reason="needs cmake and a C compiler",
)
def test_every_package_jm_writes_is_owned_or_reserved(tmp_path):
    """Derived, not listed: build a project with a standalone object, a
    flat module and a dotted one, and every directory jm (and the build)
    put below `src/<pkg>/` is a component's, a module's package (or one of
    its parents), or in `PACKAGE_DIRS` there. A new package jm starts
    writing fails here until it is reserved."""
    root = _project(
        tmp_path,
        ("object", "g"),
        ("module", "m"),
        ("object", "x", "--module", "m"),
        ("module", "d.e"),
        ("object", "y", "--module", "d.e"),
    )
    build = root / "build"
    for cmd in (
        [
            "cmake",
            "-S",
            str(root),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        ["cmake", "--build", str(build), "-j", "4"],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    cfg = C.load(root)
    src = root / "src" / "p"
    # The build ran: the standalone object's extension is there.
    assert list(src.glob("g.*.so")) or list(src.glob("g.*.pyd")), sorted(
        p.name for p in src.iterdir()
    )
    packages = {C.module_package_resolved(cfg, m) for m in C.modules(cfg)}
    owned = set(packages) | {
        "/".join(p.split("/")[: i + 1])
        for p in packages
        for i in range(len(p.split("/")))
    }
    owned |= {c for c in C.components(cfg) if not C.module_of(cfg, c)}
    reserved = {
        f"{base}{d}"
        for base in ["", *(f"{p}/" for p in packages)]
        for d in C.PACKAGE_DIRS
    }
    written = {
        d.relative_to(src).as_posix()
        for d in src.rglob("*")
        if d.is_dir() and d.name != "__pycache__"
    }
    assert written, "the build wrote nothing below src/p"
    assert written <= owned | reserved, sorted(written - owned - reserved)
    # Not vacuous: jm did write its own packages at both levels.
    assert {"tests", "benchmarks", "m/tests", "d/e/tests"} <= written
