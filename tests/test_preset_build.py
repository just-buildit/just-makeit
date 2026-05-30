"""Build regression for the no-step / void-return presets.

v0.14 foot-gun #1: the generated ``_ext.c`` for ``--return-type void``
(consumer) and ``--no-step`` (reader) objects once carried a destroy()
arg-count mismatch in ``tp_dealloc`` and failed to compile. The fix landed
on main; this test compiles both preset scaffolds end-to-end so the mismatch
cannot silently come back.

Skipped when the C toolchain is unavailable (matches test_examples.py).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from just_makeit import _cli_object
from just_makeit._new import run as new_run


def _skip_reason():
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    return None


_SKIP = _skip_reason()


@pytest.mark.parametrize(
    "preset",
    ["consumer", "reader"],
)
def test_preset_scaffold_compiles(preset, tmp_path, monkeypatch):
    if _SKIP:
        pytest.skip(_SKIP)

    root = tmp_path / "proj"
    new_run("proj", root)

    # _cli_object.run resolves the project from the cwd and expands --preset.
    monkeypatch.chdir(root)
    _cli_object.run(["comp", "--preset", preset])

    build = root / "build"
    cfg = subprocess.run(
        ["cmake", "-S", str(root), "-B", str(build)],
        capture_output=True,
        text=True,
    )
    assert cfg.returncode == 0, f"cmake configure failed:\n{cfg.stderr}"

    bld = subprocess.run(
        ["cmake", "--build", str(build)],
        capture_output=True,
        text=True,
    )
    assert bld.returncode == 0, (
        f"build failed for --preset {preset} "
        f"(foot-gun #1 regression):\n{bld.stdout}\n{bld.stderr}"
    )
    assert list(Path(build).rglob("comp*.so")) or list(Path(build).rglob("*.so")), (
        "no extension module produced"
    )
