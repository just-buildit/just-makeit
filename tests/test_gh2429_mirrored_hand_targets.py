"""gh-2429: switching ``[project] layout`` keeps the author's own CMake
targets building.

doppler dry-ran the mirrored layout (gh-2423) and found two gaps in the
respell `apply` runs after moving the generated native tests:

A. The ``<c>_extra.cmake`` hook -- the file jm includes from every
   component CMakeLists and never writes (gh-1351), where an author's own
   targets for the directory go -- was not among the files respelled.
   `status --check` stayed clean and cmake configure failed.
B. A hand-written target whose source path WAS respelled lost the
   directory the source left. A C file finds ``#include "x.h"`` beside
   itself first, so a test moved out of ``native/tests/`` no longer found
   ``jm_test.h`` or the author's ``dp_test.h``. jm's own test target is
   given ``native/tests`` for exactly that (`_incpath.native_slots`).

GATES:

1. The hook is respelled, both directions, and a second `apply` changes
   nothing.
2. Real compile: hand targets in the root CMakeLists and in the hook, each
   including a header beside its source, configure, build and pass `ctest`
   after the switch to mirrored, and again after the switch back with a
   header the author put in the mirrored directory.
3. `status` before `apply` names each move once, with `apply` as its
   command, and still counts it.
4. The include line jm adds to its own test target is aligned under the
   one it follows.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _incpath as INC


def _jm(cwd, *args):
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r


def _declare(root: Path, value: str) -> None:
    toml = root / "just-makeit.toml"
    text = re.sub(
        r'^layout = "[^"]*"\n',
        "",
        toml.read_text(encoding="utf-8"),
        flags=re.M,
    )
    if value:
        text = text.replace("[project]\n", f'[project]\nlayout = "{value}"\n')
    toml.write_text(text, encoding="utf-8")


def _rel(kind: str, root: Path) -> str:
    return INC.native_rel(kind, "fir", root)


def _hook(root: Path) -> Path:
    return root / "native" / "src" / "fir" / "fir_extra.cmake"


def _scaffold(where: Path) -> Path:
    """A flat project whose author added targets of their own: one in the
    hook, building fir's test, one in the root CMakeLists, building its
    bench, each source including a header that sits beside it."""
    _jm(where, "new", "p")
    root = where / "p"
    _jm(root, "object", "fir")
    test, symbols, bench = (
        _rel(k, root) for k in ("test", "symbols", "bench")
    )
    _hook(root).write_text(
        "add_executable(test_fir_hand\n"
        f"               ${{CMAKE_SOURCE_DIR}}/{test}\n"
        f"               ${{CMAKE_SOURCE_DIR}}/{symbols})\n"
        "target_link_libraries(test_fir_hand PRIVATE fir_core"
        " ${JM_MATH_LIBRARY})\n"
        "target_include_directories(test_fir_hand"
        " PRIVATE ${CMAKE_SOURCE_DIR}/native/inc)\n"
        "add_test(NAME test_fir_hand COMMAND test_fir_hand)\n",
        encoding="utf-8",
    )
    top = root / "CMakeLists.txt"
    top.write_text(
        top.read_text(encoding="utf-8") + "\nadd_executable(bench_fir_hand"
        f" ${{CMAKE_SOURCE_DIR}}/{bench})\n"
        "target_link_libraries(bench_fir_hand PRIVATE fir_core"
        " ${JM_MATH_LIBRARY})\n"
        "target_include_directories(bench_fir_hand"
        " PRIVATE ${CMAKE_SOURCE_DIR}/native/inc)\n",
        encoding="utf-8",
    )
    _beside(root, "test", "dp_test.h")
    _beside(root, "bench", "dp_bench.h")
    return root


def _beside(root: Path, kind: str, header: str) -> None:
    """Put *header* beside fir's *kind* source and include it there."""
    src = root / _rel(kind, root)
    (src.parent / header).write_text(
        f"#define DP_{kind.upper()} 1\n", encoding="utf-8"
    )
    text = src.read_text(encoding="utf-8")
    src.write_text(f'#include "{header}"\n' + text, encoding="utf-8")


def _build_files(root: Path) -> str:
    return _hook(root).read_text(encoding="utf-8") + (
        root / "CMakeLists.txt"
    ).read_text(encoding="utf-8")


def _gives(text: str, tree: str) -> "list[str]":
    """Each line giving ``test_fir_hand`` *tree*, however it is wrapped."""
    return re.findall(
        r"target_include_directories\(test_fir_hand\s+PRIVATE"
        rf" \$\{{CMAKE_SOURCE_DIR\}}/{re.escape(tree)}\)",
        text,
    )


def test_the_hook_is_respelled_both_ways(tmp_path):
    root = _scaffold(tmp_path)
    flat = {k: _rel(k, root) for k in ("test", "symbols")}
    _declare(root, "mirrored")
    _jm(root, "apply")
    hook = _hook(root).read_text(encoding="utf-8")
    for kind, old in flat.items():
        assert f"/{old}\n" not in hook and f"/{old})" not in hook, hook
        assert f"${{CMAKE_SOURCE_DIR}}/{_rel(kind, root)}" in hook, hook
    # The target is given the directory its sources left (gh-2429 B).
    assert len(_gives(hook, INC.TESTS_DIR)) == 1, hook
    before = _build_files(root)
    _jm(root, "apply")
    assert _build_files(root) == before  # a second run adds nothing

    # Back to flat, with a header the author keeps in the mirrored
    # directory: it survives the move, and is the directory the test left.
    nested = root / INC.TESTS_DIR / "fir"
    (nested / "dp_nested.h").write_text("#define N 1\n", encoding="utf-8")
    _declare(root, "")
    _jm(root, "apply")
    hook = _hook(root).read_text(encoding="utf-8")
    for kind, old in flat.items():
        assert f"${{CMAKE_SOURCE_DIR}}/{old}\n" in hook or (
            f"${{CMAKE_SOURCE_DIR}}/{old})" in hook
        ), hook
    assert len(_gives(hook, f"{INC.TESTS_DIR}/fir")) == 1, hook
    _jm(root, "status", "--check")


def _ctest(root: Path, build: Path) -> str:
    for cmd in (
        ["cmake", "-S", str(root), "-B", str(build), "-DBUILD_PYTHON=OFF"],
        ["cmake", "--build", str(build), "-j4"],
        ["ctest", "--test-dir", str(build), "--output-on-failure"],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True)
        assert r.returncode == 0, (cmd, r.stdout[-3000:], r.stderr[-3000:])
    assert any(build.rglob("bench_fir_hand")), "the root hand target built"
    return r.stdout


@pytest.mark.skipif(not shutil.which("cmake"), reason="cmake not available")
def test_hand_targets_build_after_switching_both_ways(tmp_path):
    root = _scaffold(tmp_path)
    _declare(root, "mirrored")
    _jm(root, "apply")
    out = _ctest(root, tmp_path / "b1")
    assert "test_fir_hand" in out

    _declare(root, "")
    _jm(root, "apply")
    out = _ctest(root, tmp_path / "b2")
    assert "test_fir_hand" in out


def test_status_names_each_move_once_with_apply(tmp_path):
    root = _scaffold(tmp_path)
    _declare(root, "mirrored")
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode != 0  # a pending move is drift
    assert "MISSING" not in r.stdout, r.stdout
    assert "just-makeit upgrade" not in r.stdout, r.stdout
    for kind in INC.NATIVE_FILES:
        new = _rel(kind, root)
        lines = [
            ln for ln in r.stdout.splitlines() if ln.strip() == f"+ {new}"
        ]
        assert not lines, (new, r.stdout)
        assert f"({new})" in r.stdout, r.stdout
    assert "3 to move" in r.stdout, r.stdout
    assert r.stdout.count("Run `just-makeit apply` to rename it") == 3


def test_the_jm_test_include_is_aligned(tmp_path):
    _jm(tmp_path, "new", "p")
    root = tmp_path / "p"
    _declare(root, "mirrored")
    _jm(root, "object", "gain")
    _jm(root, "module", "filt")
    _jm(root, "object", "fir", "--module", "filt")
    want = f"${{CMAKE_SOURCE_DIR}}/{INC.TESTS_DIR})"
    seen = 0
    for cml in (root / "native" / "src").glob("*/CMakeLists.txt"):
        lines = cml.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.strip() == want:
                above = lines[i - 1]
                assert "PRIVATE ${CMAKE_SOURCE_DIR}" in above, above
                assert line.index("$") == above.index("$"), (above, line)
                seen += 1
    assert seen >= 2
