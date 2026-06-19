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
    ["consumer", "reader", "blockwise"],
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
        timeout=600,
    )
    assert cfg.returncode == 0, f"cmake configure failed:\n{cfg.stderr}"

    bld = subprocess.run(
        ["cmake", "--build", str(build)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    # A clean build (compile + link) is the foot-gun #1 regression signal:
    # the bug was a destroy() arg-count mismatch that failed to *compile*.
    # We deliberately don't assert on a built artifact path — the Python
    # module lands in src/<pkg>/ (not build/), which made the old check
    # flaky.
    assert bld.returncode == 0, (
        f"build failed for --preset {preset} "
        f"(foot-gun #1 regression):\n{bld.stdout}\n{bld.stderr}"
    )


def test_array_return_variable_output_compiles(tmp_path, monkeypatch):
    """A ``--variable-output`` method whose return type carries an explicit
    ``[]`` (e.g. ``--return-type "float _Complex[]"``) once rendered the
    invalid ``float complex[] *out`` into ``_core.h`` / ``_core.c`` / ``_ext.c``
    and failed to compile (gh-201 follow-up). The output buffer holds elements,
    so the ``[]`` is now stripped to the element type.
    """
    if _SKIP:
        pytest.skip(_SKIP)

    root = tmp_path / "proj"
    new_run("proj", root)
    monkeypatch.chdir(root)
    _cli_object.run(
        [
            "filt",
            "--arg-type",
            "float _Complex[]",
            "--return-type",
            "float _Complex[]",
            "--variable-output",
        ]
    )

    # The element type is stripped everywhere the buffer/param/sizeof renders.
    core_h = (root / "native/inc/filt/filt_core.h").read_text()
    core_c = (root / "native/src/filt/filt_core.c").read_text()
    ext_c = (root / "native/src/filt/filt_ext.c").read_text()
    for text in (core_h, core_c, ext_c):
        assert "[] *out" not in text
        assert "sizeof(float complex[])" not in text
    assert "float complex *out" in core_h
    assert (
        "NPY_COMPLEX64" in ext_c
    )  # element NumPy enum, not the NPY_FLOAT fallback

    build = root / "build"
    cfg = subprocess.run(
        ["cmake", "-S", str(root), "-B", str(build)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert cfg.returncode == 0, f"cmake configure failed:\n{cfg.stderr}"
    bld = subprocess.run(
        ["cmake", "--build", str(build)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert bld.returncode == 0, (
        f"array-return variable_output build failed:\n{bld.stdout}\n{bld.stderr}"
    )
