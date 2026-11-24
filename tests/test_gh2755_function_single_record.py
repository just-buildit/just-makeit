"""A module function returns ONE record by value (gh-2755).

``single = true`` with ``result_fields`` (and ``record_name``) on a
``[[module.X.functions]]`` row was refused at load as a method key, and
``result_fields`` alone gave the list-of-records binding: a capacity param,
a ``list[tuple]`` and a ``-> Any`` stub. A C API whose natural shape is a
small struct returned by value -- doppler's CCSDS 415.1 scene helper -- had
no binding.

Now a function returns the method's single record (gh-244): the C function
returns the struct by value, the binding builds a named
``PyStructSequence`` from it through the same `_record` emitters a method
uses, the module creates and publishes the type at init, the package
re-exports it, and the stub declares the class and annotates the return.

GATE. The issue's own manifest, applied and compiled:

* the header prototype returns the struct and takes only the params, and
  the scaffolded stub returns a zeroed one;
* the call returns the module's registered type, with the declared field
  names and the values the C wrote, and ``isinstance`` holds;
* the stub declares the record class and annotates the function with it;
  the contract test asserts the type and runs; the C smoke test runs;
* ``jm function --single --record-name`` records the keys and ``jm script``
  replays them;
* the load refuses ``single`` with no fields, a record key with no
  ``single``, and a key a by-value record never reads; a method still
  refuses ``--single`` with no ``--result-field``.

The mypy face is ``test_gh2755_function_record_mypy.py`` (PROJECT_ENV).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli

from just_makeit import _config as C
from just_makeit import _record
from just_makeit import _render as R


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

#: The manifest the issue says doppler will write, with a body.
FRAGMENT = '''\
[module.ccsds]
objects = []

[[module.ccsds.functions]]
name = "ccsds415_scene"
doc = "The scene's derived numbers at carrier fc ..."
return_type = "dp_ccsds415_scene_t"
single = true
record_name = "Ccsds415Scene"
impl = """
dp_ccsds415_scene_t s;
s.chip_rate = fc / spc;
s.ratio = spc * 2.0;
s.start_ppm = start_hz / fc * 1e6;
s.rate_ppm_s = -1.5;
return s;
"""
result_fields = [
  { name = "chip_rate", type = "double" },
  { name = "ratio", type = "double" },
  { name = "start_ppm", type = "double" },
  { name = "rate_ppm_s", type = "double" },
]
params = [
  { name = "fc", type = "double" },
  { name = "spc", type = "double" },
  { name = "start_hz", type = "double" },
]
'''

#: The author's struct, in the sacred module header -- jm names it, the
#: author declares it, as for a single-record method.
STRUCT = """\
/** The scene's derived numbers. */
typedef struct {
  double chip_rate;  /**< Chips per second. */
  double ratio;
  double start_ppm;
  double rate_ppm_s;
} dp_ccsds415_scene_t;

"""

HEADER = "native/inc/p/ccsds/ccsds_core.h"
EXT = "native/src/ccsds/ccsds_ext.c"
PYI = "src/p/ccsds/ccsds.pyi"


def scene_project(base: Path, fragment: str = FRAGMENT) -> "tuple[Path, str]":
    """``p`` with module ``ccsds``, its struct, and *fragment* applied.

    Returns the project and apply's output when it failed or warned of a
    key it does not read (the load's old answer to ``single`` on a
    function), else ``""``.
    """
    r = run_cli("new", "p", "--no-c-prefix", cwd=base)
    assert r.returncode == 0, r.stderr
    dest = base / "p"
    r = run_cli("module", "ccsds", cwd=dest)
    assert r.returncode == 0, r.stdout + r.stderr
    header = dest / HEADER
    text = header.read_text("utf-8")
    anchor = "/* Declare module-level functions here. */"
    assert anchor in text, text
    header.write_text(text.replace(anchor, STRUCT + anchor), "utf-8")
    frag = base / "frag.toml"
    frag.write_text(fragment, encoding="utf-8")
    r = run_cli("apply", str(frag), cwd=dest)
    out = r.stdout + r.stderr
    return dest, (out if r.returncode or "unknown function key" in out else "")


@pytest.fixture(scope="module")
def applied(tmp_path_factory):
    """The issue's manifest, applied: every generated file, nothing built."""
    dest, err = scene_project(tmp_path_factory.mktemp("gh2755"))
    assert not err, err
    return dest


@pytest.fixture(scope="module")
def built(applied):
    """The applied module, compiled, its C test built."""
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


def _py(dest: Path, code: str) -> str:
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=dest,
        env={**os.environ, "PYTHONPATH": str(dest / "src")},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout.strip()


# -- the generated text --------------------------------------------------


def test_the_prototype_returns_the_struct(applied):
    text = (applied / HEADER).read_text("utf-8")
    assert (
        "dp_ccsds415_scene_t ccsds415_scene(double fc, double spc, "
        "double start_hz);"
    ) in text, text
    assert "max_results" not in text, text


def test_the_scaffold_stub_returns_a_zeroed_struct():
    stub = R.fn_c_stub(
        "f",
        [("x", "double")],
        "rec_t",
        result_fields=[{"name": "a", "type": "double"}],
        single=True,
    )
    assert "return (rec_t){0}; /* placeholder */" in stub, stub
    assert "*result" not in stub, stub


def test_the_binding_builds_one_record(applied):
    text = (applied / EXT).read_text("utf-8")
    wrapper = text[text.index("_bind_ccsds415_scene(") :]
    wrapper = wrapper[: wrapper.index("\n}\n")]
    assert (
        "dp_ccsds415_scene_t _r = ccsds415_scene(fc, spc, start_hz);"
        in wrapper
    ), wrapper
    assert "PyStructSequence_New(_jm_fnrec_ccsds415_scene_type)" in wrapper
    assert "PyMem_Malloc" not in wrapper, wrapper
    assert '"p.ccsds.Ccsds415Scene"' in text, text


def test_the_module_registers_the_type(applied):
    text = (applied / EXT).read_text("utf-8")
    init = text[text.index("PyInit_ccsds") :]
    assert "PyStructSequence_NewType(&_jm_fnrec_ccsds415_scene_desc)" in init
    assert (
        'PyModule_AddObject(m, "Ccsds415Scene",'
        " (PyObject *)_jm_fnrec_ccsds415_scene_type)"
    ) in init, init


def test_the_stub_declares_the_record(applied):
    text = (applied / PYI).read_text("utf-8")
    assert "class Ccsds415Scene(tuple[float, float, float, float]):" in text
    assert (
        "def ccsds415_scene(fc: float, spc: float, start_hz: float)"
        " -> Ccsds415Scene:"
    ) in text, text
    # The struct's own member doc reaches the field (gh-1300's source).
    assert "Chips per second." in text, text
    assert "from typing import final" in text, text


def test_the_package_reexports_the_record(applied):
    text = (applied / "src/p/ccsds/__init__.py").read_text("utf-8")
    assert "Ccsds415Scene" in text, text


def test_the_manifest_keeps_the_keys(applied):
    (fn,) = C.module_functions(C.load(applied), "ccsds")
    assert fn["single"] is True
    assert fn["record_name"] == "Ccsds415Scene"
    # A capacity is a list's; a record by value has no buffer to size.
    assert "max_results" not in fn, fn


def test_the_contract_test_asserts_the_type(applied):
    text = (applied / "src/p/ccsds/tests/test_ccsds_functions.py").read_text(
        "utf-8"
    )
    assert "self.assertIsInstance(result, module.Ccsds415Scene)" in text, text


def test_a_method_and_function_share_one_registration_shape():
    fn = {
        "name": "f",
        "single": True,
        "record_name": "R",
        "result_fields": [{"name": "n", "type": "int"}],
    }
    (fn_reg,) = _record.function_registrations([fn])
    (m_reg,) = _record.registrations([fn], "Obj")
    # One public name and shape: the aggregator aliases the second claim.
    assert (fn_reg.name, fn_reg.shape) == (m_reg.name, m_reg.shape)


# -- the compiled module -------------------------------------------------


def test_the_call_returns_the_registered_record(built):
    out = _py(
        built,
        "import p.ccsds as m\n"
        "r = m.ccsds415_scene(1e6, 4.0, start_hz=10.0)\n"
        "print(type(r).__module__, type(r).__name__)\n"
        "print(isinstance(r, m.Ccsds415Scene), type(r) is m.Ccsds415Scene)\n"
        "print(r.chip_rate, r.ratio, r.start_ppm, r.rate_ppm_s)\n"
        "print(tuple(r) == (r.chip_rate, r.ratio, r.start_ppm,"
        " r.rate_ppm_s))\n"
        "print(repr(r))\n",
    )
    assert out.splitlines() == [
        "p.ccsds Ccsds415Scene",
        "True True",
        "250000.0 8.0 10.0 -1.5",
        "True",
        "p.ccsds.Ccsds415Scene(chip_rate=250000.0, ratio=8.0,"
        " start_ppm=10.0, rate_ppm_s=-1.5)",
    ], out


def test_the_record_carries_its_docs(built):
    out = _py(
        built,
        "import p.ccsds as m\n"
        "print(m.Ccsds415Scene.chip_rate.__doc__)\n"
        "print(m.ccsds415_scene.__doc__.splitlines()[0])\n",
    )
    assert out.splitlines() == [
        "Chips per second.",
        "The scene's derived numbers at carrier fc ...",
    ], out


def test_the_contract_test_runs(built):
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "-v",
            "p.ccsds.tests.test_ccsds_functions",
        ],
        cwd=built,
        env={**os.environ, "PYTHONPATH": str(built / "src")},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, r.stderr
    line = next(
        ln
        for ln in r.stderr.splitlines()
        if "test_ccsds415_scene_contract" in ln
    )
    assert line.endswith("ok"), line


def test_the_c_smoke_test_runs(built):
    r = subprocess.run(
        ["ctest", "--test-dir", str(built / "build"), "-R", "ccsds"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "100% tests passed" in r.stdout, r.stdout


# -- the command line and the replay ------------------------------------


def test_the_cli_spells_it_and_script_replays_it(tmp_path):
    assert run_cli("new", "p", "--no-c-prefix", cwd=tmp_path).returncode == 0
    dest = tmp_path / "p"
    assert run_cli("module", "ccsds", cwd=dest).returncode == 0
    r = run_cli(
        "function",
        "scene",
        "--module",
        "ccsds",
        "--return-type",
        "scene_t",
        "--single",
        "--record-name",
        "Scene",
        "--record-doc",
        "The scene.",
        "--result-field",
        "ratio:double:The ratio.",
        "--param",
        "fc:double",
        cwd=dest,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    (fn,) = C.module_functions(C.load(dest), "ccsds")
    assert fn["single"] is True
    assert (fn["record_name"], fn["record_doc"]) == ("Scene", "The scene.")
    stub = (dest / "native/src/ccsds/scene.c").read_text("utf-8")
    assert "return (scene_t){0}; /* placeholder */" in stub, stub
    r = run_cli("script", cwd=dest)
    assert r.returncode == 0, r.stderr
    for flag in (
        "--single",
        "--record-name Scene",
        '--record-doc "The scene."',
    ):
        assert flag in r.stdout, r.stdout


# -- refusals -----------------------------------------------------------

_PLAIN = """\
[module.ccsds]
objects = []

[[module.ccsds.functions]]
name = "f"
return_type = "{rt}"
{extra}
params = [{{ name = "x", type = "double" }}]
"""

_FIELDS = 'result_fields = [{ name = "ratio", type = "double" }]'

#: (extra keys, the return type, what the refusal says).
REFUSED = [
    (
        "single = true",
        "dp_ccsds415_scene_t",
        "single = true (--single) needs result_fields",
    ),
    (
        f'record_name = "R"\n{_FIELDS}',
        "dp_ccsds415_scene_t",
        "record_name (--record-name) names the single record's type",
    ),
    (
        f"single = true\ncheck_return = true\n{_FIELDS}",
        "dp_ccsds415_scene_t",
        "check_return has no effect on a single = true record",
    ),
    (
        f"single = true\nmax_results = 8\n{_FIELDS}",
        "dp_ccsds415_scene_t",
        "max_results has no effect on a single = true record",
    ),
    (
        f"single = true\n{_FIELDS}",
        "double",
        "must be the C struct that the --single record",
    ),
]


@pytest.mark.parametrize(
    ("extra", "rt", "says"), REFUSED, ids=[s for _, _, s in REFUSED]
)
def test_a_record_it_cannot_honour_is_refused(tmp_path, extra, rt, says):
    _, err = scene_project(tmp_path, _PLAIN.format(rt=rt, extra=extra))
    assert says in err, err


@pytest.mark.parametrize(
    "argv",
    [
        ("method", "o", "m", "--return-type", "rec_t", "--single"),
        (
            "function",
            "f",
            "--module",
            "ccsds",
            "--return-type",
            "rec_t",
            "--single",
        ),
    ],  # fmt: skip
    ids=["method", "function"],
)
def test_single_without_fields_is_refused_in_the_loads_words(tmp_path, argv):
    """Before the return-type check, which would call the struct a bad
    scalar: the command and the load refuse it in one sentence."""
    assert run_cli("new", "p", "--no-c-prefix", cwd=tmp_path).returncode == 0
    dest = tmp_path / "p"
    assert run_cli("module", "ccsds", cwd=dest).returncode == 0
    assert run_cli("object", "o", cwd=dest).returncode == 0
    r = run_cli(*argv, cwd=dest)
    assert r.returncode != 0
    assert "single = true (--single) needs result_fields" in r.stderr, (
        r.stdout + r.stderr
    )


def test_the_load_reads_every_record_key():
    """No key of the issue's row is one jm does not read on a function.

    Asked directly, not through apply's warning, which is printed once per
    process and so is silent for every project after the first.
    """
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        import tomli as tomllib  # type: ignore[no-redef]

    from just_makeit import _keys

    cfg = tomllib.loads(FRAGMENT)
    assert [u.message() for u in _keys.unknown_keys(cfg)] == []
    assert C.manifest_type_errors(cfg) == []
    assert C.function_record_errors(cfg) == []


# -- one public name, one shape, from every direction (gh-1268) --------

_SCENE = ("--return-type", "scene_t", "--single", "--record-name", "Scene")

_FN_SCENE = (
    "function", "scene", "--module", "ccsds", *_SCENE,
    "--result-field", "ratio:double", "--param", "x:double",
)  # fmt: skip

_FN_SCENE2 = (
    "function", "scene2", "--module", "ccsds", *_SCENE,
    "--result-field", "n:int", "--param", "x:double",
)  # fmt: skip

_METHOD_SCENE = (
    "method", "o", "m", "--module", "ccsds", *_SCENE,
    "--result-field", "n:int",
)  # fmt: skip


def _module_project(tmp_path: Path) -> Path:
    assert run_cli("new", "p", "--no-c-prefix", cwd=tmp_path).returncode == 0
    dest = tmp_path / "p"
    for argv in (("module", "ccsds"), ("object", "o", "--module", "ccsds")):
        r = run_cli(*argv, cwd=dest)
        assert r.returncode == 0, r.stdout + r.stderr
    return dest


#: (the record declared first, the clashing one) -- every direction.
CLASHES = [
    (_FN_SCENE, _FN_SCENE2),
    (_FN_SCENE, _METHOD_SCENE),
    (_METHOD_SCENE, _FN_SCENE),
]


@pytest.mark.parametrize(
    ("first", "second"),
    CLASHES,
    ids=["function-then-function", "function-then-method", "method-then-fn"],
)
def test_a_clashing_record_name_is_refused_in_one_line(
    tmp_path, first, second
):
    dest = _module_project(tmp_path)
    r = run_cli(*first, cwd=dest)
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli(*second, cwd=dest)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "Traceback" not in out, out
    assert "error: two records share the public name 'Scene'" in out, out


def test_a_clashing_manifest_is_refused_at_load():
    rec = {"single": True, "record_name": "Scene", "return_type": "s_t"}
    a = [{"name": "a", "type": "double"}]
    n = [{"name": "n", "type": "int"}]
    cfg = {
        "module": {
            "m": {
                "objects": [],
                "functions": [
                    {**rec, "name": "f", "result_fields": a},
                    {**rec, "name": "g", "result_fields": n},
                ],
            }
        }
    }
    (err,) = C.function_record_errors(cfg)
    assert err.startswith("[module.m] two records share the public"), err
