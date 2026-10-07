"""``rank`` and ``elements_per_sample`` on a constructor array (gh-2004).

gh-805 §C gave a method's (and a module function's) array param two shape
keys: ``rank``, the opt-in ``PyArray_NDIM`` guard, and
``elements_per_sample``, the interleave factor between numpy's element count
and the samples the C counts. ``[[<obj>.init_params]]`` had neither, so a
constructor array whose C contract is 1-D silently flattened a 2-D input
through ``PyArray_SIZE``: doppler's ``HalfbandDecimator(h)`` constructed from
a ``(4, 19)`` array, and ``jm adopt`` dropped the hand-written guard that had
refused it, because no manifest key could carry it. Written anyway, either
key drew an unknown-key warning and did nothing.

Now both are init-param keys, read by the readers every other face uses
(``_coerce.array_rank``, ``_coerce.elements_per_sample``). ``rank`` emits
``_coerce.array_rank_guard`` -- the method form's guard and its message --
failing with ``return -1;`` because it sits in a ``tp_init``, where a
``return NULL`` compiles and reports success, and releasing every array the
constructor already holds. ``elements_per_sample`` divides the ``<name>_len``
passed to ``create()``. A key no acquisition would read -- on a scalar, a
``rank`` a ``T[][]`` contradicts, an interleave on a 2-D or dtype-dispatch
array -- is refused at load, one ``error:`` line per row.

This file proves it three ways:

* **the build**: a compiled object whose ``create()`` records the lengths it
  is handed, so a 2-D array is refused with the method form's message, the
  interleave reaches the C, and repeated refusals change no refcount -- the
  second array is the guarded one, so the guard must release the first;
* **the render**: every constructor path that acquires an array, each with
  ``rank`` declared, holds one guard that fails as an ``initproc`` must and
  releases every array acquired before it -- derived from the rendered C,
  not listed here;
* **the refusals and the round trip**: each key a constructor array cannot
  honour stops the load, and a declared key survives ``save``/``load``,
  ``apply`` and ``jm script``.

GATE: a constructor array's rank and elements_per_sample are honoured or
refused, never accepted and ignored.
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

from _jmrun import run_cli  # noqa: E402

from just_makeit import _coerce  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._keys import INIT_PARAM_KEYS  # noqa: E402
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

#: `w` is unguarded and FIRST, so `h`'s guard bails out with `w_arr` held:
#: the refcount test fails unless the guard releases it. `iq` is the
#: defaulted (omittable) path, guarded and interleaved. `count` is a method
#: param with the same `rank`, so the two faces' messages are compared.
_FRAGMENT = """\
[hb]
arg_type = "float"
return_type = "float"
mutable = "false"
no_state = "false"
no_step = "true"

[[hb.state]]
name = "wn"
type = "size_t"
default = "0"

[[hb.state]]
name = "hn"
type = "size_t"
default = "0"

[[hb.state]]
name = "iqn"
type = "size_t"
default = "0"

[[hb.init_params]]
name = "w"
type = "float[]"

[[hb.init_params]]
name = "h"
type = "float[]"
rank = 1

[[hb.init_params]]
name = "iq"
type = "int16_t[]"
default = "[]"
rank = 1
elements_per_sample = 2

[[hb.methods]]
name = "count"
arg_type = "void"
return_type = "int"
params = [{name = "x", type = "float[]", rank = 1}]
impl = "(void)state; (void)x; return (int)x_len;"
"""

#: The scaffold's `create()` assigns each state field its default; the
#: fixture makes it record the lengths it was handed instead.
_DEFAULTS = "    obj->wn = 0;\n    obj->hn = 0;\n    obj->iqn = 0;\n"
_RECORD = (
    "    obj->wn = w_len;\n    obj->hn = h_len;\n    obj->iqn = iq_len;\n"
)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The `hb` object above, applied from its fragment and compiled."""
    if _SKIP:
        pytest.skip(_SKIP)
    dest = _apply_fragment(tmp_path_factory.mktemp("gh2004"), _FRAGMENT)
    core = dest / "native/src/hb/hb_core.c"
    text = core.read_text("utf-8")
    assert text.count(_DEFAULTS) == 1, "create()'s scaffold is not as expected"
    core.write_text(text.replace(_DEFAULTS, _RECORD), encoding="utf-8")
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
    """What *body* prints, or ``CRASH <rc>`` when the interpreter died.

    A bailout that releases an array twice can take the process down rather
    than return a wrong answer; either way the test fails on its own
    assertion.
    """
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys\nimport numpy as np\nfrom p.hb import Hb\n" + body,
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


#: Construct with *args* 300 times, collecting each ValueError, and report
#: whether the refcount of every array passed came back where it started.
_REFUSE_REPEATEDLY = """\
args = {args}
before = [sys.getrefcount(a) for a in args]
msgs = set()
for _ in range(300):
    try:
        Hb(*args)
        msgs.add("CONSTRUCTED")
    except ValueError as e:
        msgs.add(str(e))
after = [sys.getrefcount(a) for a in args]
print(sorted(msgs), before == after)
"""


def test_a_2d_array_is_refused_with_the_method_forms_message(built):
    got = _outcome(
        built,
        "try:\n"
        "    Hb(np.zeros(3, np.float32), np.zeros((4, 19), np.float32))\n"
        "    print('CONSTRUCTED')\n"
        "except ValueError as e:\n"
        "    print(e)\n"
        "try:\n"
        "    Hb(np.zeros(3, np.float32), np.zeros(5, np.float32))"
        ".count(np.zeros((2, 2), np.float32))\n"
        "except ValueError as e:\n"
        "    print(e)\n",
    )
    assert got.splitlines() == [
        "h must be a 1-D array",
        "x must be a 1-D array",
    ], got


def test_a_3d_array_is_refused_too(built):
    got = _outcome(
        built,
        "try:\n"
        "    Hb(np.zeros(3, np.float32), np.zeros((2, 3, 5), np.float32))\n"
        "    print('CONSTRUCTED')\n"
        "except ValueError as e:\n"
        "    print(e)\n",
    )
    assert got == "h must be a 1-D array", got


def test_a_1d_array_constructs_and_create_sees_its_length(built):
    got = _outcome(
        built,
        "o = Hb(np.zeros(3, np.float32), np.zeros(19, np.float32))\n"
        "print(o.get_wn(), o.get_hn(), o.get_iqn())\n",
    )
    assert got == "3 19 0", got


def test_the_interleave_reaches_create_in_samples(built):
    """8 `int16_t` elements at 2 per sample are 4 samples."""
    got = _outcome(
        built,
        "o = Hb(np.zeros(3, np.float32), np.zeros(19, np.float32),"
        " np.zeros(8, np.int16))\n"
        "print(o.get_iqn())\n",
    )
    assert got == "4", got


def test_refusing_the_second_array_releases_the_first(built):
    """`h` is guarded and `w` is already held when its guard bails out.

    `jm_array_arg` hands back the caller's own array, with a new reference,
    when it is already the right dtype and contiguous. So a bailout that
    forgot `w_arr` leaks one reference to `w` per call, and one that
    released `h_arr` twice takes `h` below its owner's count.
    """
    got = _outcome(
        built,
        _REFUSE_REPEATEDLY.format(
            args="(np.zeros(3, np.float32), np.zeros((4, 19), np.float32))"
        ),
    )
    assert got == "['h must be a 1-D array'] True", got


def test_refusing_an_omittable_array_releases_every_other(built):
    """The defaulted path's guard sits inside its branch, with two held."""
    got = _outcome(
        built,
        _REFUSE_REPEATEDLY.format(
            args="(np.zeros(3, np.float32), np.zeros(19, np.float32),"
            " np.zeros((2, 8), np.int16))"
        ),
    )
    assert got == "['iq must be a 1-D array'] True", got


# -- the render: every constructor path that acquires an array ---------------

_OBJ_HEAD = """\
[o]
arg_type = "float"
return_type = "float"
mutable = "false"
no_state = "true"
no_step = "true"
"""

#: One row per constructor path that acquires an array. Each carries `rank`
#: on the param named `g`; what its guard must release is read from the
#: rendered C (`_held_before`), not stated here.
_PATHS = {
    "plain, first": """\
[[o.init_params]]
name = "g"
type = "float[]"
rank = 1
""",
    "plain, after another array": """\
[[o.init_params]]
name = "a"
type = "double[]"

[[o.init_params]]
name = "g"
type = "float[]"
rank = 1
""",
    "dtype dispatch": """\
[[o.init_params]]
name = "a"
type = "double[]"

[[o.init_params]]
name = "g"
type = "float _Complex[]"
real_type = "float[]"
real_create_fn = "o_create_real"
rank = 1
""",
    "defaulted": """\
[[o.init_params]]
name = "a"
type = "double[]"

[[o.init_params]]
name = "b"
type = "int16_t[]"
default = "[]"

[[o.init_params]]
name = "g"
type = "float[]"
default = "[]"
rank = 1
""",
    "optional dispatch": """\
[[o.init_params]]
name = "a"
type = "double[]"

[[o.init_params]]
name = "b"
type = "int16_t[]"
default = "[]"

[[o.init_params]]
name = "g"
type = "float[]"
optional = true
create_fn = "o_create_with_g"
rank = 1
""",
    "beside a path": """\
[[o.init_params]]
name = "where"
type = "path"

[[o.init_params]]
name = "g"
type = "float[]"
rank = 1
""",
    "a named length (derived)": """\
[[o.init_params]]
name = "g"
type = "float[]"
derived = "n_taps"
rank = 1
""",
    "a view's own constructor": """\
[[o.init_params]]
name = "a"
type = "double[]"

[[o.views]]
class_name = "Alt"
create_fn = "o_create_alt"
init_params = [
    {name = "a", type = "double[]"},
    {name = "g", type = "float[]", rank = 1},
]
""",
}

#: The same object, in a module: its binding is a per-object fragment.
_MODULE_HEAD = '[module.m]\nobjects = ["o"]\n\n'

#: A view is a module-object feature, so it has no standalone row.
_FACES = [
    (path, in_module)
    for path in sorted(_PATHS)
    for in_module in (False, True)
    if in_module or "view" not in path
]


def _tp_inits(dest: Path) -> "list[str]":
    """Every generated ``tp_init`` body under ``native/src``."""
    bodies = []
    for c in sorted((dest / "native/src").rglob("*.c")):
        text = c.read_text("utf-8")
        for m in re.finditer(r"\n\w+_init\([^)]*kwds\)\n\{\n", text):
            bodies.append(text[m.end() : text.index("\n}\n", m.end())])
    return bodies


def _guards(body: str, name: str) -> "list[re.Match]":
    return list(
        re.finditer(
            rf"( *)if \(PyArray_NDIM\({name}_arr\) != (\d+)\) \{{\n"
            r"(?:.*\n){2}"
            r" *(?P<release>.*)\n *\}\n",
            body,
        )
    )


def _held_before(body: str, at: int, name: str) -> "set[str]":
    """Every array the ``tp_init`` holds at offset *at*, but *name*'s own.

    An array is held from its acquisition on; a 2-D extent's own guard is
    not an acquisition. Read off the rendered C, so a path added later is
    held to the same rule.
    """
    head = body[:at]
    # `x_arr =` then the converter (a plain or omittable array), or
    # `x_arr = _x_real` then a choice of two (a dtype dispatch)
    held = set(re.findall(r"\b(\w+_arr) =\s*jm_array_arg", head))
    held |= set(re.findall(r"\b(\w+_arr) = _\w+_real\n", head))
    held.discard(f"{name}_arr")
    return held


def _released_on_failed_acquisition(body: str, name: str) -> "set[str]":
    """What the ``if (!<name>_arr)`` bailout just above the guard releases.

    The guard runs one step later on the same path, so it must release at
    least this much -- a path borrow included, which is not an array.
    """
    m = re.search(rf"if \(!{name}_arr\) \{{([^}}]*)\}}", body)
    assert m, f"no failed-acquisition bailout for {name}"
    return set(re.findall(r"Py_X?DECREF\((\w+)\);", m.group(1)))


@pytest.mark.parametrize(
    "path,in_module",
    _FACES,
    ids=[f"{p}-{'module' if m else 'object'}" for p, m in _FACES],
)
def test_every_path_guards_as_an_initproc_must(tmp_path, path, in_module):
    head = (_MODULE_HEAD if in_module else "") + _OBJ_HEAD
    dest = _apply_fragment(tmp_path, head + "\n" + _PATHS[path])
    bodies = [b for b in _tp_inits(dest) if "g_obj" in b]
    assert bodies, f"{path}: no tp_init acquires g"
    for body in bodies:
        guards = _guards(body, "g")
        assert len(guards) == 1, f"{path}: {len(guards)} guards\n{body}"
        guard = guards[0]
        assert guard.group(2) == "1"
        release = guard.group("release")
        # an initproc: `return NULL` would compile and report success
        assert release.endswith("Py_DECREF(g_arr); return -1;"), release
        held = _held_before(body, guard.start(), "g")
        held |= _released_on_failed_acquisition(body, "g")
        for ref in held:
            assert f"DECREF({ref});" in release, (
                f"{path}: the guard leaks {ref}:\n{release}"
            )
        # before the length it protects, as the method form has it
        tail = body[guard.end() :]
        assert re.match(r"\s*(size_t )?g_len =", tail), tail[:200]
        # and it IS the method form's guard: the one emitter's text
        assert _coerce.array_rank_guard(
            "g",
            "g_arr",
            1,
            release[: -len("Py_DECREF(g_arr); return -1;")].strip(),
            fail="return -1;",
            indent=guard.group(1),
        ) == guard.group(0)


@pytest.mark.parametrize("path", sorted(_PATHS))
def test_an_undeclared_rank_grows_no_guard(tmp_path, path):
    """Flattening stays the default: the key is the opt-in."""
    head = (_MODULE_HEAD if "view" in path else "") + _OBJ_HEAD
    fragment = head + "\n" + _PATHS[path].replace("rank = 1\n", "")
    fragment = fragment.replace(", rank = 1}", "}")
    dest = _apply_fragment(tmp_path, fragment)
    for body in _tp_inits(dest):
        assert "PyArray_NDIM(g_arr) != 1" not in body, body


# -- the refusals --------------------------------------------------------------

#: (row, what the one `error:` line must say). Each is a key no acquisition
#: would read on that row.
_REFUSED = {
    "rank on a scalar": (
        {"name": "k", "type": "int", "rank": 1},
        "rank is read only by an array",
    ),
    "elements_per_sample on a scalar": (
        {"name": "k", "type": "int", "elements_per_sample": 2},
        "elements_per_sample is read only by an array",
    ),
    "rank = 0": (
        {"name": "g", "type": "float[]", "rank": 0},
        "declares rank = 0; it must be an integer of at least 1",
    ),
    "rank as a string": (
        {"name": "g", "type": "float[]", "rank": "1"},
        "declares rank = '1'; it must be an integer of at least 1",
    ),
    "elements_per_sample = 0": (
        {"name": "g", "type": "int16_t[]", "elements_per_sample": 0},
        "elements_per_sample = 0; it must be an integer of at least 1",
    ),
    "rank 1 on a T[][]": (
        {"name": "g", "type": "float[][]", "rank": 1},
        "rank = 1 can never hold on a 'float[][]'",
    ),
    "elements_per_sample on a T[][]": (
        {"name": "g", "type": "int16_t[][]", "elements_per_sample": 2},
        "elements_per_sample = 2 has no count to divide",
    ),
    "elements_per_sample on dtype dispatch": (
        {
            "name": "g",
            "type": "float _Complex[]",
            "real_type": "float[]",
            "real_create_fn": "o_create_real",
            "elements_per_sample": 2,
        },
        "one elements_per_sample = 2 cannot describe both",
    ),
}


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    _quiet(new_run, "p", root, c_prefix=None)
    return root


def _write_rows(root: Path, table: str, row: dict) -> None:
    """Put *row* on the init-params of `o`, a view of it, a method or a fn."""
    cfg = C.load(root)
    cfg["o"] = {
        "arg_type": "float",
        "return_type": "float",
        "no_state": "true",
        "no_step": "true",
    }
    if table == "init_params":
        cfg["o"]["init_params"] = [row]
    elif table == "view":
        cfg["o"]["views"] = [
            {"class_name": "Alt", "create_fn": "o_alt", "init_params": [row]}
        ]
    elif table == "method":
        cfg["o"]["methods"] = [
            {
                "name": "m",
                "arg_type": "void",
                "return_type": "int",
                "params": [row],
            }
        ]
    else:
        cfg.setdefault("module", {})["f"] = {
            "functions": [
                {"name": "fn", "return_type": "int", "params": [row]}
            ]
        }
    C.save(root, cfg)


@pytest.mark.parametrize("case", sorted(_REFUSED))
@pytest.mark.parametrize("table", ["init_params", "view"])
def test_a_key_a_constructor_cannot_honour_is_refused(tmp_path, case, table):
    root = _project(tmp_path)
    row, says = _REFUSED[case]
    _write_rows(root, table, dict(row))
    r = run_cli("apply", cwd=root)
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert r.returncode != 0 and len(errors) == 1, r.stderr
    assert says in errors[0], errors[0]
    assert "init_params]] " in errors[0] or "init_params]]:" in errors[0]


@pytest.mark.parametrize("table", ["method", "function"])
@pytest.mark.parametrize("key", ["rank", "elements_per_sample"])
def test_a_shape_key_on_a_scalar_param_is_refused_on_every_face(
    tmp_path, table, key
):
    """The method and function faces read the keys only on an array too."""
    root = _project(tmp_path)
    _write_rows(root, table, {"name": "k", "type": "int", key: 2})
    r = run_cli("apply", cwd=root)
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert r.returncode != 0 and len(errors) == 1, r.stderr
    assert f"{key} is read only by an array" in errors[0], errors[0]


@pytest.mark.parametrize(
    "row",
    [
        {"name": "g", "type": "float[][]", "rank": 2},
        {"name": "g", "type": "float[][]", "elements_per_sample": 1},
        {
            "name": "g",
            "type": "float _Complex[]",
            "real_type": "float[]",
            "real_create_fn": "o_create_real",
            "elements_per_sample": 1,
        },
    ],
    ids=["rank 2 on a T[][]", "1 on a T[][]", "1 on dtype dispatch"],
)
def test_a_key_stating_what_jm_already_does_loads(tmp_path, row):
    """Not a contradiction, so not refused: it is honoured as written.

    The 2-D guard already holds `rank = 2`, and one element per sample is
    true of a 2-D array's extents and of both dispatched element types.
    """
    root = _project(tmp_path)
    _write_rows(root, "init_params", dict(row))
    assert _quiet(C.load, root)["o"]["init_params"][0] == row


# -- the round trip ------------------------------------------------------------


def test_both_keys_are_init_param_vocabulary():
    assert {"rank", "elements_per_sample"} <= INIT_PARAM_KEYS


def test_apply_keeps_the_keys_and_warns_about_neither(tmp_path):
    """Accepted, persisted, and rendered by `apply` -- not merely tolerated.

    On main both drew `unknown init_param key` and did nothing.
    """
    root = _project(tmp_path)
    frag = tmp_path / "frag.toml"
    frag.write_text(
        _OBJ_HEAD
        + "\n"
        + """\
[[o.init_params]]
name = "g"
type = "int16_t[]"
rank = 1
elements_per_sample = 2
""",
        encoding="utf-8",
    )
    r = run_cli("apply", str(frag), cwd=root)
    assert r.returncode == 0, r.stderr
    assert "unknown" not in r.stderr, r.stderr
    row = C.load(root)["o"]["init_params"][0]
    assert row["rank"] == 1 and row["elements_per_sample"] == 2, row
    ext = (root / "native/src/o/o_ext.c").read_text("utf-8")
    assert "if (PyArray_NDIM(g_arr) != 1) {" in ext
    assert "size_t g_len = (size_t)PyArray_SIZE(g_arr) / 2;" in ext
    # and a second apply, from the manifest the first one wrote, agrees
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stderr
    assert (root / "native/src/o/o_ext.c").read_text("utf-8") == ext


def test_script_names_the_keys_it_cannot_spell(tmp_path):
    """`--init-param` has no spelling for either, as `--param` has none."""
    root = _project(tmp_path)
    _write_rows(
        root,
        "init_params",
        {
            "name": "g",
            "type": "int16_t[]",
            "rank": 1,
            "elements_per_sample": 2,
        },
    )
    r = run_cli("script", cwd=root)
    assert r.returncode == 0, r.stderr
    note = [ln for ln in r.stdout.splitlines() if "'g' declares" in ln]
    assert len(note) == 1, r.stdout
    assert " rank = 1" in note[0] and " elements_per_sample = 2" in note[0]
