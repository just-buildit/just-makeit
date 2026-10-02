"""gh-1700: a byte array takes bytes; gh-1824: a str is numpy's to convert.

Every generated binding acquired an array argument with a bare
``PyArray_FROM_OTF``. numpy reads a ``str`` or a ``bytes`` as ONE scalar of a
text dtype and then casts that scalar to the requested number type, so:

* ``Fld("0101")`` reached ``create()`` as a one-element array holding 101, no
  error -- and ``"1.5"`` into a ``float[]`` became ``[1.5]``;
* ``Fld(b"\\x01\\x00")`` was refused with ``invalid literal for int()``,
  although ``bytes`` is the one Python type that already is a byte buffer.

The fix is one C helper, ``jm_array_arg`` (``_coerce.ARRAY_ARG_C``), emitted
into every extension translation unit and called by every generator that
acquires an array argument: for ``uint8_t[]`` / ``int8_t[]`` a byte buffer is
its bytes, one element per byte.

gh-1700 also refused a ``str`` (and a ``bytes`` into a wider element type) on
every array argument. gh-1824 made that refusal opt-in per parameter, through
``str_hint`` (gh-1756, tested there): a parameter declaring none hands a
``str`` to numpy, so ``"0101"`` into a ``uint8_t[]`` is the one element 101,
exactly ``np.asarray("0101", dtype=np.uint8)``. What numpy does is measured in
the same process as the call, not assumed here.

The central test BUILDS a project with each parameter kind -- a required and
a defaulted init-param, a method param, a module function param, and the
``steps()`` input -- and calls it. ``test_no_generator_calls_from_otf`` is
the registration-free half: a new generator that acquires an argument with a
bare ``PyArray_FROM_OTF`` fails it, which is how a fix in four places misses
the fifth.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from _jmrun import run_cli  # noqa: E402
from just_makeit import _capsule, _coerce, _composer, _handle  # noqa: E402
from just_makeit import _types as T  # noqa: E402
from test_capsule_codegen import _cfg as _capsule_cfg  # noqa: E402
from test_composer_codegen import (  # noqa: E402
    _complex_cfg as _composer_complex_cfg,
)

_SRC = Path(__file__).parent.parent / "src" / "just_makeit"

_NO_TOOLCHAIN = shutil.which("cmake") is None or (
    shutil.which("cc") is None and shutil.which("gcc") is None
)


# -- the source: no generator converts an argument around the helper ---------

# The one file allowed to spell the call: the helper's own definition.
_ALLOWED = {"_coerce.py"}


def test_no_generator_calls_from_otf():
    found: dict[str, int] = {}
    for path in sorted(_SRC.rglob("*")):
        rel = path.relative_to(_SRC)
        if not path.is_file() or rel.parts[0] == "examples":
            continue
        if path.suffix not in (".py", ".c", ".h"):
            continue
        n = len(re.findall(r"\bPyArray_FROM_OTF\(", path.read_text("utf-8")))
        if n:
            found[str(rel)] = n
    stray = {f: n for f, n in found.items() if f not in _ALLOWED}
    assert not stray, (
        "these generators convert an array argument with a bare "
        f"PyArray_FROM_OTF; use _coerce.array_arg (gh-1700): {stray}"
    )
    # Armed: the scan reads the helper itself, so it is reading the tree.
    assert "_coerce.py" in found


def _handle_ext() -> str:
    cfg = {
        "project": {"name": "p"},
        "module": {
            "hd": {
                "kind": "handle",
                "backing": "b",
                "header": "b/b.h",
                "type_name": "H",
                "close_fn": "b_close",
                "create_fn": "b_open",
                "create_args": [],
                "methods": [
                    {
                        "name": "send",
                        "fn": "b_send",
                        "returns": "size_t",
                        "args": [{"name": "iq", "type": "uint8_t[]"}],
                    }
                ],
            }
        },
    }
    return _handle.render_ext(cfg, "hd")


def _capsule_ext() -> str:
    return _capsule.render_ext(_capsule_cfg(), "ddc_fn")


def _composer_ext() -> str:
    return _composer.render_ext(_composer_complex_cfg(), "wfm_compose")


@pytest.mark.parametrize(
    "ext",
    [_handle_ext, _capsule_ext, _composer_ext],
    ids=["handle", "capsule", "composer"],
)
def test_a_kind_module_defines_the_helper_before_calling_it(ext):
    """The kinds with a hand-built preamble, which no build test reaches."""
    text = ext()
    call = re.search(rf"\b{_coerce.ARRAY_ARG_FN}\(\w+,", text)
    assert call, "the array argument is not acquired through the helper"
    assert 0 <= text.find(_coerce.ARRAY_ARG_C) < call.start()


# -- the stubs: a byte array says it takes bytes, in both generators ---------


@pytest.mark.parametrize("ct", ["uint8_t[]", "int8_t[]"])
def test_a_byte_array_param_admits_byte_buffers(ct):
    ann = T.py_param_annotation("NDArray[np.x]", ct, "")
    assert ann.endswith("| bytes | bytearray | memoryview"), ann


@pytest.mark.parametrize("ct", ["float[]", "uint16_t[]", "uint8_t[][]"])
def test_other_arrays_are_unchanged(ct):
    assert T.py_param_annotation("NDArray[np.x]", ct, "") == "NDArray[np.x]"


# -- the build: every parameter kind, called --------------------------------

_ENC = r"""
#include <stddef.h>
#include <stdint.h>
#define ENC_BODY(p, n) \
    int64_t r = (int64_t)(n); \
    for (size_t i = 0; i < (n); i++) r = r * 1000 + (int64_t)(p)[i] + 500; \
    return r;
static int64_t enc_u8(const uint8_t *p, size_t n) { ENC_BODY(p, n) }
static int64_t enc_i8(const int8_t *p, size_t n) { ENC_BODY(p, n) }
static int64_t enc_f(const float *p, size_t n) { ENC_BODY(p, n) }
static int64_t g_last[3];
"""


def _enc(seq) -> int:
    """The Python half of the C ``ENC_BODY``: length, then each element."""
    r = len(seq)
    for v in seq:
        r = r * 1000 + int(v) + 500
    return r


def _jm(*args, cwd):
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, f"jm {' '.join(args)}\n{r.stdout}\n{r.stderr}"


def _sub(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, (path, old)
    path.write_text(text.replace(old, new), encoding="utf-8")


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("gh1700")
    _jm("new", "jmp", cwd=root)
    p = root / "jmp"
    for args in (
        (
            "object", "fld", "--no-state", "--no-step",
            "--arg-type", "void", "--return-type", "void",
            "--init-param", "taps:float[]",
            "--init-param", "bits:uint8_t[]:[]",
            "--init-param", "sbits:int8_t[]:[]",
        ),
        ("method", "fld", "peek", "--param", "b:uint8_t[]",
         "--return-type", "int64_t"),
        ("method", "fld", "last", "--param", "which:int",
         "--return-type", "int64_t"),
        ("object", "acc", "--arg-type", "uint8_t", "--return-type", "uint8_t"),
        ("module", "m"),
        ("function", "peekb", "--module", "m", "--param", "b:int8_t[]",
         "--return-type", "int64_t"),
    ):  # fmt: skip
        _jm(*args, cwd=p)

    core = p / "native" / "src" / "fld" / "fld_core.c"
    _sub(core, "#include", _ENC + "#include")
    _sub(
        core,
        "/* <<IMPLEMENT: initialise state >> */",
        "g_last[0] = enc_f(taps, taps_len);"
        " g_last[1] = enc_u8(bits, bits_len);"
        " g_last[2] = enc_i8(sbits, sbits_len);",
    )
    _sub(
        core,
        "(void)state; (void)b; (void)b_len;\n    return (int64_t)0;",
        "(void)state; return enc_u8(b, b_len);",
    )
    _sub(
        core,
        "(void)state; (void)which;\n    return (int64_t)0;",
        "(void)state; return g_last[which];",
    )
    fn = p / "native" / "src" / "m" / "peekb.c"
    _sub(fn, "#include", _ENC + "#include")
    _sub(
        fn,
        "(void)b; (void)b_len;\n    return (int64_t)0; /* placeholder */",
        "return enc_i8(b, b_len);",
    )
    build = subprocess.run(["make"], cwd=p, capture_output=True, text=True)
    assert build.returncode == 0, build.stdout[-3000:] + build.stderr[-3000:]
    return p


_CASES = r"""
import json, sys
import numpy as np
from jmp import Fld, Acc
from jmp.m import peekb

T = [1.0, 2.0]
cases = {
    "init bits list": lambda: Fld(T, bits=[1, 0, 1]),
    "init bits bytes": lambda: Fld(T, bits=b"\x01\x00\xff"),
    "init bits bytearray": lambda: Fld(T, bits=bytearray(b"\x01\x00")),
    "init bits memoryview": lambda: Fld(T, bits=memoryview(b"\x07\x08")),
    "init bits str": lambda: Fld(T, bits="0101"),
    "init bits text": lambda: Fld(T, bits="pn:7:3"),
    "init sbits bytes": lambda: Fld(T, sbits=b"\x01\xff"),
    "init sbits str": lambda: Fld(T, sbits="12"),
    "init bits overflow": lambda: Fld(T, bits="300"),
    "init taps str": lambda: Fld("1.5"),
    "init taps bytes": lambda: Fld(b"1.5"),
    "init taps list": lambda: Fld([1.5, 2.5]),
    "method list": lambda: Fld(T).peek([3, 4]),
    "method bytes": lambda: Fld(T).peek(b"\x03\x04"),
    "method memoryview": lambda: Fld(T).peek(memoryview(bytearray(b"\x05"))),
    "method str": lambda: Fld(T).peek("34"),
    "function list": lambda: peekb([-1, 2]),
    "function bytes": lambda: peekb(b"\xff\x02"),
    "function bytearray": lambda: peekb(bytearray(b"\x80")),
    "function str": lambda: peekb("12"),
    "steps list": lambda: Acc().steps([1, 2, 3]).shape,
    "steps bytes": lambda: Acc().steps(b"\x01\x02\x03").shape,
    "steps str": lambda: Acc().steps("123"),
}
# gh-1824: what numpy itself makes of each str (and the wide bytes) case,
# measured here rather than assumed, as (value, dtype).
NUMPY = {
    "init bits str": ("0101", np.uint8),
    "init bits text": ("pn:7:3", np.uint8),
    "init bits overflow": ("300", np.uint8),
    "init sbits str": ("12", np.int8),
    "init taps str": ("1.5", np.float32),
    "init taps bytes": (b"1.5", np.float32),
    "method str": ("34", np.uint8),
    "function str": ("12", np.int8),
    "steps str": ("123", np.uint8),
}
out = {}
for name, (value, dtype) in NUMPY.items():
    try:
        out["numpy " + name] = {
            "ok": np.asarray(value, dtype=dtype).ravel().tolist()
        }
    except Exception as e:
        out["numpy " + name] = {"err": type(e).__name__, "msg": str(e)}
for name, call in cases.items():
    try:
        r = call()
        if isinstance(r, Fld):
            r = [r.last(0), r.last(1), r.last(2)]
        # A result a regression lets through (an ndarray from `steps("123")`)
        # must reach the assertion, not crash this harness.
        if hasattr(r, "tolist"):
            r = r.tolist()
        out[name] = {"ok": r if not isinstance(r, tuple) else list(r)}
    except Exception as e:
        out[name] = {"err": type(e).__name__, "msg": str(e)}
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def results(project) -> dict:
    ext = sysconfig.get_config_var("EXT_SUFFIX")
    so = list(project.rglob(f"fld{ext}"))
    assert so, "extension module was not built"
    env_path = str(so[0].parent.parent)
    run = subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {env_path!r})"
         + "\n" + _CASES],
        cwd=project,
        capture_output=True,
        text=True,
    )  # fmt: skip
    assert run.returncode == 0, run.stderr[-3000:]
    return json.loads(run.stdout.strip().splitlines()[-1])


_TAPS = _enc([1, 2])


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="needs cmake and a C compiler")
class TestEveryParamKind:
    """The asked behaviour, per parameter kind, from the running extension."""

    @pytest.mark.parametrize(
        "case, reached",
        [
            ("init bits str", lambda v: [_TAPS, _enc(v), _enc([])]),
            ("init sbits str", lambda v: [_TAPS, _enc([]), _enc(v)]),
            ("init taps str", lambda v: [_enc(v), _enc([]), _enc([])]),
            # gh-1824: no `str_hint`, so a bytes into a float[] is numpy's
            # too -- parsed as text, as numpy parses it.
            ("init taps bytes", lambda v: [_enc(v), _enc([]), _enc([])]),
            ("method str", _enc),
            ("function str", _enc),
            # The scaffold's step() passes each sample through.
            ("steps str", lambda v: v),
        ],
    )
    def test_a_str_without_a_hint_is_what_numpy_makes_it(
        self, results, case, reached
    ):
        """gh-1824: no blanket refusal; the param gets numpy's conversion."""
        ref = results["numpy " + case]
        assert "ok" in ref, ref  # numpy converts every one of these
        assert results[case] == {"ok": reached(ref["ok"])}

    @pytest.mark.parametrize("case", ["init bits text", "init bits overflow"])
    def test_numpy_refusing_a_str_is_the_refusal(self, results, case):
        """Where numpy refuses, the binding raises numpy's own error."""
        ref = results["numpy " + case]
        assert "err" in ref, ref
        assert results[case] == ref

    @pytest.mark.parametrize(
        "case, expect",
        [
            ("init bits list", [_TAPS, _enc([1, 0, 1]), _enc([])]),
            ("init bits bytes", [_TAPS, _enc([1, 0, 255]), _enc([])]),
            ("init bits bytearray", [_TAPS, _enc([1, 0]), _enc([])]),
            ("init bits memoryview", [_TAPS, _enc([7, 8]), _enc([])]),
            # one element per byte: 0xff is -1 in an int8_t[]
            ("init sbits bytes", [_TAPS, _enc([]), _enc([1, -1])]),
            ("init taps list", [_enc([1, 2]), _enc([]), _enc([])]),
            ("method list", _enc([3, 4])),
            ("method bytes", _enc([3, 4])),
            ("method memoryview", _enc([5])),
            ("function list", _enc([-1, 2])),
            ("function bytes", _enc([-1, 2])),
            ("function bytearray", _enc([-128])),
            ("steps list", [3]),
            ("steps bytes", [3]),
        ],
    )
    def test_a_list_or_a_byte_buffer_reaches_c(self, results, case, expect):
        assert results[case] == {"ok": expect}

    def test_the_stubs_say_what_the_binding_takes(self, project):
        # The stub is reflowed to the project's width, so read it as tokens.
        fld = " ".join(
            (project / "src" / "jmp" / "fld.pyi").read_text("utf-8").split()
        )
        assert (
            "def peek( self, b: NDArray[np.uint8] | bytes | bytearray"
            " | memoryview, ) -> int:"
        ) in fld
        m = " ".join(
            (project / "src" / "jmp" / "m" / "m.pyi")
            .read_text("utf-8")
            .split()
        )
        assert (
            "def peekb(b: NDArray[np.int8] | bytes | bytearray | memoryview)"
        ) in m

    def test_the_array_init_stub_imports_npt(self, project):
        """``npt.ArrayLike`` was written with no ``import numpy.typing``.

        Found building this fixture: every object with an array init-param
        got a stub naming ``npt`` it never imported, so the stub failed mypy.
        """
        fld = (project / "src" / "jmp" / "fld.pyi").read_text("utf-8")
        assert "npt.ArrayLike" in fld
        assert "\nimport numpy.typing as npt\n" in fld
