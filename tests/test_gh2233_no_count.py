"""``no_count``: a void-input variable_output method that is ``f()`` (gh-2233).

A ``variable_output`` method with ``arg_type = "void"`` and no ``params`` is
the generator shape: jm synthesizes a leading Python ``count`` and hands the
kernel ``size_t n``, and no key removed either. A kernel that writes **at most
one result sized by the object alone** -- a spectrogram's ``flush``, the one
zero-padded row a stream still owes -- could not be bound as ``f()``: its
callers passed a count that meant nothing and its C carried a slot it had to
ignore.

``no_count = true`` drops both. The kernel is ``fn(state, out[, max_out])``,
its ``<fn>_max_out(state)`` takes the state alone and sizes every result, and
``out=`` stays optional and is checked against that bound as before. This
file proves it in five ways:

* **the build**: one compiled object, one method per composition the issue
  names -- alone, ``out_cols``, ``none_on_empty``, ``count_type`` with
  ``error_negative``, ``exact_max_out`` -- plus doppler's ``flush`` (all of
  them at once), a ``multi_output`` result (no ``out=``, so no argument at
  all), and a placeholder bound (refused, as the all-scalar shape refuses);
* **the faces**: no ``count`` on the binding, either ``.pyi`` or the runtime
  ``__doc__``, and a state-only ``<m>_max_out()`` on all of them;
* **the manifest**: the CLI writes the key, ``jm script`` replays it, and a
  ``no_count`` on a shape with no synthesized count is refused at load;
* **the header**: a ``_max_out`` that still takes a count is refused before
  anything is written;
* **the note**: ``pass_capacity`` on such a method is live, not the gh-920
  seam ``jm status`` reports.

GATE: a ``no_count`` method takes no count on any face, calls its kernel and
its ``_max_out`` with the state alone, and composes with the keys above.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from _jmrun import run_cli, script_round_trip  # noqa: E402

from just_makeit import _config as C  # noqa: E402
from just_makeit import _object  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402


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


def _quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(io.StringIO()):
            return fn(*args, **kwargs)


# -- the build ----------------------------------------------------------------

#: One `spec` object (`nfft` 4, `pending` set at construction) and a method
#: per composition. Every kernel writes `1, 2, ...` so a result names how
#: many values it holds, and none of them reads a count: there is none.
_METHOD = """
[[spec.methods]]
name = "{name}"
arg_type = "void"
return_type = "float"
variable_output = true
no_count = true
{keys}
impl = \"\"\"{body}\"\"\"
"""

#: Write `min(pending, cap)` values and return how many.
_FILL = (
    "size_t k = state->pending < {cap} ? state->pending : {cap};\n"
    "    for (size_t i = 0; i < k; i++) out[i] = (float)(i + 1);\n"
    "    return k;"
)

#: doppler's `flush`: nothing owed is 0, a whole row is the row, and
#: anything else is a refusal the kernel reports as a negative count.
_ROW = (
    "if (state->pending == 0) return 0;\n"
    "    if (state->pending != state->nfft || max_out < state->nfft)"
    " return -1;\n"
    "    for (size_t i = 0; i < state->nfft; i++) out[i] = (float)(i + 1);\n"
    "    return (int64_t)state->nfft;"
)

_METHODS = {
    "plain": (
        "pass_capacity = true\nmax_out = 4",
        _FILL.format(cap="max_out"),
    ),
    "rows": (
        'pass_capacity = true\nmax_out = 4\nout_cols = "state->nfft"',
        "if (state->pending < state->nfft) return 0;\n"
        "    for (size_t i = 0; i < state->nfft; i++)"
        " out[i] = (float)(i + 1);\n"
        "    return state->nfft;",
    ),
    "maybe": (
        "pass_capacity = true\nmax_out = 4\nnone_on_empty = true",
        _FILL.format(cap="max_out"),
    ),
    "signed": (
        'pass_capacity = true\nmax_out = 4\ncount_type = "int64_t"\n'
        'error_negative = true\nerror = "ValueError"\n'
        'error_message = "too many pending"',
        "if (state->pending > max_out) return -1;\n"
        "    for (size_t i = 0; i < state->pending; i++)"
        " out[i] = (float)(i + 1);\n"
        "    return (int64_t)state->pending;",
    ),
    # No `pass_capacity`: the kernel is `fn(state, out)` and is never told
    # its capacity, so `exact_max_out` is the author's word that 4 bounds it.
    "exact": ("exact_max_out = true\nmax_out = 4", _FILL.format(cap="4")),
    "flush": (
        'pass_capacity = true\nmax_out = 4\nout_cols = "state->nfft"\n'
        'none_on_empty = true\ncount_type = "int64_t"\n'
        'error_negative = true\nerror = "ValueError"\n'
        'error_message = "not a whole row"',
        _ROW,
    ),
    "pair": (
        'pass_capacity = true\nmax_out = 4\nmulti_output = ["uint8_t"]',
        "size_t k = state->pending < max_out ? state->pending : max_out;\n"
        "    for (size_t i = 0; i < k; i++) {\n"
        "        out[i] = (float)(i + 1);\n"
        "        out1[i] = (uint8_t)(10 + i);\n"
        "    }\n"
        "    return k;",
    ),
    # The scaffold's `return 0;` bound, with no `pass_capacity` to make 0 a
    # value: jm cannot size the result, and says so.
    "unsized": ("", "(void)out;\n    return 0;"),
}

_FRAGMENT = """\
[spec]
arg_type = "float"
return_type = "float"
mutable = "false"
no_state = "false"
no_step = "true"

[[spec.state]]
name = "nfft"
type = "size_t"
default = "4"

[[spec.state]]
name = "pending"
type = "size_t"
default = "0"
""" + "".join(
    _METHOD.format(name=n, keys=k, body=b) for n, (k, b) in _METHODS.items()
)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The `spec` object above, applied from its fragment and compiled."""
    if _SKIP:
        pytest.skip(_SKIP)
    dest = tmp_path_factory.mktemp("gh2233") / "p"
    _quiet(new_run, "p", dest, c_prefix=None)
    fragment = dest.parent / "spec.toml"
    fragment.write_text(_FRAGMENT, encoding="utf-8")
    _quiet(apply_run, dest, fragment)

    build = dest / "build"
    for cmd in (
        [
            "cmake",
            "-S",
            str(dest),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        ["cmake", "--build", str(build)],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, f"{cmd[:2]}:\n{r.stdout}\n{r.stderr}"
    return dest


def _outcome(dest: Path, body: str) -> str:
    """What *body* prints, or ``CRASH <rc>`` when the interpreter died."""
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import numpy as np\nfrom p.spec import Spec\n" + body,
        ],
        cwd=dest,
        env={**os.environ, "PYTHONPATH": str(dest / "src")},
        capture_output=True,
        text=True,
        timeout=300,
    )
    if r.returncode != 0:
        return f"CRASH {r.returncode}: {r.stderr.strip()[-300:]}"
    return r.stdout.strip()


def test_the_kernel_and_its_bound_take_the_state_alone(built):
    """The C half of the key: no `size_t n` on the kernel or on `_max_out`,
    and the binding calls both with what remains."""
    hdr = (built / "native/inc/p/spec/spec_core.h").read_text("utf-8")
    assert "size_t spec_plain_max_out(spec_state_t *state);" in hdr, hdr
    assert (
        "size_t spec_plain(spec_state_t *state, float *out, size_t max_out);"
        in hdr
    ), hdr
    assert "int64_t spec_flush(spec_state_t *state, float *out," in hdr, hdr
    ext = (built / "native/src/spec/spec_ext.c").read_text("utf-8")
    assert "spec_plain(self->handle, _d0, _cap)" in ext, ext
    assert '"count"' not in ext, "a no_count binding still parses a count"


def test_the_call_takes_no_count(built):
    out = _outcome(
        built,
        "print(Spec(pending=3).plain().tolist())\n"
        "try:\n"
        "    Spec(pending=3).plain(count=2)\n"
        "except TypeError as e:\n"
        "    print('TypeError')\n",
    )
    assert out == "[1.0, 2.0, 3.0]\nTypeError", out


def test_the_bound_is_the_state_and_out_is_checked_against_it(built):
    """`out=` is optional, and must hold `max_out(state)` -- 4 -- as before;
    the result is a view of exactly what the kernel wrote."""
    out = _outcome(
        built,
        "print(Spec().plain_max_out())\n"
        "buf = np.full(6, -1.0, dtype=np.float32)\n"
        "r = Spec(pending=2).plain(out=buf)\n"
        "print(r.tolist(), np.shares_memory(r, buf), buf[2:].tolist())\n"
        "try:\n"
        "    Spec(pending=2).plain(out=np.zeros(3, dtype=np.float32))\n"
        "except ValueError as e:\n"
        "    print('ValueError', e)\n",
    )
    lines = out.splitlines()
    assert lines[0] == "4", out
    assert lines[1] == "[1.0, 2.0] True [-1.0, -1.0, -1.0, -1.0]", out
    assert lines[2].startswith("ValueError") and ">= 4" in lines[2], out


def test_it_composes_with_out_cols(built):
    """A `(1, nfft)` row, and a `(0, nfft)` matrix when nothing is owed."""
    out = _outcome(
        built,
        "r = Spec(pending=4).rows()\n"
        "print(r.shape, r.tolist())\n"
        "print(Spec(pending=1).rows().shape)\n",
    )
    assert out == "(1, 4) [[1.0, 2.0, 3.0, 4.0]]\n(0, 4)", out


def test_it_composes_with_none_on_empty(built):
    out = _outcome(
        built,
        "print(Spec().maybe())\nprint(Spec(pending=2).maybe().tolist())",
    )
    assert out == "None\n[1.0, 2.0]", out


def test_it_composes_with_a_signed_count_and_error_negative(built):
    out = _outcome(
        built,
        "print(Spec(pending=2).signed().tolist())\n"
        "try:\n"
        "    Spec(pending=9).signed()\n"
        "except ValueError as e:\n"
        "    print('ValueError', e)\n",
    )
    assert out.splitlines()[0] == "[1.0, 2.0]", out
    assert "ValueError too many pending" in out and "rc=-1" in out, out


def test_it_composes_with_exact_max_out(built):
    """No `pass_capacity`: the kernel is `fn(state, out)`, and the binding
    allocates the asserted bound exactly."""
    hdr = (built / "native/inc/p/spec/spec_core.h").read_text("utf-8")
    assert "size_t spec_exact(spec_state_t *state, float *out);" in hdr, hdr
    out = _outcome(built, "print(Spec(pending=4).exact().tolist())")
    assert out == "[1.0, 2.0, 3.0, 4.0]", out


def test_doppler_flush_composes_all_of_them(built):
    """doppler's Spectrogram.flush: `None` when nothing is owed, a `(1, nfft)`
    row when one is, a `ValueError` when the kernel refuses -- and the same
    row written into a caller's `(rows, nfft)` buffer."""
    out = _outcome(
        built,
        "print(Spec().flush())\n"
        "r = Spec(pending=4).flush()\n"
        "print(r.shape, r.tolist())\n"
        "try:\n"
        "    Spec(pending=2).flush()\n"
        "except ValueError as e:\n"
        "    print('ValueError', e)\n"
        "buf = np.full((2, 4), -1.0, dtype=np.float32)\n"
        "r = Spec(pending=4).flush(out=buf)\n"
        "print(r.shape, np.shares_memory(r, buf), buf[1].tolist())\n",
    )
    lines = out.splitlines()
    assert lines[0] == "None", out
    assert lines[1] == "(1, 4) [[1.0, 2.0, 3.0, 4.0]]", out
    assert lines[2].startswith("ValueError not a whole row"), out
    assert lines[3] == "(1, 4) True [-1.0, -1.0, -1.0, -1.0]", out


def test_a_multi_output_result_takes_no_argument_at_all(built):
    """No `out=` is offered for two arrays, so nothing is left to parse: the
    method is METH_NOARGS, and refuses an argument rather than dropping it."""
    out = _outcome(
        built,
        "a, b = Spec(pending=2).pair()\n"
        "print(a.tolist(), b.tolist())\n"
        "try:\n"
        "    Spec(pending=2).pair(2)\n"
        "except TypeError:\n"
        "    print('TypeError')\n",
    )
    assert out == "[1.0, 2.0] [10, 11]\nTypeError", out


def test_a_placeholder_bound_is_refused_not_overrun(built):
    """`_max_out` still `return 0;`, and no `pass_capacity` to make 0 a
    value: the same refusal as the all-scalar-params shape, naming what to
    implement."""
    out = _outcome(
        built,
        "try:\n"
        "    Spec(pending=2).unsized()\n"
        "except RuntimeError as e:\n"
        "    print('RuntimeError', e)\n",
    )
    assert out.startswith("RuntimeError"), out
    assert "spec_unsized_max_out" in out, out


# -- the faces ----------------------------------------------------------------


def _member(pyi: str, name: str) -> str:
    start = pyi.index(f"    def {name}(")
    nxt = pyi.find("\n    def ", start + 1)
    return pyi[start : nxt if nxt != -1 else len(pyi)]


def test_no_face_publishes_a_count(built):
    pyi = (built / "src/p/spec.pyi").read_text("utf-8")
    flush = _member(pyi, "flush")
    sig = flush[: flush.index(":\n")]
    assert "count" not in flush, flush
    assert "out:" in sig, sig
    assert "def flush_max_out(self) -> int:" in pyi, pyi
    assert "def pair(self) -> tuple[" in pyi, _member(pyi, "pair")
    out = _outcome(
        built,
        "print(Spec.flush.__doc__.splitlines()[0])\n"
        "print('count' in Spec.flush.__doc__)\n"
        "print(Spec.flush_max_out.__doc__.splitlines()[0])\n",
    )
    assert out == (
        "flush(out) -> ndarray | None\nFalse\nflush_max_out() -> int"
    ), out


def _scaffold(tmp_path: Path) -> Path:
    """A project made the way a user makes one: `jm new`, default layout."""
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    return tmp_path / "p"


_FLUSH_ARGV = (
    "--arg-type",
    "void",
    "--return-type",
    "float",
    "--variable-output",
    "--pass-capacity",
    "--no-count",
)


def test_a_module_object_publishes_the_same_face(tmp_path):
    """The module-aggregated `.pyi` is the other writer of the same member,
    and the peer this repo keeps finding a drifted copy in (gh-1042,
    gh-1051, gh-1074)."""
    root = _scaffold(tmp_path)
    for argv in (
        ("module", "dsp"),
        ("object", "spec", "--module", "dsp", "--no-step"),
        ("method", "spec", "flush", "--module", "dsp", *_FLUSH_ARGV),
    ):
        r = run_cli(*argv, cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
    pyi = (root / "src/p/dsp/dsp.pyi").read_text("utf-8")
    flush = _member(pyi, "flush")
    assert "count" not in flush, flush
    assert "def flush_max_out(self) -> int:" in pyi, pyi
    ext = (root / "native/src/dsp/dsp_ext_spec.c").read_text("utf-8")
    assert '"count"' not in ext, ext
    assert "p_spec_flush(self->handle, _d0, _cap)" in ext, ext
    # The module stub renders after the header is read back, whose
    # state-only `_max_out` would answer for the key. Rendered here from the
    # manifest alone -- no `_doc_blocks` -- the key has to answer by itself,
    # as the binding's own `_max_out_count_param_ctx` does.
    from just_makeit import _stubs

    bare = _stubs._obj_stub(C.load(root), "spec", "p", "dsp")
    assert "def flush_max_out(self) -> int:" in bare, bare


# -- the manifest -------------------------------------------------------------


def test_the_cli_flag_writes_the_key_and_script_replays_it(tmp_path):
    root = _scaffold(tmp_path)
    for argv in (
        ("object", "spec", "--no-step"),
        ("method", "spec", "flush", *_FLUSH_ARGV),
    ):
        r = run_cli(*argv, cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
        # The key is vocabulary: jm does not warn that it reads nothing.
        assert "unknown method key" not in r.stdout + r.stderr
    (m,) = C.methods(C.load(root), "spec")
    assert m.get("no_count") is True, m
    # The verb writes the C itself -- the sacred header's declarations and
    # the `_core.c` stubs -- before any `apply` re-renders them, so both are
    # held here, on the files the author then implements.
    hdr = (root / "native/inc/p/spec/spec_core.h").read_text("utf-8")
    assert "size_t p_spec_flush_max_out(p_spec_state_t *state);" in hdr, hdr
    assert (
        "size_t p_spec_flush(p_spec_state_t *state, float *out,"
        " size_t max_out);" in hdr
    ), hdr
    core = (root / "native/src/spec/spec_core.c").read_text("utf-8")
    assert "p_spec_flush_max_out(p_spec_state_t *state)\n{" in core, core
    assert (
        "p_spec_flush(p_spec_state_t *state, float *out, size_t max_out)\n{"
        in core
    ), core
    assert "(void)n;" not in core, core
    # ...and the glue it renders BEFORE the header it just wrote is read
    # back, which is when only the key can say there is no count.
    ext = (root / "native/src/spec/spec_ext.c").read_text("utf-8")
    assert "p_spec_flush_max_out(self->handle))" in ext, ext
    assert "p_spec_flush_max_out(self->handle," not in ext, ext
    pyi = (root / "src/p/spec.pyi").read_text("utf-8")
    assert "def flush_max_out(self) -> int:" in pyi, pyi
    assert "--no-count" in run_cli("script", cwd=root).stdout
    orig, replay = script_round_trip(root, tmp_path / "replay")
    assert orig == replay


def test_the_two_prototype_builders_agree(tmp_path):
    """`make_methods_ctx`'s declarations are the peer of the header's.

    The header gets the prototypes from `_method._build_method_prototype`;
    `make_methods_ctx` renders the same declarations for a ``_core.h``
    written whole (gh-594: "must render the identical signature"). A build
    sees only the one that reaches the header, so the other is held to it
    here -- the kernel and its `_max_out` both, with no header to read the
    arity from, so the key alone has to say there is no count.
    """
    from just_makeit import _csym
    from just_makeit._context._methods import make_methods_ctx

    root = _scaffold(tmp_path)
    for argv in (
        ("object", "spec", "--no-step"),
        ("method", "spec", "flush", *_FLUSH_ARGV),
    ):
        assert run_cli(*argv, cwd=root).returncode == 0, argv
    cfg = C.load(root)
    decls = make_methods_ctx(
        "spec",
        "Spec",
        C.methods(cfg, "spec"),
        "p",
        csym=_csym.stem(cfg, "spec"),
    )["method_decls"]
    hdr = (root / "native/inc/p/spec/spec_core.h").read_text("utf-8")
    for sym in ("p_spec_flush_max_out", "p_spec_flush"):
        pat = rf"^\w+ {sym}\([^)]*\);"
        (want,) = re.findall(pat, hdr, re.M)
        (got,) = re.findall(pat, decls, re.M)
        assert got == want, (got, want)


@pytest.mark.parametrize(
    "argv, needle",
    [
        (("--arg-type", "void"), "variable_output"),
        (("--arg-type", "float", "--variable-output"), 'arg_type = "void"'),
        (
            ("--arg-type", "void", "--variable-output", "--param", "k:int"),
            "no params",
        ),
        (
            (
                "--arg-type",
                "void",
                "--variable-output",
                "--count-default",
                "4",
            ),
            "count_default",
        ),
        (
            ("--arg-type", "void", "--variable-output", "--count-name", "n"),
            "count_name",
        ),
    ],
)
def test_no_count_where_there_is_no_count_is_refused(tmp_path, argv, needle):
    root = _scaffold(tmp_path)
    assert run_cli("object", "spec", cwd=root).returncode == 0
    r = run_cli(
        "method",
        "spec",
        "flush",
        "--return-type",
        "float",
        *argv,
        "--no-count",
        cwd=root,
    )
    text = r.stdout + r.stderr
    assert r.returncode != 0, text
    assert "no_count" in text and needle in text, text


def _declare_no_count(frag: Path) -> None:
    """Add `no_count = true` to the one method in *frag*, by hand.

    Beside `variable_output`, not appended: a method with params ends in a
    `[[...params]]` table, where an appended key would land on the param.
    """
    text = frag.read_text("utf-8")
    assert text.count("variable_output = true\n") == 1, text
    frag.write_text(
        text.replace(
            "variable_output = true\n",
            "variable_output = true\nno_count = true\n",
        ),
        encoding="utf-8",
    )


_GENERATOR = {"no_count": True, "variable_output": True, "arg_type": "void"}


@pytest.mark.parametrize(
    "extra, needle",
    [
        ({"no_count": "yes"}, "must be true or false"),
        ({"variable_output": False}, "needs variable_output"),
        ({"arg_type": "float[]"}, 'needs arg_type = "void"'),
        ({"params": [{"name": "k", "type": "int"}]}, "no params"),
        ({"extra_args": [{"name": "k", "type": "int"}]}, "no params"),
        ({"count_default": "4"}, "count_default describes"),
        ({"count_name": "n"}, "count_name describes"),
        ({"batch": True}, "batch is a 1:1 transform"),
        (
            {"result_fields": [{"name": "i", "type": "int"}]},
            "record or list-of-records",
        ),
    ],
)
def test_every_load_rule_refuses_its_shape(extra, needle):
    """One row per rule `_config._no_count_errors` states, each on the
    generator shape that is otherwise accepted, so a rule that stops firing
    fails here by name. The CLI and `apply` both ask this function."""
    assert C._no_count_errors(dict(_GENERATOR), "m") == []
    errs = C._no_count_errors({**_GENERATOR, **extra}, "m")
    assert len(errs) == 1 and needle in errs[0], errs


def test_a_record_array_result_may_drop_its_count():
    """`record_dtype` rows are a variable_output ARRAY, which does read the
    count -- so `no_count` applies to them, and is not refused."""
    entry = {
        **_GENERATOR,
        "result_fields": [{"name": "i", "type": "int"}],
        "record_dtype": "rec_t",
    }
    assert C._no_count_errors(entry, "m") == []


def test_a_hand_edited_manifest_is_refused_on_load(tmp_path):
    """The CLI is not the only writer: a manifest edited by hand reaches the
    same validator when it is loaded."""
    root = _scaffold(tmp_path)
    for argv in (
        ("object", "spec"),
        ("method", "spec", "run", "--param", "x:float[]", "--return-type",
         "float", "--variable-output"),
    ):  # fmt: skip
        assert run_cli(*argv, cwd=root).returncode == 0, argv
    _declare_no_count(root / "objects" / "spec.toml")
    r = run_cli("apply", cwd=root)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "no_count needs a method with no params" in r.stdout + r.stderr


def test_a_counted_max_out_in_the_header_is_refused(tmp_path):
    """The PSD adoption path: a method scaffolded WITH a count, whose header
    therefore declares `_max_out(state, size_t n)`, gains `no_count` in the
    manifest. The binding would call a two-argument function with one, so
    the render refuses -- naming the declaration to write -- and writes
    nothing."""
    root = _scaffold(tmp_path)
    for argv in (
        ("object", "spec", "--no-step"),
        ("method", "spec", "flush", *_FLUSH_ARGV[:-1]),
    ):
        assert run_cli(*argv, cwd=root).returncode == 0, argv
    hdr = root / "native/inc/p/spec/spec_core.h"
    counted = r"p_spec_flush_max_out\([^)]*,\s*size_t n\)"
    assert re.search(counted, hdr.read_text("utf-8"))
    _declare_no_count(root / "objects" / "spec.toml")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    r = run_cli("apply", cwd=root)
    text = r.stdout + r.stderr
    assert r.returncode != 0, text
    assert "no_count" in text and "p_spec_flush_max_out" in text, text
    assert "p_spec_state_t *state)" in text, text
    after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert after == before


# -- the note -----------------------------------------------------------------


def test_pass_capacity_on_a_no_count_method_is_not_inert(tmp_path):
    """gh-921 reports `pass_capacity` beside a state-only `_max_out` as
    inert, because a bound that cannot see the count cannot be trusted as a
    per-call one. A no_count call HAS no count: its state-only bound is the
    per-call bound, and the binding trusts it exactly."""
    root = _scaffold(tmp_path)
    for argv in (
        ("object", "spec", "--no-step"),
        ("method", "spec", "flush", *_FLUSH_ARGV),
    ):
        assert run_cli(*argv, cwd=root).returncode == 0, argv
    cfg = C.load(root)
    assert _object.inert_pass_capacity(cfg, root, "spec") == []
    # The exact allocation, on the render that READS the header's
    # state-only `_max_out` -- `apply`'s -- and not only on the verb's,
    # which renders before that declaration is read back.
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    ext = (root / "native/src/spec/spec_ext.c").read_text("utf-8")
    assert "(void)_need;" in ext, ext
    assert "_cap < _need" not in ext, ext


# -- the element contract -----------------------------------------------------


def test_the_invariant_round_trip_calls_a_no_count_reader_with_nothing(
    tmp_path,
):
    """gh-1404's generated round trip writes 8 elements and reads them back
    with `obj.<reader>(len(x))`. A no_count reader takes no count -- the 8
    would be read as its `out=` buffer and refused -- so it is called `()`,
    and returns what the object holds."""
    root = _scaffold(tmp_path)
    for argv in (
        ("object", "o", "--no-step"),
        ("record", "o", "sample", "--type", "float"),
        ("method", "o", "write", "--arg-type", "sample[]",
         "--return-type", "bool"),
        ("method", "o", "drain", "--arg-type", "void", "--return-type",
         "sample", "--variable-output", "--pass-capacity", "--no-count"),
    ):  # fmt: skip
        r = run_cli(*argv, cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
    # The round trip is generated only once the reader is real (a scaffold
    # stub `(void)`s its state, `_invariants.kernel_is_stub`).
    core = root / "native/src/o/o_core.c"
    stub = (
        "    (void)state;\n    (void)out; (void)max_out;\n"
        "    return 0; /* placeholder */\n}\n"
    )
    text = core.read_text("utf-8")
    assert text.count(stub) == 1, text
    core.write_text(
        text.replace(
            stub,
            "    out[0] = (float)state->gain; (void)max_out;\n"
            "    return 1;\n}\n",
        ),
        encoding="utf-8",
    )
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    inv = (root / "src/p/tests/test_o_invariants.py").read_text("utf-8")
    assert "    y = obj.drain()\n" in inv, inv
