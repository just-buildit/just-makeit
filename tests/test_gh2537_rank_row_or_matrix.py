"""``rank = [1, 2]``: a ``T[][]`` array that also takes a 1-D one (gh-2537).

A constructor's ``T[][]`` init-param passes ``create()`` its data and two
extents, and jm refused any array that was not 2-D. doppler's
``Correlator(ref, ...)`` takes a 1-D reference or a 2-D template through one
``(ref, ny, nx)`` C contract, so its commonest call needed ``ref[None, :]``:
``rank = 1`` on the ``T[][]`` was refused ("can never hold"), a 1-D type
with a two-name ``derived`` was refused, and a 1-D type without ``rank``
flattened a 2-D template and lost its shape.

Now ``rank = [1, 2]`` is the one list ``rank`` may be, and only on a
``T[][]``, read by the one reader (``_coerce.array_rank``). A constructor
reads a 1-D array as ONE row -- ``dim0 = 1``, ``dim1`` its length -- and a
2-D one as before; any other rank is refused naming both. The C signature
does not change. The ``.pyi`` says what a 1-D input means. The list is an
init param's only: a method's or a module function's ``T[][]`` passes a flat
length, with no row to read (gh-1908), so the list is refused there.

Proven three ways:

* **the build**: a compiled object whose ``create()`` records the extents it
  is handed, constructed from a 1-D, a 2-D and a 3-D array, on the required
  and the optional-dispatch path, with refcounts unchanged by a refusal;
* **the render and the doc**: the guard is the one emitter's, and the stub
  names the 1-D reading;
* **the refusals and the round trip**: every other list, the list on a 1-D
  type, and the list on a method or module-function param stop the load; the key survives ``save``/``load``, ``apply``
  and ``jm script``.

GATE: a ``rank = [1, 2]`` array is read as a row or a matrix, never refused
as 1-D and never accepted at a rank it has no reading for.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from _jmrun import run_cli  # noqa: E402

from just_makeit import _coerce  # noqa: E402
from just_makeit import _config as C  # noqa: E402
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


def _apply_fragment(base: Path, fragment: str) -> Path:
    """A fresh project with *fragment* applied to it."""
    dest = base / "p"
    _quiet(new_run, "p", dest, c_prefix=None)
    frag = base / "frag.toml"
    frag.write_text(fragment, encoding="utf-8")
    _quiet(apply_run, dest, frag)
    return dest


# -- the build ----------------------------------------------------------------

#: `w` is unguarded and FIRST, so `ref`'s guard bails out with `w_arr` held:
#: the refcount test fails unless the guard releases it. `cor` is the
#: required path; `opt` the optional-dispatch path, whose guard runs with
#: `w_arr` held too.
_FRAGMENT = """\
[module.k]
objects = ["cor", "opt"]

[cor]
arg_type = "float"
return_type = "float"
mutable = "false"
no_state = "false"
no_step = "true"

[[cor.state]]
name = "ny"
type = "size_t"
default = "0"

[[cor.state]]
name = "nx"
type = "size_t"
default = "0"

[[cor.init_params]]
name = "w"
type = "float[]"

[[cor.init_params]]
name = "ref"
type = "float _Complex[][]"
derived = ["ny", "nx"]
rank = [1, 2]

[opt]
arg_type = "float"
return_type = "float"
mutable = "false"
no_state = "false"
no_step = "true"

[[opt.state]]
name = "ny"
type = "size_t"
default = "0"

[[opt.state]]
name = "nx"
type = "size_t"
default = "0"

[[opt.init_params]]
name = "w"
type = "float[]"

[[opt.init_params]]
name = "bank"
type = "float[][]"
optional = true
create_fn = "opt_create_with_bank"
rank = [1, 2]
"""


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The module above, applied from its fragment and compiled.

    Each `create()` -- `opt`'s dispatched one too -- records the extents it
    was handed in its `ny` / `nx` state.
    """
    if _SKIP:
        pytest.skip(_SKIP)
    dest = _apply_fragment(tmp_path_factory.mktemp("gh2537"), _FRAGMENT)
    core = dest / "native/src/cor/cor_core.c"
    text = core.read_text("utf-8")
    for field in ("ny", "nx"):
        assert text.count(f"    obj->{field} = 0;\n") == 1, text
        text = text.replace(
            f"    obj->{field} = 0;\n", f"    obj->{field} = {field};\n"
        )
    core.write_text(text, encoding="utf-8")
    alt = dest / "native/src/opt/opt_core.c"
    text = alt.read_text("utf-8")
    # the dispatched constructor's scaffold: record its extents as well
    head = "opt_create_with_bank("
    assert text.count(head) == 1, text
    body = text.index("{", text.index(head))
    text = (
        text[: body + 1] + "\n    opt_state_t *o = opt_create(w, w_len);"
        "\n    (void)bank;"
        "\n    if (o) { o->ny = bank_dim0; o->nx = bank_dim1; }"
        "\n    return o;" + text[body + 1 :]
    )
    alt.write_text(text, encoding="utf-8")
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
            "import sys\nimport numpy as np\nfrom p.k import Cor, Opt\n"
            "W = np.zeros(3, np.float32)\n" + body,
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


#: The extents `create()` saw for each shape, or the ValueError's text.
_SHAPES = """\
for shape in [(8,), (3, 5), (1, 8), (2, 3, 4), ()]:
    try:
        o = {ctor}
        print(shape, o.get_ny(), o.get_nx())
    except ValueError as e:
        print(shape, e)
"""


@pytest.mark.parametrize(
    "ctor,name",
    [
        ("Cor(W, np.ones(shape, np.complex64))", "ref"),
        ("Opt(W, np.ones(shape, np.float32))", "bank"),
    ],
    ids=["required", "optional dispatch"],
)
def test_a_1d_array_is_one_row_and_a_2d_one_is_unchanged(built, ctor, name):
    got = _outcome(built, _SHAPES.format(ctor=ctor))
    assert got.splitlines() == [
        "(8,) 1 8",
        "(3, 5) 3 5",
        "(1, 8) 1 8",
        f"(2, 3, 4) {name} must be a 1-D or 2-D array",
        f"() {name} must be a 1-D or 2-D array",
    ], got


@pytest.mark.parametrize("ctor", ["Cor", "Opt"])
def test_a_refusal_releases_every_array_it_held(built, ctor):
    """`w` is held when the second array's guard bails out."""
    got = _outcome(
        built,
        "args = (W, np.zeros((2, 3, 4), np.float32))\n"
        "before = [sys.getrefcount(a) for a in args]\n"
        "for _ in range(300):\n"
        "    try:\n"
        f"        {ctor}(*args)\n"
        "    except (ValueError, TypeError):\n"
        "        pass\n"
        "print([sys.getrefcount(a) for a in args] == before)\n",
    )
    assert got == "True", got


# -- the render and the doc ----------------------------------------------------


def test_the_guard_is_the_one_emitters(built):
    ext = (built / "native/src/k/k_ext_cor.c").read_text("utf-8")
    guard = _coerce.array_rank_guard(
        "ref",
        "ref_arr",
        (1, 2),
        "Py_DECREF(w_arr);",
        fail="return -1;",
    )
    assert guard in ext, ext
    assert _coerce.array_extents_c("ref", "ref_arr", (1, 2)) in ext


def test_the_stub_says_what_a_1d_array_means(built):
    pyi = (built / "src/p/k/k.pyi").read_text("utf-8")
    note = _coerce.rank_doc((1, 2))
    assert note
    # once per class: `ref` and `bank`
    assert pyi.count(note) == 2, pyi


def test_a_plain_t2d_array_keeps_its_2d_guard_and_no_note(tmp_path):
    """`rank = 2`, or none, is the type's own rank: no 1-D reading."""
    frag = _FRAGMENT.replace("rank = [1, 2]\n", "")
    dest = _apply_fragment(tmp_path, frag)
    ext = (dest / "native/src/k/k_ext_cor.c").read_text("utf-8")
    assert "if (PyArray_NDIM(ref_arr) != 2) {" in ext
    assert '"ref must be a 2-D array"' in ext
    assert "PyArray_NDIM(ref_arr) == 1 ? 1 :" not in ext
    pyi = (dest / "src/p/k/k.pyi").read_text("utf-8")
    assert _coerce.rank_doc((1, 2)) not in pyi


# -- the refusals --------------------------------------------------------------


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    _quiet(new_run, "p", root, c_prefix=None)
    return root


def _write_row(root: Path, table: str, row: dict) -> None:
    """Put *row* on `o`'s init-params, a method's params or a function's."""
    cfg = C.load(root)
    cfg["o"] = {
        "arg_type": "float",
        "return_type": "float",
        "no_state": "true",
        "no_step": "true",
    }
    if table == "init_params":
        cfg["o"]["init_params"] = [row]
    elif table == "function":
        cfg.setdefault("module", {})["f"] = {
            "functions": [
                {"name": "fn", "return_type": "int", "params": [row]}
            ]
        }
    else:
        cfg["o"]["methods"] = [
            {
                "name": "m",
                "arg_type": "void",
                "return_type": "int",
                "params": [row],
            }
        ]
    C.save(root, cfg)


_REFUSED = {
    "a list on a 1-D type": (
        {"name": "g", "type": "float[]", "rank": [1, 2]},
        "a list of ranks is read only on a T[][] array",
    ),
    "another order": (
        {"name": "g", "type": "float[][]", "rank": [2, 1]},
        "the one list of ranks is [1, 2]",
    ),
    "a third rank": (
        {"name": "g", "type": "float[][]", "rank": [1, 2, 3]},
        "the one list of ranks is [1, 2]",
    ),
    "one rank in a list": (
        {"name": "g", "type": "float[][]", "rank": [2]},
        "the one list of ranks is [1, 2]",
    ),
}


@pytest.mark.parametrize("case", sorted(_REFUSED))
@pytest.mark.parametrize("table", ["init_params", "method", "function"])
def test_a_list_with_no_reading_is_refused(tmp_path, case, table):
    root = _project(tmp_path)
    row, says = _REFUSED[case]
    _write_row(root, table, dict(row))
    r = run_cli("apply", cwd=root)
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert r.returncode != 0 and len(errors) == 1, r.stderr
    assert says in errors[0], errors[0]


@pytest.mark.parametrize("table", ["method", "function"])
def test_the_list_is_refused_on_a_method_or_function_param(tmp_path, table):
    """Only a constructor's `T[][]` passes two extents a row can fill.

    A method's or a function's `T[][]` passes one flat length, so there is
    no row reading, and gh-1908 would refuse the type there outright.
    """
    root = _project(tmp_path)
    _write_row(root, table, {"name": "g", "type": "float[][]", "rank": [1, 2]})
    r = run_cli("apply", cwd=root)
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert r.returncode != 0 and len(errors) == 1, r.stderr
    assert "read only on a constructor's T[][]" in errors[0], errors[0]
    assert "gh-1908" in errors[0], errors[0]


@pytest.mark.parametrize("table", ["method", "function"])
@pytest.mark.parametrize("rank", [1, 2])
def test_an_int_rank_on_a_method_or_function_t2d_still_loads(
    tmp_path, table, rank
):
    """The refusal is the list's alone: `rank = 1` / `2` load as before."""
    root = _project(tmp_path)
    row = {"name": "g", "type": "float[][]", "rank": rank}
    _write_row(root, table, dict(row))
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stderr


def test_rank_1_on_a_t2d_names_the_list_that_holds(tmp_path):
    root = _project(tmp_path)
    _write_row(
        root, "init_params", {"name": "g", "type": "float[][]", "rank": 1}
    )
    r = run_cli("apply", cwd=root)
    assert r.returncode != 0
    assert "declare rank = [1, 2]" in r.stderr, r.stderr


# -- the round trip ------------------------------------------------------------


def test_the_list_survives_save_load_and_script(tmp_path):
    root = _project(tmp_path)
    row = {"name": "g", "type": "float[][]", "rank": [1, 2]}
    _write_row(root, "init_params", dict(row))
    got = _quiet(C.load, root)["o"]["init_params"][0]
    assert got["rank"] == [1, 2], got
    r = run_cli("script", cwd=root)
    assert r.returncode == 0, r.stderr
    note = [ln for ln in r.stdout.splitlines() if "'g' declares" in ln]
    assert len(note) == 1 and "rank = [1, 2]" in note[0], r.stdout


def test_a_second_apply_from_the_written_manifest_agrees(built):
    ext = built / "native/src/k/k_ext_cor.c"
    before = ext.read_text("utf-8")
    r = run_cli("apply", cwd=built)
    assert r.returncode == 0, r.stderr
    assert ext.read_text("utf-8") == before
