"""An output the kernel fills blind needs a length the call carries (gh-1888).

``jm function ramp --param n:int --out-type float`` wrote
``void ramp(float *out, int n)`` and a binding that allocated ONE element:
``jm --help`` and ``docs/types.md`` promised "the first integer scalar
param", which the function face never read. A body filling the ``n`` it was
called with wrote past the buffer -- a heap overflow in generated code, which
AddressSanitizer reports at the body's first write past element 0.

Every face whose kernel is handed a bare ``T *out`` and never its length now
sizes it by one rule, ``_outbuf.length``:

* a module function's ``out_type`` -- fixed, variable-output and ``str`` --
  reads its ``out_size``, the integer param a ``T[n]`` names (now spelled on
  the command line too, and honoured on a variable output, where it was
  ignored), or the first array param's length. With none it is REFUSED
  (``_outbuf.length_why_not``): by ``jm function`` before anything is
  written, and by ``apply`` and ``status``, whose replay goes through it;
* a method's fixed ``out_type`` reads the first array the call parses -- an
  array ``arg_type`` input among them, which it skipped, allocating nothing
  -- else the first integer param (gh-65).

This file proves it two ways:

* **the build**: one project holding every accepted shape, each body writing
  exactly the length its declaration names, built once -- under
  AddressSanitizer where the toolchain has one -- and every result read back
  whole, so an allocation short of the write is a sanitizer report and one
  past it is a wrong length;
* **the refusals**: every function shape that names no length, on the
  command line and as a manifest row under ``apply``, ``status`` and
  ``status --check``, each leaving the tree byte-identical.

GATE: a module function's allocated output is sized from a length its call
carries, or the declaration is refused before anything is written.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc, find_compiler
from _jmrun import run_cli

from just_makeit import _config as C
from just_makeit import _textio


def _skip_reason() -> "str | None":
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    try:
        import numpy  # noqa: F401
    except ImportError:
        return "numpy not importable"
    return None


_SKIP = _skip_reason()


def _run_ok(root: Path, *args: str) -> None:
    r = run_cli(*args, cwd=root)
    assert r.returncode == 0, (args, r.stdout + r.stderr)


def _asan() -> "tuple[str, str] | None":
    """``(gcc, its libasan)`` when this box can sanitize a Python extension.

    Linux only: the runtime is preloaded into the interpreter, which only
    ``LD_PRELOAD`` does without rebuilding Python. Elsewhere the build is
    plain and the read-back below is the whole check -- every shape is
    still built and called.
    """
    if not sys.platform.startswith("linux"):
        return None
    gcc = find_compiler("gcc")
    if gcc is None:
        return None
    lib = subprocess.run(
        [gcc, "-print-file-name=libasan.so"], capture_output=True, text=True
    ).stdout.strip()
    return (gcc, lib) if os.path.isabs(lib) and os.path.exists(lib) else None


# -- the build ----------------------------------------------------------------

#: Every accepted shape, its body writing EXACTLY the length its declaration
#: names -- so the allocation must be that length, no shorter (a sanitizer
#: report, or a crash) and no longer (a wrong length read back). None reads
#: past what it is told: `head` ignores its array, whose length is not the
#: output's.
_FUNCTIONS = [
    # the issue's function, naming its length
    {
        "name": "ramp",
        "out_type": "float[n]",
        "params": [{"name": "n", "type": "int"}],
        "impl": "for (int i = 0; i < n; i++) out[i] = (float)i;",
    },
    # an array sizes it
    {
        "name": "twice",
        "out_type": "float",
        "params": [{"name": "x", "type": "float[]"}],
        "impl": "for (size_t i = 0; i < x_len; i++) out[i] = 2.0f * x[i];",
    },
    # a named length wins over the array
    {
        "name": "head",
        "out_type": "float[n]",
        "params": [
            {"name": "x", "type": "float[]"},
            {"name": "n", "type": "size_t"},
        ],
        "impl": "(void)x; (void)x_len;\n"
        "    for (size_t i = 0; i < n; i++) out[i] = (float)i;",
    },
    # a variable output's declared capacity
    {
        "name": "fill",
        "out_type": "float",
        "variable_output": True,
        "out_size": "n",
        "return_type": "size_t",
        "params": [{"name": "n", "type": "int"}],
        "impl": "for (int i = 0; i < n; i++) out[i] = (float)i;\n"
        "    return (size_t)n;",
    },
    # ...and its named length, which the variable output used to ignore
    {
        "name": "fill_named",
        "out_type": "float[n]",
        "variable_output": True,
        "return_type": "size_t",
        "params": [{"name": "n", "type": "int"}],
        "impl": "for (int i = 0; i < n; i++) out[i] = (float)i;\n"
        "    return (size_t)n;",
    },
    # a variable output an array sizes
    {
        "name": "vo_arr",
        "out_type": "float",
        "variable_output": True,
        "return_type": "size_t",
        "params": [{"name": "x", "type": "float[]"}],
        "impl": "for (size_t i = 0; i < x_len; i++) out[i] = x[i] + 1.0f;\n"
        "    return x_len;",
    },
    # a text result's capacity
    {
        "name": "letters",
        "out_type": "str",
        "variable_output": True,
        "out_size": "n",
        "return_type": "size_t",
        "params": [{"name": "n", "type": "int"}],
        "impl": "for (int i = 0; i < n; i++) out[i] = (char)('a' + i);\n"
        "    return (size_t)n;",
    },
]

_METHODS = [
    # gh-65: the requested count
    {
        "name": "gen",
        "arg_type": "void",
        "return_type": "void",
        "out_type": "float",
        "params": [{"name": "n", "type": "uint32_t"}],
        "impl": "(void)state;\n"
        "    for (uint32_t i = 0; i < n; i++) out[i] = (float)i;",
    },
    # an array `arg_type` input, which the sizing skipped: 0 elements
    {
        "name": "scale",
        "arg_type": "float[]",
        "return_type": "void",
        "out_type": "float",
        "impl": "(void)state;\n"
        "    for (size_t i = 0; i < x_len; i++) out[i] = 3.0f * x[i];",
    },
    # ...ahead of an integer param, which sized it instead
    {
        "name": "scale_n",
        "arg_type": "float[]",
        "return_type": "void",
        "out_type": "float",
        "params": [{"name": "k", "type": "int"}],
        "impl": "(void)state;\n"
        "    for (size_t i = 0; i < x_len; i++) out[i] = (float)k * x[i];",
    },
    # an array param, divided
    {
        "name": "pairs",
        "arg_type": "void",
        "return_type": "void",
        "out_type": "float",
        "out_divisor": 2,
        "params": [{"name": "raw", "type": "int8_t[]"}],
        "impl": "(void)state;\n"
        "    for (size_t i = 0; i < raw_len / 2; i++)\n"
        "        out[i] = (float)(raw[2 * i] + raw[2 * i + 1]);",
    },
]

#: (call, what it prints): one per shape above, read back whole.
_CALLS = {
    "ramp": ("m.ramp(5)", [0.0, 1.0, 2.0, 3.0, 4.0]),
    "twice": ("m.twice(np.array([1, 2, 3], np.float32))", [2.0, 4.0, 6.0]),
    "head": ("m.head(np.ones(2, np.float32), 4)", [0.0, 1.0, 2.0, 3.0]),
    "fill": ("m.fill(3)", [0.0, 1.0, 2.0]),
    "fill_named": ("m.fill_named(4)", [0.0, 1.0, 2.0, 3.0]),
    "vo_arr": ("m.vo_arr(np.array([1, 2], np.float32))", [2.0, 3.0]),
    "letters": ("m.letters(5)", "abcde"),
    "gen": ("O().gen(3)", [0.0, 1.0, 2.0]),
    "scale": ("O().scale(np.array([1, 2], np.float32))", [3.0, 6.0]),
    "scale_n": ("O().scale_n(np.array([1, 2], np.float32), 9)", [9.0, 18.0]),
    "pairs": ("O().pairs(np.array([1, 2, 3, 4], np.int8))", [3.0, 7.0]),
}


def _build(base: Path) -> "tuple[Path, dict]":
    """Every shape above, applied from the manifest and built once."""
    _run_ok(base, "new", "p", "--no-c-prefix")
    root = base / "p"
    _run_ok(root, "module", "m")
    _run_ok(root, "object", "o")
    cfg = C.load(root)
    cfg["module"]["m"]["functions"] = _FUNCTIONS
    cfg["o"]["methods"] = _METHODS
    C.save(root, cfg)
    _run_ok(root, "apply")

    asan = _asan()
    flags = (
        [
            f"-DCMAKE_C_COMPILER={asan[0]}",
            "-DCMAKE_C_FLAGS=-fsanitize=address -fno-omit-frame-pointer",
        ]
        if asan
        else []
    )
    build = root / "build"
    for cmd in (
        ["cmake", "-S", str(root), "-B", str(build)]
        + [f"-DPython3_EXECUTABLE={sys.executable}", *flags],
        ["cmake", "--build", str(build)],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        assert r.returncode == 0, f"{cmd[:2]}:\n{r.stdout}\n{r.stderr}"
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    if asan:
        env |= {"LD_PRELOAD": asan[1], "ASAN_OPTIONS": "detect_leaks=0"}
    return root, env


def test_every_shape_is_called():
    """The read-back covers the manifest, so no shape is built uncalled."""
    names = [r["name"] for r in _FUNCTIONS + _METHODS]
    assert sorted(names) == sorted(_CALLS)


#: Calls every shape in ONE interpreter and prints what each returned, or
#: what it raised. A write past an allocation does not return at all under
#: the sanitizer: the process dies with a report naming the body.
_DRIVER = """\
import json
import numpy as np
from p import m
from p.o import O
got = {}
for name, call in json.loads(CALLS).items():
    try:
        y = eval(call)
        got[name] = y if isinstance(y, str) else y.tolist()
    except Exception as e:
        got[name] = f"{type(e).__name__}: {e}"
print(json.dumps(got))
"""


def test_the_allocation_is_the_length_the_body_writes(tmp_path):
    """One project, one build, one interpreter: every shape at once.

    One test rather than one per shape, because a module fixture is built
    once per xdist worker, and the build is the cost (gh-2078).
    """
    import json

    if _SKIP:
        pytest.skip(_SKIP)
    root, env = _build(tmp_path)
    calls = {k: c for k, (c, _) in _CALLS.items()}
    r = subprocess.run(
        [sys.executable, "-c", f"CALLS = {json.dumps(calls)!r}\n" + _DRIVER],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, f"died: {r.stderr.strip()[-2000:]}"
    got = json.loads(r.stdout)
    assert got == {k: want for k, (_, want) in _CALLS.items()}


# -- the refusals --------------------------------------------------------------


@pytest.fixture(scope="module")
def blank(tmp_path_factory) -> Path:
    """A fresh project with one module, built once and copied per case."""
    base = tmp_path_factory.mktemp("gh1888_blank")
    _run_ok(base, "new", "p")
    _run_ok(base / "p", "module", "m")
    return base / "p"


def _copy(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst / "p")
    return dst / "p"


def _snapshot(root: Path) -> "dict[str, bytes]":
    """Every file under *root*, and every directory as an empty marker."""
    return {
        p.relative_to(root).as_posix(): (
            p.read_bytes() if p.is_file() else b"<dir>"
        )
        for p in sorted(root.rglob("*"))
        if "__pycache__" not in p.parts
    }


def _refused(root: Path, fn: str, args: "list[str]", why: str) -> None:
    """*args* exit 1 naming function *fn* and *why*, and write nothing."""
    before = _snapshot(root)
    r = run_cli(*args, cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    # `status` indents the replay's own `error:` line under its header.
    errors = [
        ln.strip()
        for ln in r.stderr.splitlines()
        if ln.strip().startswith("error:")
    ]
    assert any(f"function '{fn}'" in e for e in errors), r.stderr
    # ...and re-wraps it, so the words are compared, not the line breaks.
    assert why in " ".join(r.stderr.split()), r.stderr
    assert _snapshot(root) == before, f"a refused `jm {' '.join(args)}` wrote"


_NO_LENGTH = "but nothing says how long it is"
_NOT_A_COUNT = "is not one of its integer parameters"

#: What a declaration with no length is, one per way it can lack one, on
#: the command line: (args after `jm function f --module m`, the refusal).
CLI = {
    "issue-repro": (
        ["--param", "n:int", "--return-type", "void", "--out-type", "float"],
        _NO_LENGTH,
    ),
    "no-params": (["--out-type", "double"], _NO_LENGTH),
    "variable-output": (
        ["--param", "n:int", "--variable-output", "--out-type", "float"]
        + ["--return-type", "size_t"],
        _NO_LENGTH,
    ),
    "names-nothing": (
        ["--param", "n:int", "--out-type", "float[m]"],
        _NOT_A_COUNT,
    ),
    "names-a-double": (
        ["--param", "g:double", "--out-type", "float[g]"],
        _NOT_A_COUNT,
    ),
    "names-an-array": (
        ["--param", "x:float[]", "--out-type", "float[x]"],
        _NOT_A_COUNT,
    ),
}


@pytest.mark.parametrize("case", sorted(CLI))
def test_the_command_line_refuses_and_writes_nothing(blank, tmp_path, case):
    args, why = CLI[case]
    root = _copy(blank, tmp_path)
    _refused(root, "f", ["function", "f", "--module", "m", *args], why)


def _edit(root: Path, pattern: str, repl: str) -> None:
    """Exactly one match in the manifest's TOML, replaced -- an edit that
    lands nowhere would turn a refusal test into a no-op."""
    hits = []
    for path in sorted(root.rglob("*.toml")):
        text = path.read_text(encoding="utf-8")
        new, n = re.subn(pattern, repl, text, flags=re.MULTILINE)
        if n:
            hits.append((path, new, n))
    assert [n for _, _, n in hits] == [1], (pattern, hits)
    _textio.write_text(hits[0][0], hits[0][1])


_VO = ["--variable-output", "--return-type", "size_t", "--out-size", "n"]

#: How a row an older jm accepted lacks a length: (the valid function it
#: starts as, the edits that take its length away, the refusal).
POISONS = {
    "fixed": (
        ["--param", "n:int", "--out-type", "float[n]"],
        [(r'^out_type = "float\[n\]"$', 'out_type = "float"')],
        _NO_LENGTH,
    ),
    "variable-output": (
        ["--param", "n:int", "--out-type", "float", *_VO],
        [(r'^out_size = "n"\n', "")],
        _NO_LENGTH,
    ),
    "str": (
        ["--param", "n:int", "--out-type", "float", *_VO],
        [
            (r'^out_size = "n"\n', ""),
            (r'^out_type = "float"$', 'out_type = "str"'),
        ],
        _NO_LENGTH,
    ),
    "names-nothing": (
        ["--param", "n:int", "--out-type", "float[n]"],
        [(r'^out_type = "float\[n\]"$', 'out_type = "float[m]"')],
        _NOT_A_COUNT,
    ),
}


@pytest.mark.parametrize("poison", sorted(POISONS))
def test_a_manifest_row_is_refused_by_apply_and_status(
    blank, tmp_path, poison
):
    start, edits, why = POISONS[poison]
    root = _copy(blank, tmp_path)
    _run_ok(root, "function", "f", "--module", "m", *start)
    for pattern, repl in edits:
        _edit(root, pattern, repl)
    for command in (["apply"], ["status"], ["status", "--check"]):
        _refused(root, "f", command, why)


def test_a_command_that_rerenders_the_module_refuses_too(blank, tmp_path):
    """The binding asks as well, for the commands that re-render a module's
    functions without replaying them: adding an object to it, here.

    The tree is not compared: ``jm object`` writes the object before it
    renders the module it joins.
    """
    start, edits, why = POISONS["fixed"]
    root = _copy(blank, tmp_path)
    _run_ok(root, "function", "f", "--module", "m", *start)
    for pattern, repl in edits:
        _edit(root, pattern, repl)
    r = run_cli("object", "z", "--module", "m", cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    assert why in " ".join(r.stderr.split()), r.stderr
