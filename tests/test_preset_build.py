"""Build regression for the no-step / void-return presets.

v0.14 foot-gun #1: the generated ``_ext.c`` for ``--return-type void``
(consumer) and ``--no-step`` (reader) objects once carried a destroy()
arg-count mismatch in ``tp_dealloc`` and failed to compile. The fix landed
on main; this test compiles both preset scaffolds end-to-end so the mismatch
cannot silently come back.

Skipped when the C toolchain is unavailable (matches test_examples.py).
"""

from _jminc import INC_ROOT  # noqa: E402
import os
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
    invalid ``float _Complex[] *out`` into ``_core.h`` / ``_core.c`` / ``_ext.c``
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
    core_h = (root / INC_ROOT / "filt/filt_core.h").read_text()
    core_c = (root / "native/src/filt/filt_core.c").read_text()
    ext_c = (root / "native/src/filt/filt_ext.c").read_text()
    for text in (core_h, core_c, ext_c):
        assert "[] *out" not in text
        assert "sizeof(float _Complex[])" not in text
    assert "float _Complex *out" in core_h
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


# ---------------------------------------------------------------------------
# gh-1742 / gh-1745: a scaffold builds, and builds warning-clean
# ---------------------------------------------------------------------------
#
# The class both issues belong to: a tree jm scaffolds must build, and build
# under the warning flags a real C project turns on. doppler builds its C with
# `-Wall -Wextra -Werror` (doppler#1658); a scaffold that trips them is a tree
# the author cannot build without editing jm's output. So each shape below is
# scaffolded through the CLI and built -- every target, the benchmark
# included -- with those flags on, by gcc (the stringop and set-but-unused
# findings are gcc's) and by clang.
#
# The three -Wno- flags are CPython's ABI shape, not defects: METH_NOARGS's
# unused `args`, the PyCFunction cast, the `{NULL}` sentinel (gh-1745 lists
# them as "not asks").

_WARN_FLAGS = (
    "-Wall -Wextra -Werror -Wno-unused-parameter -Wno-cast-function-type"
    " -Wno-missing-field-initializers"
)

_ENUM = '\n[[enum]]\nname = "mode"\nvalues = ["a", "b", "c"]\n'

_FILL = ["--param", "b:uint8_t[]", "--out-param", "o:uint8_t[]"] + [
    "--return-type",
    "size_t",
]
_VOID = ["--arg-type", "void", "--return-type", "void"]

#: shape id -> the CLI commands that scaffold it after `jm new wp`. A `None`
#: entry appends the `[[enum]]` above to the manifest, which has no CLI.
_WARN_SHAPES = {
    "default_object": [["object", "deflt"]],
    "no_state": [["object", "ns", "--no-state"]],
    # gh-1742: no state and no step, with and without a benchable method.
    "no_state_no_step": [
        ["object", "nsns", "--no-state", "--no-step", *_VOID]
    ],
    "no_state_no_step_method": [
        ["object", "nsns", "--no-state", "--no-step", *_VOID],
        ["method", "nsns", "fill", *_FILL],
    ],
    "no_step_method": [
        ["object", "nsm", "--no-step", *_VOID],
        ["method", "nsm", "fill", *_FILL],
    ],
    # gh-1745 item 1: a module whose type only DECODES the enum (a read-only
    # property) has no call site for the lookup ...
    "module_enum_decode_only": [
        None,
        ["module", "m"],
        ["object", "ro", "--module", "m", "--state", "mode:int:0"],
        ["property", "ro", "mode", "--module", "m", "--type", "int"]
        + ["--enum", "mode"],
    ],
    # ... and one whose setter and function parameter look it up does.
    "module_enum_looked_up": [
        None,
        ["module", "m"],
        ["object", "rw", "--module", "m", "--state", "mode:int:0"],
        ["property", "rw", "mode", "--module", "m", "--type", "int"]
        + ["--enum", "mode", "--writable"],
        ["function", "pick", "--module", "m", "--param", "k:enum:mode"]
        + ["--return-type", "int"],
    ],
}

#: (shape, compiler family) -> the one error that shape is KNOWN to still
#: produce, and the issue tracking it. Not a skip: the build must fail with
#: exactly that error and no other, so a new finding in the shape is still
#: caught -- and the day the issue is fixed this goes red and the entry
#: comes out.
_WARN_KNOWN = {
    # gh-1747: `jm_array_arg` is a main-file `static inline` with no caller
    # in an extension that takes no array; clang (not gcc) reports it.
    ("no_state_no_step", "clang"): "unused function 'jm_array_arg'",
}


def _compiler_family(cc: str) -> str:
    """``"clang"`` or ``"gcc"``, by what the compiler says it is.

    By its banner, not its name: macOS's ``gcc`` is Apple clang, and the
    clang-only finding in `_WARN_KNOWN` follows the compiler, not the name.
    """
    banner = subprocess.run(
        [cc, "--version"], capture_output=True, text=True
    ).stdout
    return "clang" if "clang" in banner else "gcc"


@pytest.mark.parametrize("cc", ["gcc", "clang"])
@pytest.mark.parametrize("shape", sorted(_WARN_SHAPES))
def test_scaffold_builds_warning_clean(shape, cc, tmp_path):
    """Every target of the shape builds with `-Wall -Wextra -Werror`.

    gh-1742: a --no-state --no-step object's benchmark timed a method on an
    `obj` it declared only inside a TODO comment -- the tree did not build at
    all. gh-1745: a dead `_enum_index` lookup (-Wunused-function), a bench
    sink stored and never read (-Wunused-but-set-variable), and
    `jm_bench.h`'s strncpy (-Wstringop-truncation) each failed a -Werror
    build.
    """
    from _jmrun import run_cli

    if _SKIP:
        pytest.skip(_SKIP)
    if not shutil.which(cc):
        pytest.skip(f"{cc} not on PATH")

    root = tmp_path / "wp"
    r = run_cli("new", "wp", str(root))
    assert r.returncode == 0, r.stderr
    for cmd in _WARN_SHAPES[shape]:
        if cmd is None:
            with (root / "just-makeit.toml").open("a", encoding="utf-8") as f:
                f.write(_ENUM)
            continue
        r = run_cli(*cmd, cwd=root)
        assert r.returncode == 0, f"jm {' '.join(cmd)}:\n{r.stderr}"

    build = root / "build"
    cfg = subprocess.run(
        ["cmake", "-S", str(root), "-B", str(build)]
        + [f"-DCMAKE_C_COMPILER={cc}", f"-DCMAKE_C_FLAGS={_WARN_FLAGS}"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert cfg.returncode == 0, f"cmake configure failed:\n{cfg.stderr}"
    # MAKEFLAGS=-k: report every failing file rather than the first, so the
    # known-finding check below sees all of them. A Ninja build ignores it.
    bld = subprocess.run(
        ["cmake", "--build", str(build)],
        capture_output=True,
        text=True,
        timeout=600,
        env={**os.environ, "MAKEFLAGS": "-k"},
    )
    errors = sorted(
        {
            line.strip()
            for line in (bld.stdout + bld.stderr).splitlines()
            if "error:" in line
        }
    )
    known = _WARN_KNOWN.get((shape, _compiler_family(cc)))
    if known is None:
        assert bld.returncode == 0, (
            f"{shape} does not build warning-clean with {cc} "
            f"({_WARN_FLAGS}):\n" + "\n".join(errors or [bld.stdout[-4000:]])
        )
        return
    assert bld.returncode != 0 and errors, (
        f"{shape} now builds clean with {cc}: the known finding "
        f"{known!r} is fixed -- drop its _WARN_KNOWN entry"
    )
    others = [e for e in errors if known not in e]
    assert not others, (
        f"{shape} with {cc}: errors beyond the known {known!r}:\n"
        + "\n".join(others)
    )
