"""A ``variable_output`` kernel whose zero is a real answer can refuse (gh-2012).

The kernel's one return value is the COUNT it wrote, and that count sizes
the result. Its only declared failure was a zero (`error_on_empty`, gh-1159),
which is unsound for a kernel that legitimately produces nothing: doppler's
resampler emits 0 outputs for a third of valid one-sample calls. So it had no
way to refuse a call. `error_negative` was refused on the shape, and a
``(size_t)-1`` came back from the overflow guard as::

    RuntimeError: Blk.interleave: wrote 18446744073709551615 elements into a
    buffer of 3

Two opt-in forms, by the C library's own convention, both raising `error`
with `error_message`:

A. ``count_type = "int64_t"`` (any signed int) with ``error_negative``: the
   kernel returns the count or a negative code, raised through
   `_diagnostics._rc_raise_c` with ``(rc=N)`` appended.
B. ``error_sentinel = "SIZE_MAX"`` (verbatim C): a ``size_t`` count's
   refusal value, raised through `_diagnostics.empty_raise_c`.

What carries this file:

1. **Compiled, both forms, both call paths.** ``0`` is an empty array and no
   raise, a count is the array, the refusal value is the declared exception
   and text -- on the ``out=`` path (which holds ``out_arr``) and the
   allocate path (which holds ``arr0``), the gh-1159 lesson. Composed with
   ``error_on_empty`` / ``none_on_empty``, zero is still read independently.
2. **Placement.** The test runs BEFORE ``_coerce.returned_count_c``: placed
   after it, every refusal is the overflow guard's ``RuntimeError`` again.
3. **Every face agrees on the count's type** -- the ``_core.h`` declaration,
   the ``_core.c`` stub and the binding's local.
4. **Refusals.** Every combination that could not take effect, through the
   CLI and through ``apply`` (one ``error:`` line), from the one predicate.
5. **Plumbing.** The manifest, ``apply`` and ``jm script`` carry both keys;
   the docstring and ``.pyi`` document each condition.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc

from _jmrun import run_cli
from just_makeit import _method
from just_makeit._context import _diagnostics as D


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

#: The kernels, lifted by `--impl`. Each copies its input and returns the
#: count -- except at n_in == 3, where it REFUSES with its declared value.
#: n_in == 0 returns 0, an ordinary empty answer.
KERNELS = """\
#include <stddef.h>
#include <stdint.h>

int64_t signed_k(void *state, const float *in, size_t n_in, float *out)
{
    (void)state;
    if (n_in == 3)
        return -3;
    for (size_t i = 0; i < n_in; i++)
        out[i] = in[i];
    return (int64_t)n_in;
}

size_t sentinel_k(void *state, const float *in, size_t n_in, float *out)
{
    (void)state;
    if (n_in == 3)
        return SIZE_MAX;
    for (size_t i = 0; i < n_in; i++)
        out[i] = in[i];
    return n_in;
}

size_t multi_k(void *state, const float *in, size_t n_in, float *out,
               int32_t *out1)
{
    (void)state;
    if (n_in == 3)
        return SIZE_MAX;
    for (size_t i = 0; i < n_in; i++) {
        out[i] = in[i];
        out1[i] = (int32_t)i;
    }
    return n_in;
}
"""

#: (method, kernel, extra flags). `negz` / `senn` are the same two forms
#: composed with the zero readings -- and `negz` with `--nogil`, whose
#: kernel call declares the signed local outside the released block.
METHODS = [
    ("neg", "signed_k", ["--count-type", "int64_t", "--error-negative"]),
    ("sen", "sentinel_k", ["--error-sentinel", "SIZE_MAX"]),
    (
        "negz",
        "signed_k",
        [
            "--count-type", "int32_t", "--error-negative", "--error-on-empty",
            "--nogil",
        ],
    ),
    ("senn", "sentinel_k", ["--error-sentinel", "(size_t)-1", "--none-on-empty"]),
]  # fmt: skip
#: A second output: no `out=` path, and an allocate path holding TWO arrays.
MULTI = (
    "multi",
    "multi_k",
    ["--error-sentinel", "SIZE_MAX", "--multi-output", "int32_t"],
)
ERROR = {"neg": "ValueError", "sen": "OSError", "negz": "ValueError"}
ERROR |= {"senn": "OSError", "multi": "IndexError"}


def _msg(name: str) -> str:
    return f"{name}: not a whole number of blocks"


def _ok(*args, cwd):
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout + r.stderr)
    return r


def _scaffold(where: Path) -> Path:
    """One object, one method per (form, composition), scaffolded by the CLI
    with each new flag -- so the flags, not only the keys, are exercised."""
    _ok("new", "p", cwd=where)
    root = where / "p"
    (root / "kernels.c").write_text(KERNELS, encoding="utf-8")
    _ok(
        "object",
        "blk",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        cwd=root,
    )
    for name, kernel, flags in [*METHODS, MULTI]:
        _ok(
            "method", "blk", name,
            "--arg-type", "float", "--return-type", "float",
            "--variable-output", *flags,
            "--error", ERROR[name], "--error-message", _msg(name),
            "--impl", f"kernels.c::{kernel}",
            cwd=root,
        )  # fmt: skip
    return root


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    if _SKIP:
        pytest.skip(_SKIP)
    root = _scaffold(tmp_path_factory.mktemp("gh2012"))
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
        ["cmake", "--build", str(build)],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, f"{cmd[:2]}:\n{r.stdout}\n{r.stderr}"
    return root


@pytest.fixture(scope="module")
def scaffolded(tmp_path_factory) -> Path:
    """The same tree, uncompiled, for the text and plumbing tests."""
    return _scaffold(tmp_path_factory.mktemp("gh2012src"))


def _py(root: Path, body: str) -> str:
    """Run *body* against the BUILT extension; its stdout, stripped."""
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys\nimport numpy as np\nfrom p import Blk\nb = Blk()\n"
            + body,
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    return r.stdout.strip()


def _outcome(root: Path, call: str) -> str:
    """``LEN <n> <values>``, ``NONE``, or ``ERR <type>: <msg>``."""
    return _py(
        root,
        "try:\n"
        f"    r = {call}\n"
        "except Exception as e:\n"
        "    print('ERR', type(e).__name__ + ':', e)\n"
        "else:\n"
        "    print('NONE' if r is None else "
        "f'LEN {len(r)} {[float(v) for v in r]}')\n",
    )


#: The two call paths. `{n}` is the input length; `out=` has room for 8.
PATHS = {
    "allocate": "b.{m}(np.arange({n}, dtype=np.float32))",
    "out": "b.{m}(np.arange({n}, dtype=np.float32), out=np.zeros(8, np.float32))",
}
CASES = [(m, p) for m, _k, _f in METHODS for p in PATHS]
IDS = [f"{m}-{p}" for m, p in CASES]


# -- 1. compiled: both forms, both paths --------------------------------------


@pytest.mark.parametrize("method, path", CASES, ids=IDS)
def test_the_refusal_value_raises_the_declared_error(built, method, path):
    """The bug: this was `RuntimeError: ... wrote 18446744073709551615
    elements into a buffer of N`, from the overflow guard."""
    got = _outcome(built, PATHS[path].format(m=method, n=3))
    rc = " (rc=-3)" if method.startswith("neg") else ""
    assert got == f"ERR {ERROR[method]}: {_msg(method)}{rc}", got


@pytest.mark.parametrize("method, path", CASES, ids=IDS)
def test_a_count_is_the_array(built, method, path):
    got = _outcome(built, PATHS[path].format(m=method, n=4))
    assert got == "LEN 4 [0.0, 1.0, 2.0, 3.0]", got


@pytest.mark.parametrize("method, path", CASES, ids=IDS)
def test_zero_is_read_only_by_the_zero_keys(built, method, path):
    """The point of both forms: 0 is an ordinary answer. Composed with a
    zero reading, that reading still answers -- independently."""
    got = _outcome(built, PATHS[path].format(m=method, n=0))
    want = {
        "neg": "LEN 0 []",
        "sen": "LEN 0 []",
        # error_on_empty: the same declared pair, and no code to append.
        "negz": f"ERR ValueError: {_msg('negz')}",
        "senn": "NONE",
    }[method]
    assert got == want, got


def test_a_second_output_is_released_and_refused(built):
    """`--multi-output` has no `out=` path, and its allocate path holds
    `arr0` AND `arr1` -- both released before the raise."""
    call = "b.multi(np.arange({n}, dtype=np.float32))"
    assert _outcome(built, call.format(n=3)) == (
        f"ERR IndexError: {_msg('multi')}"
    )
    got = _py(built, f"print([len(a) for a in {call.format(n=4)}])")
    assert got == "[4, 4]", got
    body = _wrapper(_ext(built), "multi")
    test = body.index("if (n_out == (SIZE_MAX)) {")
    guard = body.index("if ((size_t)(n_out) > (size_t)(_cap))", test)
    assert "Py_DECREF(arr0); Py_DECREF(arr1);" in body[test:guard], body


@pytest.mark.parametrize("method", ["neg", "sen", "multi"])
def test_a_refused_allocate_call_frees_what_it_allocated(built, method):
    """The allocate path made its arrays before the kernel ran; a refusal
    that forgot them leaks an array per call. Measured by tracemalloc,
    which numpy reports its buffers to: 5000 refusals leaking even the
    smallest array would hold hundreds of kB."""
    got = _py(
        built,
        "import gc, tracemalloc\n"
        "x = np.arange(3, dtype=np.float32)\n"
        f"f = b.{method}\n"
        "def burst():\n"
        "    for _ in range(5000):\n"
        "        try:\n"
        "            f(x)\n"
        "        except Exception:\n"
        "            pass\n"
        "tracemalloc.start()\n"
        "burst()\n"  # warm: interned strings, exception caches
        "gc.collect()\n"
        "before = tracemalloc.get_traced_memory()[0]\n"
        "burst()\n"
        "gc.collect()\n"
        "print(tracemalloc.get_traced_memory()[0] - before < 64 * 1024)\n",
    )
    assert got == "True", got


@pytest.mark.parametrize("method", [m for m, _k, _f in METHODS])
def test_a_refused_out_call_releases_the_callers_buffer(built, method):
    """The `out=` path holds a reference to the caller's array; a refusal
    that forgot to drop it leaks one reference per call."""
    got = _py(
        built,
        "out = np.zeros(8, np.float32)\n"
        "before = sys.getrefcount(out)\n"
        "for _ in range(50):\n"
        "    try:\n"
        f"        b.{method}(np.arange(3, dtype=np.float32), out=out)\n"
        "    except Exception:\n"
        "        pass\n"
        "print(sys.getrefcount(out) - before)\n",
    )
    assert got == "0", got


# -- 2. placement, in the generated C -----------------------------------------


def _ext(root: Path, comp: str = "blk") -> str:
    return (root / f"native/src/{comp}/{comp}_ext.c").read_text("utf-8")


def _wrapper(ext: str, method: str, cls: str = "Blk") -> str:
    """One method's wrapper, from its definition to its closing brace."""
    start = ext.index(f"\n{cls}_{method}({cls}Object *self")
    return ext[start : ext.index("\n}\n", start)]


_TEST = {
    "neg": "if (_rc < 0) {",
    "negz": "if (_rc < 0) {",
    "sen": "if (n_out == (SIZE_MAX)) {",
    "senn": "if (n_out == ((size_t)-1)) {",
}


@pytest.mark.parametrize("method", list(_TEST))
def test_each_path_tests_before_the_overflow_guard(scaffolded, method):
    """Twice, once per path, each ahead of the guard on the same count and
    each releasing the array THAT path holds."""
    body = _wrapper(_ext(scaffolded), method)
    tests = [m.start() for m in re.finditer(re.escape(_TEST[method]), body)]
    guards = [
        m.start()
        for m in re.finditer(
            r"if \(\(size_t\)\(n_out\) > \(size_t\)\(_cap\)\)", body
        )
    ]
    assert len(tests) == 2 and len(guards) == 2, body
    for t, g in zip(tests, guards):
        assert t < g, body
    out_block = body[tests[0] : guards[0]]
    alloc_block = body[tests[1] : guards[1]]
    assert "Py_DECREF(out_arr);" in out_block, out_block
    assert "Py_DECREF(arr0);" in alloc_block, alloc_block
    assert "return NULL;" in out_block and "return NULL;" in alloc_block


def test_the_message_is_an_argument_not_a_format(scaffolded):
    """Read through `_c_string_literal` as an argument: a `%` in the
    author's prose must never become a conversion."""
    body = _wrapper(_ext(scaffolded), "neg")
    assert '"%s (rc=%lld)"' in body
    sen = _wrapper(_ext(scaffolded), "sen")
    assert "PyErr_SetString(PyExc_OSError," in sen
    assert "PyErr_Format(PyExc_OSError" not in sen


# -- 3. one count type on every face ------------------------------------------


@pytest.mark.parametrize(
    "method, ctype",
    [("neg", "int64_t"), ("negz", "int32_t"), ("sen", "size_t")],
)
def test_header_stub_and_binding_agree_on_the_count_type(
    scaffolded, method, ctype
):
    from just_makeit import _config as C
    from just_makeit import _csym

    header = (scaffolded / "native/inc/p/blk/blk_core.h").read_text("utf-8")
    core = (scaffolded / "native/src/blk/blk_core.c").read_text("utf-8")
    sym = f"{_csym.stem(C.load(scaffolded), 'blk')}_{method}"
    assert re.search(rf"^{ctype} {sym}\(", header, re.M), header
    assert re.search(rf"^{ctype}\n{sym}\(", core, re.M), core
    # `_max_out` is a capacity, never a refusal: it stays size_t.
    assert re.search(rf"^size_t {sym}_max_out\(", header, re.M), header
    if ctype != "size_t":
        # `negz` is `--nogil`: the local is declared outside the released
        # block and assigned inside it.
        body = _wrapper(_ext(scaffolded), method)
        assert re.search(rf"^\s*{ctype} _rc[ ;]", body, re.M), body
        assert f"_rc = {sym}(" in body, body


def test_the_renderer_reads_the_same_count_type(scaffolded):
    """`make_methods_ctx`'s declarations are the third writer of the
    prototype, beside `_build_method_prototype` and the `_core.c` stub."""
    from just_makeit import _config as C
    from just_makeit import _csym
    from just_makeit._context._methods import make_methods_ctx

    cfg = C.load(scaffolded)
    csym = _csym.stem(cfg, "blk")
    decls = make_methods_ctx(
        "blk", "Blk", C.methods(cfg, "blk"), "p", csym=csym
    )["method_decls"]
    assert f"int64_t {csym}_neg(" in decls, decls
    assert f"int32_t {csym}_negz(" in decls, decls
    assert f"size_t {csym}_sen(" in decls, decls


# -- 4. refusals --------------------------------------------------------------

#: (id, CLI flags after --variable-output's siblings, the refusal's words).
REFUSED = [
    ("negative-over-size_t", ["--variable-output", "--error-negative"], "count_type is size_t"),
    ("signed-without-negative", ["--variable-output", "--count-type", "int64_t"], "nothing reads its sign"),
    ("sentinel-on-signed", ["--variable-output", "--count-type", "int64_t", "--error-sentinel", "SIZE_MAX"], "use --error-negative"),
    ("both-forms", ["--variable-output", "--count-type", "int64_t", "--error-negative", "--error-sentinel", "X"], "Pick one"),
    ("unsigned-count-type", ["--variable-output", "--count-type", "uint32_t"], "is not a count jm reads"),
    ("sentinel-not-variable-output", ["--error-sentinel", "SIZE_MAX"], "returns none"),
    ("count-type-not-variable-output", ["--count-type", "int64_t"], "returns none"),
    ("sentinel-on-batch", ["--variable-output", "--batch", "--error-sentinel", "SIZE_MAX"], "--batch renders its own"),
    ("error-on-empty-on-batch", ["--variable-output", "--batch", "--error-on-empty"], "--batch renders its own"),
]  # fmt: skip


@pytest.fixture
def project(tmp_path: Path) -> Path:
    _ok("new", "p", cwd=tmp_path)
    root = tmp_path / "p"
    _ok(
        "object",
        "o",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        cwd=root,
    )
    return root


@pytest.mark.parametrize(
    "flags, words", [r[1:] for r in REFUSED], ids=[r[0] for r in REFUSED]
)
def test_the_cli_refuses_what_cannot_take_effect(project, flags, words):
    before = (project / "objects" / "o.toml").read_text("utf-8")
    r = run_cli(
        "method", "o", "m", "--arg-type", "float", "--return-type", "float",
        *flags, cwd=project,
    )  # fmt: skip
    assert r.returncode == 1, r.stdout + r.stderr
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert len(errors) == 1 and words in errors[0], r.stderr
    # Refused before anything is written.
    assert (project / "objects" / "o.toml").read_text("utf-8") == before


#: The same refusals as manifest rows `apply` replays.
_ROWS = {
    "negative-over-size_t": "error_negative = true\n",
    "signed-without-negative": 'count_type = "int64_t"\n',
    "sentinel-on-signed": 'count_type = "int64_t"\nerror_sentinel = "X"\n',
    "both-forms": (
        'count_type = "int64_t"\nerror_negative = true\nerror_sentinel = "X"\n'
    ),
}


@pytest.mark.parametrize("case", list(_ROWS))
def test_apply_refuses_the_same_declarations(project, case):
    p = project / "objects" / "o.toml"
    p.write_text(
        p.read_text("utf-8")
        + '\n[[o.methods]]\nname = "hand"\narg_type = "float"\n'
        'return_type = "float"\nvariable_output = true\n' + _ROWS[case],
        encoding="utf-8",
    )
    r = run_cli("apply", cwd=project)
    assert r.returncode == 1, r.stdout + r.stderr
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    want = dict((r_[0], r_[2]) for r_ in REFUSED)[case]
    assert len(errors) == 1 and want in errors[0], r.stderr


@pytest.mark.parametrize(
    "form",
    [
        ["--count-type", "int64_t", "--error-negative"],
        ["--error-sentinel", "SIZE_MAX"],
    ],
    ids=["signed", "sentinel"],
)
def test_each_form_licenses_error_and_message(project, form):
    """`error` / `error_message` are refused without a way to raise; each
    form is one, alone -- it need not ride on `error_on_empty`."""
    assert not _method.count_why_not(
        "m",
        variable_output=True,
        count_type=form[1] if form[0] == "--count-type" else "",
        error_negative="--error-negative" in form,
        error_sentinel=form[1] if form[0] == "--error-sentinel" else "",
    )
    r = run_cli(
        "method", "o", "m", "--arg-type", "float", "--return-type", "float",
        "--variable-output", *form, "--error", "OSError",
        "--error-message", "refused", cwd=project,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr


# -- 5. plumbing --------------------------------------------------------------


def test_the_keys_survive_a_manifest_round_trip(scaffolded):
    from just_makeit import _config as C

    m = {x["name"]: x for x in C.methods(C.load(scaffolded), "blk")}
    assert m["neg"]["count_type"] == "int64_t"
    assert m["neg"]["error_negative"] is True
    assert m["sen"]["error_sentinel"] == "SIZE_MAX"
    assert m["senn"]["error_sentinel"] == "(size_t)-1"
    # The default is not written: a manifest that never asked stays as it was.
    assert "count_type" not in m["sen"]


def test_apply_rebuilds_it_unchanged(scaffolded):
    """`_apply._replay_method` names keys one by one; an unnamed one would
    replay a `size_t` kernel and drop the refusal."""
    before = _ext(scaffolded)
    _ok("apply", cwd=scaffolded)
    assert _ext(scaffolded) == before
    r = run_cli("status", "--check", cwd=scaffolded)
    assert r.returncode == 0, r.stdout


def test_script_replays_the_flags(scaffolded):
    out = _ok("script", cwd=scaffolded).stdout
    assert "--count-type int64_t" in out
    assert "--error-sentinel SIZE_MAX" in out
    assert '--error-sentinel "(size_t)-1"' in out


def test_both_doc_faces_document_each_condition(scaffolded):
    pyi = (scaffolded / "src/p/blk.pyi").read_text(encoding="utf-8")
    ext = _ext(scaffolded)
    for face in (pyi, ext):
        assert "If the C call returns a negative value." in face
        assert "If the C call returns ``SIZE_MAX``, its" in face
    # Composed with error_on_empty, BOTH conditions are listed.
    negz = D.raises_doc(
        {"name": "negz", "error_negative": True, "error_on_empty": True}
    )
    assert [d.split(".")[0] for _c, d in negz] == [
        "If the C call returns a negative value",
        "If the C call writes no output",
    ]


@pytest.mark.parametrize(
    "rows",
    [
        'count_type = "int64_t"\nerror_negative = true\n',
        'error_sentinel = "SIZE_MAX"\n',
    ],
    ids=["signed", "sentinel"],
)
def test_a_sacred_fragment_without_it_is_reported(tmp_path, rows):
    """A module object's binding fragment is sacred: `apply` never revises
    a member it already holds. A refusal value declared after first render
    is then absent from the binding, and said so by name (gh-1432's axis)
    -- not left to surface as a RuntimeError at the first refusal."""
    _ok("new", "q", cwd=tmp_path)
    proj = tmp_path / "q"
    _ok("module", "m", cwd=proj)
    _ok("object", "r", "--module", "m", cwd=proj)
    _ok(
        "method", "r", "run", "--module", "m", "--arg-type", "float",
        "--return-type", "float", "--variable-output", cwd=proj,
    )  # fmt: skip
    frag = proj / "native/src/m/m_ext_r.c"
    p = proj / "objects" / "r.toml"
    body = p.read_text("utf-8")
    assert body.count("variable_output = true\n") == 1, body
    p.write_text(
        body.replace(
            "variable_output = true\n",
            "variable_output = true\n" + rows + 'error = "ValueError"\n',
        ),
        encoding="utf-8",
    )
    r = run_cli("apply", cwd=proj)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    # Its docstrings are refreshed (`_docsync`); its binding is not.
    code = frag.read_text("utf-8")
    assert "_rc < 0" not in code and "n_out == (" not in code, code
    assert "binding no longer matches the manifest" in out, out
    assert "declares a refusal value for the count" in out, out

    # ...and a fragment that has it is silent: the exact-fill test every
    # variable_output wrapper carries is not mistaken for a sentinel.
    frag.unlink()
    _ok("apply", cwd=proj)
    again = run_cli("apply", cwd=proj)
    assert "no longer matches" not in again.stdout + again.stderr


def test_an_undeclared_method_is_unchanged(project):
    """Zero churn: without the keys, the binding has no `_rc`, no sentinel
    test, and the kernel is declared `size_t` as before."""
    _ok(
        "method", "o", "plain", "--arg-type", "float", "--return-type",
        "float", "--variable-output", cwd=project,
    )  # fmt: skip
    body = _wrapper(_ext(project, "o"), "plain", cls="O")
    assert not re.search(r"\b_rc\b", body), body
    assert "n_out == (" not in body, body
    assert "size_t n_out = " in body
    header = (project / "native/inc/p/o/o_core.h").read_text("utf-8")
    assert re.search(r"^size_t \w+_plain\(", header, re.M), header
