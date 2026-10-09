"""gh-1899: switching `[project] build` reaches the Makefile.

Switching `build = "make"` to `"cmake"` (or back) left the old backend's
Makefile in place. It is create-only, so `apply` never rewrote it, and `make`
kept building without CMake. On Windows it stopped with a message telling the
author to do exactly the switch they had just made. `status` reported the file
as OUTDATED, which sent them to upgrade jm for a file the manifest had made
wrong.

The Makefile of a make project is patched as its objects are added (TARGETS and
the component rules), so it is not a function of the manifest's flags. It is
recognised by comparison with a replay of the manifest under the OTHER backend,
which is what `apply` already renders. A Makefile equal to that replay is the
other backend's, and `apply` replaces it. An author's edit equals neither, and
is left alone.

GATE: a make project whose `build` is flipped to cmake, then `apply`, has the
CMake Makefile (equal to a fresh CMake scaffold), and `status` reports the
mismatch as BACKEND drift until then. A flip the other way works too, and an
author-edited Makefile is never rewritten.

Sabotage proof: removing the `reconcile_backend_makefile` call from `apply`
turns the flip cases red.
"""

# gh-1591: this file's expectations spell jm's bare derived names, so its
# projects opt out of the prefix `jm new` now defaults to.

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _jmrun import run_cli  # noqa: E402


def _flip(root: Path, to: str) -> None:
    """Rewrite `[project] build` in the manifest, as an author would."""
    frm = "cmake" if to == "make" else "make"
    t = root / "just-makeit.toml"
    body = t.read_text(encoding="utf-8")
    old = f'build = "{frm}"'
    assert body.count(old) == 1, "manifest's build line changed"
    t.write_text(body.replace(old, f'build = "{to}"'), encoding="utf-8")


def _scaffold(tmp: Path, name: str, backend: str) -> Path:
    r = run_cli(
        "new", name, "--object", "g", "--build-system", backend, cwd=tmp
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return tmp / name


def _fresh_makefile(tmp: Path, backend: str) -> bytes:
    """The Makefile a new project of *backend* gets: the reference a flipped
    project must match."""
    sub = tmp / f"fresh-{backend}"
    sub.mkdir()
    _scaffold(sub, "p", backend)
    return (sub / "p" / "Makefile").read_bytes()


def test_flip_make_to_cmake_then_apply_writes_the_cmake_makefile(
    tmp_path: Path,
) -> None:
    root = _scaffold(tmp_path, "p", "make")
    _flip(root, "cmake")
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (root / "Makefile").read_bytes() == _fresh_makefile(
        tmp_path, "cmake"
    )


def test_flip_cmake_to_make_then_apply_writes_the_make_makefile(
    tmp_path: Path,
) -> None:
    root = _scaffold(tmp_path, "p", "cmake")
    _flip(root, "make")
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (root / "Makefile").read_bytes() == _fresh_makefile(
        tmp_path, "make"
    )


def test_before_apply_status_reports_the_backend_not_outdated(
    tmp_path: Path,
) -> None:
    """The mismatch is drift: it fails `--check`, and it is not the 'upgrade
    jm' advice OUTDATED gives for a file the manifest did not make wrong."""
    root = _scaffold(tmp_path, "p", "make")
    _flip(root, "cmake")
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "BACKEND (1)" in r.stdout, r.stdout
    assert "↑ Makefile" not in r.stdout, r.stdout


def test_status_json_names_the_backend_mismatch(tmp_path: Path) -> None:
    root = _scaffold(tmp_path, "p", "make")
    _flip(root, "cmake")
    r = run_cli("status", "--json", cwd=root)
    doc = json.loads(r.stdout)
    assert doc["backend"] == [
        {"path": "Makefile", "backend": "make", "allowed": False}
    ], doc["backend"]


def test_an_authors_edit_is_not_rewritten(tmp_path: Path) -> None:
    """A Makefile that is neither backend's render is the author's: `apply`
    leaves it, and `status` keeps reporting it as it always has."""
    root = _scaffold(tmp_path, "p", "make")
    mf = root / "Makefile"
    edited = mf.read_bytes() + b"\n# the author's own line\n"
    mf.write_bytes(edited)
    _flip(root, "cmake")
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert mf.read_bytes() == edited
    st = run_cli("status", cwd=root)
    assert "BACKEND (" not in st.stdout, st.stdout
