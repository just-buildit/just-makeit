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
import re
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
# No -Wno- flag: the unused `args` / `kwds`, the PyCFunction cast and the
# `{NULL}` sentinel were once waved through as CPython's ABI shape (gh-1745),
# and a downstream building its own C with -Wall -Wextra could not fix them in
# generated files (gh-1856). Nor for the author's own files: a scaffolded
# `_core.c` / `_core.h` stub whose parameters wait for the author says so with
# `(void)name;`, as every method stub always did (gh-1857), so a new project
# builds clean before a line of it is written.
_WARN_FLAGS = "-Wall -Wextra -Werror"

_ENUM = '\n[[enum]]\nname = "mode"\nvalues = ["a", "b", "c"]\n'
# gh-1748: an enum bound to C constants (gh-1450), which adds the
# `_enum_<name>_name` reverse lookup beside its tables.
_ENUM_CONSTANTS = (
    '\n[[enum]]\nname = "lvl"\nvalues = ["lo", "hi"]\n'
    'enumerators = ["0", "1"]\n'
)

#: gh-1825/gh-1827: a dtype-dispatched array (`real_type` has no CLI) beside
#: a defaulted one, declared in the manifest before anything is
#: materialized, so `apply` scaffolds it fresh.
_DISPATCH = (
    '\n[disp]\narg_type = "void"\nreturn_type = "void"\n'
    'no_state = "true"\nno_step = "true"\n\n'
    '[[disp.init_params]]\nname = "taps"\ntype = "float _Complex[]"\n'
    'real_type = "float[]"\nreal_create_fn = "wp_disp_create_real"\n\n'
    '[[disp.init_params]]\nname = "sync"\ntype = "uint8_t[]"\n'
    'default = "[]"\n'
)
#: gh-1827: an `optional` array's own constructor, from the CLI alone.
_OPTIONAL = ["--no-state", "--no-step", "--arg-type", "void"] + [
    "--return-type",
    "void",
    "--init-param",
    "taps:float[]:optional:wp_disp_create_taps",
    "--init-param",
    "k:int:0",
]

_FILL = ["--param", "b:uint8_t[]", "--out-param", "o:uint8_t[]"] + [
    "--return-type",
    "size_t",
]
_VOID = ["--arg-type", "void", "--return-type", "void"]


def _no_arg_record_methods(obj: str, *module: str) -> list:
    """gh-1959: an object whose methods take nothing and return records.

    One method returns ONE record (`--single`), the other a list of them.
    Both wrappers took ``(self, args)`` and never read ``args``. The record
    is a struct the C library already declares -- ``div_t``, through
    ``clib_common.h``'s ``<stdlib.h>`` -- so the shape builds with no edit
    to the sacred header.
    """
    rec = ["--arg-type", "void", "--return-type", "div_t"] + [
        "--result-field",
        "quot:int",
        "--result-field",
        "rem:int",
    ]
    return [
        ["object", obj, *module, "--state", "k:int:0", "--no-step", *_VOID],
        ["method", obj, "one", *module, *rec, "--single"],
        ["method", obj, "rows", *module, *rec],
    ]


#: shape id -> the CLI commands that scaffold it after `jm new wp`. A `str`
#: entry is appended to the manifest verbatim: an `[[enum]]` has no CLI. A
#: `(path, text)` entry appends *text* to that file under the project, for a
#: key with no CLI on a table the commands above it wrote.
_WARN_SHAPES = {
    "default_object": [["object", "deflt"]],
    # gh-1747: the extension calls only `jm_array_arg_hint` (a `str_hint`,
    # gh-1756, on its one array argument), so the `jm_array_arg` wrapper
    # beside it goes uncalled.
    "array_arg_hint_only": [
        ["object", "hint", "--no-state", "--no-step", *_VOID]
        + ["--init-param", "b:uint8_t[]"],
        ("objects/hint.toml", 'str_hint = "pass bytes"\n'),
        ["apply"],
    ],
    "no_state": [["object", "ns", "--no-state"]],
    # gh-1856: a `--batch` method is `METH_KEYWORDS`, a three-argument
    # PyCFunctionWithKeywords the table must cast through `void (*)(void)`.
    "batch_method": [
        ["object", "bm"],
        ["method", "bm", "blk", "--arg-type", "float"]
        + ["--return-type", "float", "--batch"],
    ],
    # The feature tour's `magnitude_db`: an inline module function with an
    # array param and an allocated output. Its header stub had no `out` while
    # the binding passed one, so the scaffold did not compile at all.
    "inline_out_type_function": [
        ["module", "m"],
        ["function", "mag", "--module", "m", "--param", "x:float _Complex[]"]
        + ["--out-type", "float", "--inline"],
    ],
    # gh-1716: the returned-count guard (`_coerce.returned_count_c`) on
    # every path that builds with no author C -- a module function's ndarray
    # and `str` outputs, and a method's allocated and `out=` paths.
    "variable_output_function": [
        ["module", "m"],
        ["function", "fb", "--module", "m", "--param", "n:size_t"]
        + ["--return-type", "size_t", "--out-type", "uint8_t"]
        + ["--variable-output", "--out-size", "n"],
        # A `str` output has no CLI spelling (gh-1180): declared, then applied.
        (
            "modules/m.toml",
            '\n[[module.m.functions]]\nname = "fs"\nreturn_type = "size_t"\n'
            'out_type = "str"\nvariable_output = true\nout_size = "n"\n\n'
            '[[module.m.functions.params]]\nname = "n"\ntype = "size_t"\n',
        ),
        ["apply"],
    ],
    "variable_output_method": [
        ["object", "gen", "--state", "k:size_t:0", "--no-step", *_VOID],
        ["method", "gen", "burst", "--arg-type", "void"]
        + ["--return-type", "uint32_t", "--variable-output"],
    ],
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
    # gh-1959: no-argument record methods, on both faces. In the module, one
    # fragment as `jm method` wrote it and one adopted (`fragment =
    # "generated"`, as doppler's is) and so re-rendered whole by `apply`.
    "no_arg_record_methods": _no_arg_record_methods("rec"),
    "module_no_arg_record_methods": [
        ["module", "m"],
        *_no_arg_record_methods("rec", "--module", "m"),
        *_no_arg_record_methods("gen", "--module", "m"),
        ["adopt", "gen"],
        ["apply"],
    ],
    # gh-1745 item 1: a module whose type only DECODES the enum (a read-only
    # property) has no call site for the lookup ...
    "module_enum_decode_only": [
        _ENUM,
        ["module", "m"],
        ["object", "ro", "--module", "m", "--state", "mode:int:0"],
        ["property", "ro", "mode", "--module", "m", "--type", "int"]
        + ["--enum", "mode"],
    ],
    # ... and one whose setter and function parameter look it up does.
    "module_enum_looked_up": [
        _ENUM,
        ["module", "m"],
        ["object", "rw", "--module", "m", "--state", "mode:int:0"],
        ["property", "rw", "mode", "--module", "m", "--type", "int"]
        + ["--enum", "mode", "--writable"],
        ["function", "pick", "--module", "m", "--param", "k:enum:mode"]
        + ["--return-type", "int"],
    ],
    # gh-1748: a constant-bound enum only a function parameter looks up has
    # no decoder, so no `_enum_lvl_name` ... (and, gh-1747, a module whose
    # extension takes no array argument at all).
    "module_enum_constants_looked_up": [
        _ENUM_CONSTANTS,
        ["module", "m"],
        ["function", "pick", "--module", "m", "--param", "k:enum:lvl"]
        + ["--return-type", "int"],
    ],
    # ... while a property's getter decodes it, so the object face keeps it.
    "module_enum_constants_decoded": [
        _ENUM_CONSTANTS,
        ["module", "m"],
        ["object", "ro", "--module", "m", "--state", "lv:int:0"],
        ["property", "ro", "lv", "--module", "m", "--type", "int"]
        + ["--enum", "lvl"],
        ["function", "pick", "--module", "m", "--param", "k:enum:lvl"]
        + ["--return-type", "int"],
    ],
    # gh-1825: the dispatch's real call used `sync_arr` / `sync_len` before
    # the defaulted array declared them; gh-1827: `wp_disp_create_real` was
    # neither declared nor stubbed. Both faces.
    "dispatch_defaulted_array": [_DISPATCH, ["apply"]],
    "module_dispatch_defaulted_array": [
        '\n[module.m]\nobjects = ["disp"]\n' + _DISPATCH,
        ["apply"],
    ],
    # gh-1827: an optional array's `create_fn`, likewise -- and under
    # `header_only`, where both constructors are `static inline` in the
    # header rather than defined in a `_core.c`.
    "optional_array_create_fn": [["object", "disp", *_OPTIONAL]],
    "header_only_optional_array_create_fn": [
        ["object", "disp", "--header-only", *_OPTIONAL]
    ],
    "module_optional_array_create_fn": [
        ["module", "m"],
        ["object", "disp", "--module", "m", *_OPTIONAL],
    ],
    # gh-1857: the constructor stubs whose parameters reach the body unread
    # -- an --array-arg beside the state fields create() does assign, init
    # params in place of those fields (on both faces of the core), and a
    # view's own constructor, appended to `_core.c` by `jm view`.
    "array_arg_with_state": [["object", "st", "--array-arg", "h:float32"]],
    "init_params_with_state": [
        ["object", "si", "--init-param", "taps:float[]"]
        + ["--init-param", "n:int:4"]
    ],
    "header_only_init_params": [
        ["object", "hi", "--header-only", "--init-param", "n:int:4"]
    ],
    # ... and reset() with nothing to restore: an object declared without
    # `no_state` and without a state field (no CLI spelling).
    "no_state_fields": [
        '\n[nf]\narg_type = "float"\nreturn_type = "float"\n',
        ["apply"],
    ],
    "view_create_fn": [
        ["module", "m"],
        ["object", "vw", "--module", "m", "--state", "k:int:0"],
        ["view", "vw", "Burst", "--module", "m", "--create-fn"]
        + ["vw_create_burst", "--init-param", "n:int:2"],
    ],
}

#: (shape, compiler family) -> the one error that shape is KNOWN to still
#: produce, and the issue tracking it. Not a skip: the build must fail with
#: exactly that error and no other, so a new finding in the shape is still
#: caught -- and the day the issue is fixed this goes red and the entry
#: comes out.
_WARN_KNOWN: "dict[tuple[str, str], str]" = {}


def find_compiler(name: str) -> "str | None":
    """The path of compiler *name* on PATH, bare or versioned, else None.

    ``shutil.which(name)`` first; failing that, the newest ``<name>-<N>``
    on PATH (gh-1861). Debian installs clang only as ``clang-18`` (its
    ``clang`` is a separate package), so a bare lookup reported clang
    missing on a box that has it, and the sweep's clang leg skipped there
    -- which the skip gate rightly turns into a red ``make test``. Among
    several versions the highest number wins, and among one version's
    copies the first on PATH, which is the one a shell would run.

    Only an absent compiler is None: then the caller's skip names what is
    missing, and the skip gate holds it red, because installing a compiler
    is a fix a maintainer can make.

    `test_find_compiler_takes_the_newest_versioned_name` holds each of
    these on a PATH it builds.

    Examples
    --------
    >>> find_compiler("no-such-cc") is None
    True
    """
    exe = shutil.which(name)
    if exe:
        return exe
    versioned = re.compile(re.escape(name) + r"-(\d+)")
    best: "tuple[int, str] | None" = None
    for d in os.environ.get("PATH", "").split(os.pathsep):
        try:
            entries = os.listdir(d or os.curdir)
        except OSError:
            continue
        for entry in entries:
            m = versioned.fullmatch(entry)
            hit = m and shutil.which(entry, path=d or os.curdir)
            if hit and (best is None or int(m.group(1)) > best[0]):
                best = (int(m.group(1)), hit)
    return best[1] if best else None


def test_find_compiler_takes_the_newest_versioned_name(tmp_path, monkeypatch):
    """gh-1861: a box with only ``clang-NN`` still runs the clang leg.

    Built on a PATH this test owns, so it holds on every box whatever
    compilers it has. The decoys are what a Debian ``/usr/bin`` carries
    beside ``clang-18``: other tools sharing the prefix, and a file that is
    not executable.
    """

    def put(d, *names, mode=0o755):
        d.mkdir(exist_ok=True)
        for n in names:
            (d / n).write_text("#!/bin/sh\n")
            (d / n).chmod(mode)
        return d

    first = put(tmp_path / "a", "clang-17", "clang-18", "clang-format-19")
    second = put(tmp_path / "b", "clang-9", "clang-18", "clang-tidy")
    put(second, "clang-20", mode=0o644)
    monkeypatch.setenv("PATH", os.pathsep.join([str(first), str(second)]))
    # Highest version, and of its two copies the one a shell would run.
    assert find_compiler("clang") == str(first / "clang-18")

    # A bare name wins outright: it is the compiler the box calls `clang`.
    put(second, "clang")
    assert find_compiler("clang") == str(second / "clang")

    # Neither: the caller skips, naming what is missing.
    monkeypatch.setenv("PATH", str(put(tmp_path / "c", "clang-format")))
    assert find_compiler("clang") is None


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
    build. gh-1857: a constructor or reset() stub left the parameters it
    does not read unsuppressed (-Wunused-parameter). gh-1959: so did the
    binding of a no-argument method returning records, on both faces.
    """
    from _jmrun import run_cli

    if _SKIP:
        pytest.skip(_SKIP)
    exe = find_compiler(cc)
    if exe is None:
        pytest.skip(f"{cc} not on PATH, bare or as {cc}-<N>")

    root = tmp_path / "wp"
    r = run_cli("new", "wp", str(root))
    assert r.returncode == 0, r.stderr
    for cmd in _WARN_SHAPES[shape]:
        if isinstance(cmd, (str, tuple)):
            rel, text = cmd if isinstance(cmd, tuple) else ("", cmd)
            target = root / (rel or "just-makeit.toml")
            with target.open("a", encoding="utf-8") as f:
                f.write(text)
            continue
        r = run_cli(*cmd, cwd=root)
        assert r.returncode == 0, f"jm {' '.join(cmd)}:\n{r.stderr}"

    build = root / "build"
    cfg = subprocess.run(
        ["cmake", "-S", str(root), "-B", str(build)]
        + [f"-DCMAKE_C_COMPILER={exe}", f"-DCMAKE_C_FLAGS={_WARN_FLAGS}"],
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
    # clang's -Wmissing-field-initializers fires on a one-field `{NULL}`
    # sentinel, gcc's does not -- so read it off the text, where both
    # compilers' legs (and a box with only gcc) see it.
    short = sorted(
        f"{f.relative_to(root)}: {m.group().strip()}"
        for f in root.glob("native/src/**/*_ext*.c")
        for m in re.finditer(r"^\s*\{\s*NULL\s*\}\s*$", f.read_text(), re.M)
    )
    assert not short, (
        f"{shape}: a one-field table sentinel (gh-1856); CPython documents"
        f" the full-width form:\n" + "\n".join(short)
    )
    known = _WARN_KNOWN.get((shape, _compiler_family(exe)))
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
