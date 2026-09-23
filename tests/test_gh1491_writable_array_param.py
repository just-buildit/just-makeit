"""gh-1491: a writable array param is the caller's buffer, on every face.

The issue named a module function: a writable ``out`` array marshalled with
``PyArray_FROM_OTF`` and no dtype check, so a wrong-dtype or strided array
became a temporary the C function filled and the binding discarded. That
path was already guarded (gh-581, ``_coerce.out_buffer_guard``) and is pinned
below at runtime. The rest of the CLASS was not:

- A METHOD param accepts ``out = true`` (and its synonym ``mutable``) in the
  manifest (``_keys.PARAM_KEYS``), but only one of the two binding builders
  read it. On a ``variable_output`` method with params the key was dropped
  outright -- the array was marshalled read-only, with no guard, and cast to
  ``const`` -- and every prototype said ``const`` regardless, because
  ``_types.c_param_parts`` (and two hand-rolled copies of it) never asked.
  The kernel could not write the buffer it was declared to write.
- ``jm method`` had no flag that could say it, so the shape existed only by
  hand-editing the manifest.
- ``jm script`` replayed every param -- method AND function -- as
  ``--param``, so a replayed project came back with the caller's buffer
  const and read-only: exit 0, a different project.

One predicate, ``_types.param_writable``, now answers "is this the caller's
buffer?" for the prototype, both binding builders, the module-function
builder and the script.

GATE: a manifest key a method shape accepts is honoured in the generated
      binding, its stub, and a replayed script -- or it is refused.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli

_NO_TOOLCHAIN = shutil.which("cmake") is None or (
    shutil.which("cc") is None and shutil.which("gcc") is None
)

# The guard's first line, and the message `out_buffer_guard` emits for a
# param called `buf`. Anchored on the param's own object variable, so a guard
# emitted for some other argument cannot satisfy it.
_GUARD = "if (!PyArray_Check(buf_obj) ||"
_MSG = '"buf must be a writable, C-contiguous"'


def _project(tmp_path: Path) -> Path:
    """One module holding every shape that can declare a writable param.

    ``plain``  -- a method with params (the `_parse` binding builder)
    ``vo``     -- a variable_output method with params (its own builder)
    ``fill``   -- a module function (`_render`'s builder; gh-1491 as filed)

    Each takes ``x`` (input) and ``buf`` (the caller's buffer), declared
    through the CLI, so the flag is part of what is under test.
    """
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    proj = tmp_path / "p"
    for args in (
        ("module", "m"),
        ("object", "w", "--module", "m", "--no-step"),
        (
            "method", "w", "plain", "--module", "m",
            "--param", "x:float[]", "--out-param", "buf:float[]",
            "--return-type", "size_t",
        ),
        (
            "method", "w", "vo", "--module", "m",
            "--param", "x:float[]", "--out-param", "buf:float[]",
            "--return-type", "float", "--variable-output",
        ),
        (
            "function", "fill", "--module", "m",
            "--param", "x:float[]", "--out-param", "buf:float[]",
            "--return-type", "void",
        ),
    ):  # fmt: skip
        r = run_cli(*args, cwd=proj)
        assert r.returncode == 0, f"{args}:\n{r.stdout}\n{r.stderr}"
    return proj


def _fn_text(src: str, head: str) -> str:
    """The one C function whose definition starts with *head*.

    From its first line to the first ``}`` in column 0. Every assertion reads
    one function only: the module holds three writable ``buf`` params, and a
    whole-file check would pass on a sibling's guard.
    """
    m = re.search(rf"^{re.escape(head)}.*?^}}", src, re.S | re.M)
    assert m, f"no definition starting {head!r}"
    return m.group(0)


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    return _project(tmp_path_factory.mktemp("gh1491"))


class TestTheCliRecordsIt:
    def test_out_param_reaches_the_manifest(self, project):
        frag = (project / "objects" / "w.toml").read_text(encoding="utf-8")
        # Both methods' `buf`, and nothing else, is writable.
        assert frag.count("out = true") == 2, frag

    def test_out_param_refuses_a_scalar(self, project):
        r = run_cli(
            "method", "w", "bad", "--module", "m",
            "--out-param", "n:int", "--return-type", "int",
            cwd=project,
        )  # fmt: skip
        assert r.returncode != 0
        assert "must be an array type" in r.stderr


class TestThePrototypeCanWrite:
    """The C the author writes against must not say ``const``."""

    @pytest.mark.parametrize(
        "decl",
        [
            "size_t w_plain(w_state_t *state, const float *x, size_t x_len,"
            " float *buf, size_t buf_len);",
            "size_t w_vo(w_state_t *state, const float *x, size_t x_len,"
            " float *buf, size_t buf_len, float *out);",
        ],
    )
    def test_header(self, project, decl):
        hdr = (project / "native/inc/w/w_core.h").read_text(encoding="utf-8")
        assert decl in hdr

    @pytest.mark.parametrize(
        "head", ["w_plain(w_state_t *state", "w_vo(w_state_t *state"]
    )
    def test_core_c_stub(self, project, head):
        core = (project / "native/src/w/w_core.c").read_text(encoding="utf-8")
        line = _fn_text(core, head).splitlines()[0]
        assert "const float *x" in line
        assert "float *buf" in line and "const float *buf" not in line


class TestTheBindingHandsOverTheCallersArray:
    """Guarded, marshalled WRITEABLE, and passed without ``const``."""

    @pytest.mark.parametrize(
        "path, head",
        [
            ("native/src/m/m_ext_w.c", "W_plain(WObject *self"),
            ("native/src/m/m_ext_w.c", "W_vo(WObject *self"),
            ("native/src/m/m_ext.c", "_bind_fill(PyObject *self"),
        ],
    )
    def test_guard_precedes_a_writeable_marshal(self, project, path, head):
        fn = _fn_text((project / path).read_text(encoding="utf-8"), head)
        assert _GUARD in fn and _MSG in fn, fn
        marshal = re.search(
            r"PyArray_FROM_OTF\(\s*buf_obj,\s*NPY_FLOAT,\s*([^)]*)\)", fn
        )
        assert marshal, fn
        assert "NPY_ARRAY_WRITEABLE" in marshal.group(1), fn
        assert fn.index(_GUARD) < marshal.start()
        assert "(const float *)PyArray_DATA(buf_arr)" not in fn

    def test_the_guard_releases_the_input_it_already_holds(self, project):
        fn = _fn_text(
            (project / "native/src/m/m_ext_w.c").read_text(encoding="utf-8"),
            "W_vo(WObject *self",
        )
        guard = fn[fn.index(_GUARD) :]
        guard = guard[: guard.index("return NULL;")]
        assert "Py_DECREF(x_arr);" in guard


class TestTheScriptReplaysIt:
    """A replayed project must declare the same buffer writable."""

    def test_every_writable_param_replays_as_out_param(self, project):
        r = run_cli("script", cwd=project)
        assert r.returncode == 0, r.stderr
        # plain, vo and fill each declare one.
        assert r.stdout.count('--out-param "buf:float[]"') == 3, r.stdout
        assert '--param "buf:float[]"' not in r.stdout

    def test_mutable_is_the_same_key(self, tmp_path):
        """``mutable = true`` is `out`'s synonym (gh-170) on every face.

        Declared in the manifest from the start, so no face can pass by
        reading an earlier ``out = true`` render: the per-object binding
        fragment is not rewritten by a later `apply` (gh-1448), and a test
        that flipped the key after scaffolding asserted on stale text.
        """
        assert run_cli("new", "p", cwd=tmp_path).returncode == 0
        proj = tmp_path / "p"
        for args in (
            ("module", "m"),
            ("object", "w", "--module", "m", "--no-step"),
        ):
            assert run_cli(*args, cwd=proj).returncode == 0
        frag = proj / "objects" / "w.toml"
        tables = "".join(
            f'\n[[w.methods]]\nname = "{name}"\nreturn_type = "{rt}"\n{extra}'
            '\n[[w.methods.params]]\nname = "x"\ntype = "float[]"\n'
            '\n[[w.methods.params]]\nname = "buf"\ntype = "float[]"\n'
            "mutable = true\n"
            for name, rt, extra in (
                ("plain", "size_t", ""),
                ("vo", "float", "variable_output = true\n"),
            )
        )
        frag.write_text(
            frag.read_text(encoding="utf-8") + tables, encoding="utf-8"
        )
        r = run_cli("apply", cwd=proj)
        assert r.returncode == 0, r.stdout + r.stderr

        hdr = (proj / "native/inc/w/w_core.h").read_text(encoding="utf-8")
        assert hdr.count("float *buf, size_t buf_len") == 2, hdr
        assert "const float *buf" not in hdr
        src = (proj / "native/src/m/m_ext_w.c").read_text(encoding="utf-8")
        for head in ("W_plain(WObject *self", "W_vo(WObject *self"):
            assert _GUARD in _fn_text(src, head), head
        r = run_cli("script", cwd=proj)
        assert r.stdout.count('--out-param "buf:float[]"') == 2, r.stdout


def _set_body(path: Path, head: str, body: str) -> None:
    """Replace the body of the function whose definition starts *head*."""
    src = path.read_text(encoding="utf-8")
    fn = _fn_text(src, head)
    sig = fn[: fn.index("{")]
    path.write_text(
        src.replace(fn, f"{sig}{{\n{body}\n}}", 1), encoding="utf-8"
    )


# buf[i] = x[i] + 1 over the shorter of the two: observable, so "was the
# caller's buffer written?" differs from "was it left alone?".
_KERNEL = (
    "    size_t n = x_len < buf_len ? x_len : buf_len;\n"
    "    for (size_t i = 0; i < n; i++) buf[i] = x[i] + 1.0f;"
)

_PROBE = r"""
import numpy as np
from p.m import W, fill

w = W()
calls = {
    "fill": lambda x, b: fill(x, b),
    "plain": lambda x, b: w.plain(x, b),
    "vo": lambda x, b: w.vo(x, b),
}
x = np.arange(4, dtype=np.float32)
bad = []
for name, call in calls.items():
    # The control: the caller's own array is written in place.
    b = np.zeros(4, dtype=np.float32)
    call(x, b)
    if not np.array_equal(b, x + 1):
        bad.append(f"{name}: the caller's own array was not written: {b!r}")

    # Each way numpy would otherwise substitute a temporary. The first three
    # are the SILENT ones: a safe upcast, a strided view and a read-only
    # array all marshal without complaint when unguarded, and the kernel's
    # writes land somewhere the caller never sees (or in read-only memory).
    # float64 -> float32 is refused by numpy's own `safe` rule, so it only
    # pins which message the caller gets.
    big = np.zeros((4, 2), dtype=np.float32)
    read_only = np.zeros(4, dtype=np.float32)
    read_only.flags.writeable = False
    for label, b in (
        ("float16", np.zeros(4, dtype=np.float16)),
        ("strided", big[:, 0]),
        ("read-only", read_only),
        ("float64", np.zeros(4, dtype=np.float64)),
        ("list", [0.0] * 4),
    ):
        try:
            call(x, b)
        except TypeError as e:
            if "buf must be a writable, C-contiguous" not in str(e):
                bad.append(f"{name}/{label}: wrong refusal: {e}")
        else:
            bad.append(f"{name}/{label}: accepted, buffer left {b!r}")
assert not bad, "\n".join(bad)
print("ok")
"""


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_the_kernel_writes_the_callers_array_or_the_call_refuses(tmp_path):
    """Compiled proof: the defect is invisible in the generated text.

    An unguarded marshal reads as correct C and returns normally; only a
    caller who reads their own buffer afterwards finds it untouched.
    """
    proj = _project(tmp_path)
    _set_body(proj / "native/src/m/fill.c", "fill(", _KERNEL)
    core = proj / "native/src/w/w_core.c"
    _set_body(core, "w_plain(w_state_t *state", _KERNEL + "\n    return n;")
    _set_body(
        core,
        "w_vo(w_state_t *state",
        "    (void)state; (void)out;\n" + _KERNEL + "\n    return 0;",
    )

    build = proj / "build"
    for cmd in (
        [
            "cmake",
            "-S",
            str(proj),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        ["cmake", "--build", str(build), "--parallel", "4"],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, f"{cmd[:2]}:\n{r.stdout}\n{r.stderr}"

    r = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=proj,
        env={**os.environ, "PYTHONPATH": str(proj / "src")},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    assert r.stdout.strip() == "ok"
