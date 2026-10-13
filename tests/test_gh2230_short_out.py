"""``short_out``: an ``out=`` smaller than ``max_out()``, for backpressure.

gh-2230. A ``variable_output`` method's ``out=`` must hold at least what the
binding would have allocated itself (`_context._methods._capacity_exprs`),
so a short buffer raises ``out has N elements, need >= M``. That protects a
kernel that merely clamps: handed a short buffer it silently drops output it
owed. A **backpressure** kernel's contract is the reverse -- a short buffer
is how the caller asks for less, the kernel writes the whole rows that fit,
takes only the input that produced them, and the object reports how much
(doppler's Spectrogram: ``rows = s.push(x, out=buf); x = x[s.consumed:]``).
With the minimum in place that contract was unreachable from Python: no
caller could present a short ``out``, so ``consumed`` was always the whole
input.

``short_out = true`` declares the contract. It requires ``pass_capacity``,
so the kernel is told the caller's true size, and is refused at load
without it. The buffer then has no minimum beyond its shape: under
``out_cols`` any 2-D array of that width, any number of rows, 0 included;
otherwise any length. ``consumed`` stays the object's own property.

This file proves it four ways:

* **the build**: one compiled object, a backpressure kernel under the key
  driven through a short 2-D ``out=`` by the resume loop above until the
  whole input is processed; an empty buffer; a short flat buffer; and the
  same kernel WITHOUT the key, which still refuses a short buffer;
* **the refusals**: wrong width, rank and dtype are still refused;
* **the docs**: both ``.pyi`` producers and the runtime ``__doc__`` say the
  buffer has no minimum, for the method that declares it and only for it;
* **the manifest**: the key is refused without ``pass_capacity`` or
  ``variable_output``, on the CLI and on a hand-edited manifest, and the CLI
  flag, ``jm script`` and ``_dump`` carry it.

GATE: a ``short_out`` method accepts an ``out=`` of any size its shape
allows and returns what the kernel wrote; every other method keeps today's
refusal.
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

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from _jmrun import run_cli, script_round_trip  # noqa: E402

from just_makeit import _config as C  # noqa: E402


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


# -- the build ----------------------------------------------------------------

#: One kernel, three declarations, over an object of width 4 whose `taken`
#: field is the backpressure report, published as the read-only `consumed`.
#:
#: The kernel writes the whole rows that fit in `max_out`; when the buffer
#: ran out before the input did, it took only the rows it wrote, otherwise it
#: took the whole input (a tail that completes no row included, so a resume
#: loop terminates). `max_out()` answers a fixed 64, so the minimum a
#: buffer must hold without the key is 64 elements -- 16 rows.
#:
#: * ``push``  -- the key, on a matrix result (`out_cols`);
#: * ``full``  -- the same kernel without the key: the refusal it keeps;
#: * ``flat``  -- the key on a flat result, which has no rows to respect.
_KERNEL = '''impl = """size_t w = state->width;
    size_t avail = n_in / w;
    size_t fit = max_out / w;
    size_t rows = avail < fit ? avail : fit;
    for (size_t i = 0; i < rows * w; i++) out[i] = in[i];
    state->taken = rows < avail ? rows * w : n_in;
    return rows * w;"""'''

_FRAGMENT = f'''\
[spec]
arg_type = "float"
return_type = "float"
mutable = "false"
no_state = "false"
no_step = "true"

[[spec.state]]
name = "width"
type = "size_t"
default = "4"

[[spec.state]]
name = "taken"
type = "size_t"
default = "0"

[[spec.properties]]
name = "consumed"
type = "size_t"
expr = "self->handle->taken"

[[spec.methods]]
name = "push"
arg_type = "float"
return_type = "float"
variable_output = true
pass_capacity = true
short_out = true
out_cols = "state->width"
max_out = 64
{_KERNEL}

[[spec.methods]]
name = "full"
arg_type = "float"
return_type = "float"
variable_output = true
pass_capacity = true
out_cols = "state->width"
max_out = 64
{_KERNEL}

[[spec.methods]]
name = "flat"
arg_type = "float"
return_type = "float"
variable_output = true
pass_capacity = true
short_out = true
max_out = 64
impl = """size_t n = n_in < max_out ? n_in : max_out;
    for (size_t i = 0; i < n; i++) out[i] = in[i];
    state->taken = n;
    return n;"""
'''


def _new(where: Path, name: str = "p") -> Path:
    r = run_cli("new", name, "--no-c-prefix", cwd=where)
    assert r.returncode == 0, r.stdout + r.stderr
    return where / name


@pytest.fixture(scope="module")
def applied(tmp_path_factory) -> Path:
    """The `spec` object above, applied from its fragment."""
    base = tmp_path_factory.mktemp("gh2230")
    dest = _new(base)
    fragment = base / "spec.toml"
    fragment.write_text(_FRAGMENT, encoding="utf-8")
    r = run_cli("apply", str(fragment), cwd=dest)
    assert r.returncode == 0, r.stdout + r.stderr
    return dest


@pytest.fixture(scope="module")
def built(applied) -> Path:
    """`applied`, compiled."""
    if _SKIP:
        pytest.skip(_SKIP)
    build = applied / "build"
    for cmd in (
        [
            "cmake",
            "-S",
            str(applied),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        ["cmake", "--build", str(build)],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, f"{cmd[:2]}:\n{r.stdout}\n{r.stderr}"
    return applied


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
        return f"CRASH {r.returncode}: {r.stderr.strip()[-400:]}"
    return r.stdout.strip()


def test_a_short_out_resumes_through_consumed_over_the_whole_input(built):
    """The issue's loop: 42 samples, a 3-row buffer, resumed at `consumed`.

    Ten whole rows come out over four calls -- three, three, three, one --
    in order, and the two-sample tail completes no row, so the last call
    takes it whole and the loop ends. Each result is a view of the caller's
    buffer, shaped to the rows the kernel wrote.
    """
    out = _outcome(
        built,
        "s = Spec()\n"
        "x = np.arange(42, dtype=np.float32)\n"
        "buf = np.full((3, 4), -1.0, np.float32)\n"
        "rows, rest = [], x\n"
        "while rest.size:\n"
        "    r = s.push(rest, out=buf)\n"
        "    assert np.shares_memory(r, buf)\n"
        "    rows.append(r.copy())\n"
        "    rest = rest[s.consumed:]\n"
        "print([r.shape[0] for r in rows])\n"
        "got = np.concatenate(rows)\n"
        "print(got.shape, bool((got.ravel() == x[:40]).all()))\n",
    )
    assert out == "[3, 3, 3, 1]\n(10, 4) True", out


def test_an_empty_out_is_accepted_and_takes_nothing(built):
    """Zero rows is a buffer too: the kernel is told 0, writes nothing and
    takes nothing, and the result keeps its width."""
    out = _outcome(
        built,
        "s = Spec()\n"
        "r = s.push(np.arange(8, dtype=np.float32),\n"
        "           out=np.zeros((0, 4), np.float32))\n"
        "print(r.shape, s.consumed)\n",
    )
    assert out == "(0, 4) 0", out


def test_a_flat_short_out_takes_any_length(built):
    """Without `out_cols` there are no rows: any length, 0 included."""
    out = _outcome(
        built,
        "s = Spec()\n"
        "x = np.arange(10, dtype=np.float32)\n"
        "r = s.flat(x, out=np.zeros(3, np.float32))\n"
        "print(r.tolist(), s.consumed)\n"
        "r = s.flat(x, out=np.zeros(0, np.float32))\n"
        "print(r.shape, s.consumed)\n",
    )
    assert out == "[0.0, 1.0, 2.0] 3\n(0,) 0", out


@pytest.mark.parametrize(
    "buf, exc, needle",
    [
        ("np.zeros((3, 3), np.float32)", "ValueError", "4 columns"),
        ("np.zeros(12, np.float32)", "ValueError", "2-D"),
        ("np.zeros((3, 4), np.float64)", "TypeError", "output dtype"),
    ],
    ids=["width", "rank", "dtype"],
)
def test_the_shape_is_still_refused(built, buf, exc, needle):
    out = _outcome(
        built,
        "try:\n"
        f"    Spec().push(np.arange(8, dtype=np.float32), out={buf})\n"
        "    print('accepted')\n"
        "except Exception as e:\n"
        "    print(type(e).__name__, e)\n",
    )
    assert out.startswith(exc) and needle in out, out


def test_without_the_key_a_short_out_is_still_refused(built):
    """The same kernel, undeclared: today's refusal, word for word."""
    out = _outcome(
        built,
        "try:\n"
        "    Spec().full(np.arange(42, dtype=np.float32),\n"
        "                out=np.zeros((3, 4), np.float32))\n"
        "    print('accepted')\n"
        "except ValueError as e:\n"
        "    print('ValueError', e)\n",
    )
    assert out == "ValueError out has 12 elements, need >= 64", out


def test_the_allocating_path_is_unchanged(built):
    """No `out=`: the binding allocates `max_out()` and the kernel takes the
    whole input, exactly as before the key."""
    out = _outcome(
        built,
        "s = Spec()\n"
        "r = s.push(np.arange(42, dtype=np.float32))\n"
        "print(r.shape, s.consumed)\n",
    )
    assert out == "(10, 4) 42", out


# -- the binding, read without a compiler -------------------------------------


def _wrapper(ext_c: str, method: str) -> str:
    """The C wrapper jm generated for *method*, up to its closing brace."""
    i = ext_c.index(f"\nSpec_{method}(")
    return ext_c[i : ext_c.index("\n}\n", i)]


def test_only_the_declaring_methods_lose_the_minimum(applied):
    ext = (applied / "native/src/spec/spec_ext.c").read_text("utf-8")
    for method in ("push", "flat"):
        body = _wrapper(ext, method)
        assert "_min_cap" not in body and "need >=" not in body, body
        assert "out_arr), _cap)" in body, body
    assert "need >= %zu" in _wrapper(ext, "full")


# -- the docs: both stubs and the runtime literal ------------------------------


_C_LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _runtime_doc(ext_c: str, method: str) -> str:
    """*method*'s ``PyMethodDef`` doc literal, whitespace-normalised."""
    start = ext_c.index(f'{{"{method}",')
    end = ext_c.index("},", start)
    text = "".join(_C_LITERAL.findall(ext_c[start:end]))
    return " ".join(text.encode().decode("unicode_escape").split())


def _stub_doc(pyi: str, method: str) -> str:
    """*method*'s ``.pyi`` entry, whitespace-normalised."""
    start = pyi.index(f"def {method}(")
    nxt = pyi.find("    def ", start + 1)
    return " ".join(pyi[start : nxt if nxt > 0 else None].split())


#: (face, module, the .pyi, the binding's C file) -- the standalone stub is
#: `_context/_methods`'s, the module-aggregated one is `_stubs`'.
_FACES = [
    ("standalone", None, "src/demo/spec.pyi", "native/src/spec/spec_ext.c"),
    (
        "module",
        "dsp",
        "src/demo/dsp/dsp.pyi",
        "native/src/dsp/dsp_ext_spec.c",
    ),
]

_NO_MINIMUM = "It has no minimum size"


@pytest.fixture(scope="module", params=_FACES, ids=[f[0] for f in _FACES])
def faces(request, tmp_path_factory) -> "dict[str, dict[str, str]]":
    """Each method's stub and runtime doc, on one face."""
    _, module, pyi, ext = request.param
    root = _new(tmp_path_factory.mktemp("gh2230doc"), "demo")
    mod = ["--module", module] if module else []
    argvs = []
    if module:
        argvs.append(("module", module))
    argvs.append(
        ("object", "spec", *mod, "--state", "width:size_t:4", "--no-step")
    )
    for name, extra in (("push", ["--short-out"]), ("full", [])):
        argvs.append(
            (
                "method",
                "spec",
                name,
                *mod,
                "--arg-type",
                "float",
                "--return-type",
                "float",
                "--variable-output",
                "--pass-capacity",
                *extra,
            )
        )
    for argv in argvs:
        r = run_cli(*argv, cwd=root)
        assert r.returncode == 0, (argv, r.stdout + r.stderr)
    pyi_text = (root / pyi).read_text("utf-8")
    ext_text = (root / ext).read_text("utf-8")
    return {
        name: {
            "stub": _stub_doc(pyi_text, name),
            "runtime": _runtime_doc(ext_text, name),
        }
        for name in ("push", "full")
    }


@pytest.mark.parametrize("face", ["stub", "runtime"])
def test_the_docs_say_the_buffer_has_no_minimum(faces, face):
    assert _NO_MINIMUM in faces["push"][face], faces["push"][face]
    assert "Optional pre-allocated" in faces["full"][face]
    assert _NO_MINIMUM not in faces["full"][face], faces["full"][face]


# -- the manifest ---------------------------------------------------------------


def _object(root: Path) -> None:
    r = run_cli(
        "object", "spec", "--state", "width:size_t:4", "--no-step", cwd=root
    )
    assert r.returncode == 0, r.stdout + r.stderr


_METHOD = (
    "method",
    "spec",
    "push",
    "--arg-type",
    "float",
    "--return-type",
    "float",
)


@pytest.mark.parametrize(
    "extra, needle",
    [
        (["--variable-output"], "needs pass_capacity = true"),
        (["--pass-capacity"], "needs variable_output = true"),
        (
            ["--variable-output", "--pass-capacity", "--multi-output", "int"],
            "multi_output",
        ),
    ],
    ids=["no-pass-capacity", "no-variable-output", "multi-output"],
)
def test_the_cli_refuses_the_flag_where_it_cannot_apply(
    tmp_path, extra, needle
):
    root = _new(tmp_path)
    _object(root)
    r = run_cli(*_METHOD, *extra, "--short-out", cwd=root)
    text = r.stdout + r.stderr
    assert r.returncode != 0, text
    assert "short_out" in text and needle in text, text
    # Refused before anything was written.
    assert "short_out" not in (root / "objects/spec.toml").read_text("utf-8")


def test_a_hand_edited_manifest_is_refused_on_load(tmp_path):
    """The CLI is not the only writer: a key added by hand without
    `pass_capacity` reaches the same validator when the manifest loads."""
    root = _new(tmp_path)
    _object(root)
    r = run_cli(*_METHOD, "--variable-output", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    frag = root / "objects" / "spec.toml"
    text = frag.read_text("utf-8")
    assert "variable_output = true\n" in text
    frag.write_text(
        text.replace(
            "variable_output = true\n",
            "variable_output = true\nshort_out = true\n",
            1,
        ),
        encoding="utf-8",
    )
    r = run_cli("apply", cwd=root)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "short_out needs pass_capacity = true" in r.stdout + r.stderr


def test_the_flag_writes_the_key_and_script_replays_it(tmp_path):
    root = _new(tmp_path)
    _object(root)
    r = run_cli(
        *_METHOD,
        "--variable-output",
        "--pass-capacity",
        "--short-out",
        cwd=root,
    )
    text = r.stdout + r.stderr
    assert r.returncode == 0, text
    # The key is vocabulary: jm does not warn that it reads nothing.
    assert "unknown method key" not in text, text
    frag = (root / "objects/spec.toml").read_text("utf-8")
    assert re.search(r"^short_out = true$", frag, re.M), frag
    ext = (root / "native/src/spec/spec_ext.c").read_text("utf-8")
    assert "need >=" not in _wrapper(ext, "push")
    # `jm script` replays it, and the replay renders the same project.
    assert "--short-out" in run_cli("script", cwd=root).stdout
    orig, replay = script_round_trip(root, tmp_path / "replay")
    assert orig == replay


def test_dump_writes_short_out_and_it_reads_back(tmp_path):
    """`apply` replays every manifest through `_dump`, so a key it forgot
    would be lost on the next apply."""
    root = _new(tmp_path)
    _object(root)
    r = run_cli(
        *_METHOD,
        "--variable-output",
        "--pass-capacity",
        "--short-out",
        cwd=root,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    text = C._dump(C.load(root))
    assert re.search(r"^short_out = true$", text, re.M), text
