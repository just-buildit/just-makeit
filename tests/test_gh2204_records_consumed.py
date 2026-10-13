"""A list-of-records method re-offers the input its kernel left (gh-2204).

A list-of-records method -- ``result_fields`` with neither ``single`` nor
``record_dtype`` -- binds a kernel whose contract is::

    size_t <c>_<m>(state, const T *in, size_t n_in, ..., rec_t *result,
                   size_t max_results);

gh-2184 let a header-declared ``<c>_<m>_max_out`` size the result for a
kernel that can bound its output. A detector cannot: its hit count depends
on the data, so it stops when ``result`` is full and records how much input
it took (doppler's ``CorrDetector`` / ``CorrDetector2D`` / ``AcqEngine``,
doppler#1992, doppler#1995). The binding made ONE call and returned, so a
push of 100 samples into a kernel that stops at 64 came back with 64
records and samples 64..99 were never offered to the C.

``consumed_fn = "<fn>"`` names ``size_t <fn>(const <state> *)``, the input
the last call took. The binding calls the kernel, appends its records, asks
``<fn>`` and offers the rest of the input block until none is left; params
ride along unchanged on every call. With a declared ``_max_out`` too, each
call is sized for the input still left.

GATE, built and called (cmake, ``-Wall -Wextra -Werror``), for a module
object (doppler's shape) and a standalone object:

* the repro kernel returns every record -- 100 and 1000, not 64 -- in call
  order, each input element seen exactly once, and a param on every call;
* with a declared capacity each call is sized for the REMAINING input;
* a call that takes none of what is left raises ``RuntimeError`` naming the
  method instead of spinning, and so does one claiming more than it got; an
  empty input taking nothing is not an error;
* the key is refused at load, and by ``jm method`` before it writes, on a
  method with no array input and on one that does not return a list of
  records;
* a module object's fragment rendered before the key was declared is sacred,
  so ``apply`` reports it, and a regenerated one is quiet;
* ``jm script`` re-emits the flag.

Which shapes must have a row is the source's answer:
`test_every_consumed_site_is_reached_by_a_row` reads every place
`make_methods_ctx` builds the re-offering body and refuses one no row
reaches.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest
from _compilers import default_cc
from _jminc import INC_ROOT
from _jmrun import run_cli

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _csym as CSYM  # noqa: E402
from just_makeit._context import _methods as M  # noqa: E402
from just_makeit._object import _load_doc_blocks  # noqa: E402

_TOOLCHAIN = bool(shutil.which("cmake")) and default_cc() is not None

#: What a fixed buffer holds: the manifest's `max_results` default.
FIXED = 64

#: object -> module. Both layouts: doppler's detectors are module objects.
OBJECTS = {"hits": "det", "solo": None}

#: `div_t` is the record: `{int quot; int rem;}`, from <stdlib.h>, so no
#: header has to declare a struct for it.
_DIV = (
    'return_type = "div_t"\n'
    'result_fields = [{name = "quot", type = "int"}, '
    '{name = "rem", type = "int"}]'
)


def _stop_at_full(rem: str) -> str:
    """The repro kernel: one record per input element, stopping when the
    buffer is full and recording how much input it took. `quot` is the
    element's index in everything this object was ever offered, so the
    records of a call that re-offered the rest continue where it stopped."""
    return (
        "(void)in;\n"
        "    size_t j = 0;\n"
        "    for (; j < n_in && j < max_results; j++) {\n"
        "        result[j].quot = (int)(state->seen + j);\n"
        f"        result[j].rem = {rem};\n"
        "    }\n"
        "    state->used = j;\n"
        "    state->seen += j;\n"
        "    return j;"
    )


class Row(NamedTuple):
    """One list-of-records method declaring `consumed_fn`."""

    name: str
    keys: str
    impl: str
    #: the consumed function's body, over `state`
    consumed: str
    #: the `_max_out` capacity's body, or "" for none
    capacity: str = ""


ROWS = [
    # an input, no params: doppler's push(x)
    Row(
        "push",
        f'arg_type = "float"\n{_DIV}',
        _stop_at_full("(int)in[j]"),
        "return state->used;",
    ),
    # an input and a param: the param rides along on every call. `nogil`
    # releases the GIL around each one.
    Row(
        "scan",
        f'arg_type = "float"\n{_DIV}\nnogil = true\n'
        'params = [{name = "k", type = "int"}]',
        _stop_at_full("k + (int)in[j]"),
        "return state->used;",
    ),
    # both declared: each call is sized from `_max_out` for the input LEFT.
    # Half of it, plus one, so a capacity asked for the whole input every
    # time would size every call the same; `rem` records what each got.
    Row(
        "both",
        f'arg_type = "float"\n{_DIV}',
        _stop_at_full("(int)max_results"),
        "return state->used;",
        "return n_in / 2 + 1;",
    ),
    # takes nothing: must raise, not spin. Re-offered anyway -- the guard
    # missing -- it gives up the rest on the fourth call, so the test fails
    # rather than hangs.
    Row(
        "stall",
        f'arg_type = "float"\n{_DIV}',
        "(void)in; (void)result; (void)max_results;\n"
        "    state->calls += 1;\n"
        "    state->used = state->calls > 3 ? n_in : 0;\n"
        "    return 0;",
        "return state->used;",
    ),
    # claims one more than it was given: must raise, not step past the end.
    # Re-offered anyway, it claims all that is left (`in` is never read).
    Row(
        "greedy",
        f'arg_type = "float"\n{_DIV}',
        "(void)in;\n"
        "    size_t j = 0;\n"
        "    for (; j < n_in && j < max_results; j++) {\n"
        "        result[j].quot = (int)j;\n"
        "        result[j].rem = 0;\n"
        "    }\n"
        "    state->calls += 1;\n"
        "    state->used = state->calls == 1 ? j + 1 : n_in;\n"
        "    return j;",
        "return state->used;",
    ),
]


def _consumed_name(stem: str, method: str) -> str:
    return f"{stem}_{method}_used"


def _object_toml(obj: str) -> str:
    out = [
        f"[{obj}]\n"
        'arg_type = "void"\nreturn_type = "void"\nno_step = "true"\n'
        f'[[{obj}.state]]\nname = "used"\ntype = "size_t"\ndefault = "0"\n'
        f'[[{obj}.state]]\nname = "seen"\ntype = "size_t"\ndefault = "0"\n'
        f'[[{obj}.state]]\nname = "calls"\ntype = "size_t"\ndefault = "0"\n'
    ]
    out += [
        f'[[{obj}.methods]]\nname = "{r.name}"\n{r.keys}\n'
        f'consumed_fn = "{_consumed_name(obj, r.name)}"\n'
        f'impl = """{r.impl}"""\n'
        for r in ROWS
    ]
    return "\n".join(out)


def _fragment() -> str:
    out = [_object_toml(obj) for obj in OBJECTS]
    mods: dict = {}
    for obj, mod in OBJECTS.items():
        if mod:
            mods.setdefault(mod, []).append(obj)
    for mod, objs in mods.items():
        names = ", ".join(f'"{o}"' for o in objs)
        out.append(f"[module.{mod}]\nobjects = [{names}]\n")
    return "\n".join(out)


def _declare(root: Path, obj: str, rows=ROWS) -> None:
    """What the AUTHOR writes: each consumed function (and capacity) in the
    sacred header, and its body in `_core.c`."""
    cfg = C.load(root)
    stem = CSYM.stem(cfg, obj)
    decls, defs = [], []
    for r in rows:
        name = _consumed_name(obj, r.name)
        sig = f"{name}(const {stem}_state_t *state)"
        decls.append(f"size_t {sig};\n")
        defs.append(f"\nsize_t\n{sig}\n{{\n    {r.consumed}\n}}\n")
        if r.capacity:
            cap = (
                f"{stem}_{r.name}_max_out({stem}_state_t *state, size_t n_in)"
            )
            decls.append(f"size_t {cap};\n")
            defs.append(
                f"\nsize_t\n{cap}\n{{\n    (void)state;\n    {r.capacity}\n}}\n"
            )
    h = root / INC_ROOT / obj / f"{obj}_core.h"
    text = h.read_text(encoding="utf-8")
    cut = text.rindex("#ifdef __cplusplus")
    h.write_text(text[:cut] + "".join(decls) + text[cut:], encoding="utf-8")
    c = root / "native" / "src" / obj / f"{obj}_core.c"
    c.write_text(
        c.read_text(encoding="utf-8") + "".join(defs), encoding="utf-8"
    )


def _fragment_path(root: Path, obj: str) -> Path:
    mod = OBJECTS[obj]
    return root / "native" / "src" / mod / f"{mod}_ext_{obj}.c"


def _add_consumed_fn(root: Path, method: str, fn: str) -> None:
    """What the AUTHOR does to an existing method: add the key by hand, to
    whichever manifest file holds the method's row."""
    row = f'name = "{method}"'
    toml = next(
        f
        for f in [root / "just-makeit.toml", *root.rglob("objects/*.toml")]
        if row in f.read_text(encoding="utf-8")
    )
    text = toml.read_text(encoding="utf-8")
    at = text.index(row) + len(row)
    toml.write_text(
        text[:at] + f'\nconsumed_fn = "{fn}"' + text[at:], encoding="utf-8"
    )


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("gh2204")
    root = base / "p"
    r = run_cli("new", "p", str(root))
    assert r.returncode == 0, r.stdout + r.stderr
    frag = base / "frag.toml"
    frag.write_text(_fragment(), encoding="utf-8")
    r = run_cli("apply", str(frag), cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    for obj in OBJECTS:
        _declare(root, obj)
    # The header now declares the capacities, which a module object's
    # sacred fragment did not ask when it was first rendered (gh-2184's
    # remedy): delete it and re-apply.
    for obj, mod in OBJECTS.items():
        if mod:
            _fragment_path(root, obj).unlink()
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    return root


# -- which shapes must have a row: every re-offering site -------------------


def _consumed_sites() -> "list[tuple[int, int]]":
    """Each place `make_methods_ctx` builds a re-offering body, as the line
    span of its `_records_consumed_c(...)` call -- read off the source."""
    path = SRC / "just_makeit" / "_context" / "_methods.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "make_methods_ctx"
    )
    return [
        (n.lineno, n.end_lineno or n.lineno)
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_records_consumed_c"
    ]


def test_every_consumed_site_is_reached_by_a_row(project, monkeypatch):
    """A records shape added with its own re-offering body fails here until
    a row builds it, and so is held to every check below."""
    sites = _consumed_sites()
    # Armed: an input alone, and an input beside params.
    assert len(sites) >= 2, sites
    reached: set = set()
    real = M._records_consumed_c

    def spy(*a, **k):
        reached.add(sys._getframe(1).f_lineno)
        return real(*a, **k)

    monkeypatch.setattr(M, "_records_consumed_c", spy)
    cfg = C.load(project)
    M.make_methods_ctx(
        "solo",
        "Solo",
        C.methods(cfg, "solo"),
        pkg="p",
        doc_blocks=_load_doc_blocks(project, "solo", cfg),
        records=C.records(cfg, "solo"),
        csym=CSYM.stem(cfg, "solo"),
    )
    missing = [
        s for s in sites if not any(s[0] <= ln <= s[1] for ln in reached)
    ]
    assert not missing, f"re-offering site(s) no row reaches: {missing}"


# -- built and called ---------------------------------------------------------

_PROBE = r"""
import json, sys
import numpy as np
sys.path.insert(0, sys.argv[1])
from p.det import Hits
from p import Solo
out = {}
for obj, Cls in {"hits": Hits, "solo": Solo}.items():
    got = {}
    o = Cls()
    for n in (100, 1000):
        got[f"push{n}"] = o.push(np.ones(n, np.float32))
    got["scan"] = Cls().scan(np.ones(300, np.float32), 7)
    got["both"] = Cls().both(np.ones(100, np.float32))
    got["empty"] = Cls().stall(np.ones(0, np.float32))
    for m in ("stall", "greedy"):
        try:
            getattr(Cls(), m)(np.ones(5, np.float32))
            got[m] = "no error"
        except RuntimeError as exc:
            got[m] = str(exc)
    out[obj] = got
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def probe(project) -> dict:
    if not _TOOLCHAIN:
        pytest.skip("needs cmake + a C compiler")
    build = project / "build"
    log = ""
    for args in (
        [
            "cmake",
            "-S",
            str(project),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
            "-DCMAKE_C_FLAGS=-Wall -Wextra -Werror",
        ],
        # Everything: the extensions, and the C tests whose symbol table
        # links each consumed function too (gh-1361).
        ["cmake", "--build", str(build), "--parallel", "4"],
    ):
        r = subprocess.run(args, capture_output=True, text=True, timeout=900)
        log += r.stdout + r.stderr
        assert r.returncode == 0, log[-6000:]
    p = subprocess.run(
        [sys.executable, "-c", _PROBE, str(project / "src")],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert p.returncode == 0, p.stdout + p.stderr[-4000:]
    return json.loads(p.stdout)


@pytest.mark.parametrize("obj", list(OBJECTS))
def test_every_record_comes_back_in_call_order(probe, obj):
    """The repro: 100 elements due 100 records, not the first buffer's 64.
    The second push continues the first's count, so each element was
    offered exactly once and nothing was offered twice."""
    first, second = probe[obj]["push100"], probe[obj]["push1000"]
    assert len(first) == 100, f"{obj}: {len(first)} of 100 records"
    assert len(second) == 1000, f"{obj}: {len(second)} of 1000 records"
    assert [q for q, _r in first + second] == list(range(1100))


@pytest.mark.parametrize("obj", list(OBJECTS))
def test_a_param_rides_along_on_every_call(probe, obj):
    rows = probe[obj]["scan"]
    assert len(rows) == 300, f"{obj}: {len(rows)} of 300"
    assert {r for _q, r in rows} == {8}, "k = 7 reached every call"


@pytest.mark.parametrize("obj", list(OBJECTS))
def test_a_declared_capacity_sizes_each_call_for_the_rest(probe, obj):
    """`_max_out(n_in) = n_in / 2 + 1`, asked with what is LEFT: 51 of 100,
    then 25 of 49, 13 of 24, ..."""
    rows = probe[obj]["both"]
    assert len(rows) == 100, f"{obj}: {len(rows)} of 100"
    want, left = [], 100
    while left:
        cap = left // 2 + 1
        took = min(cap, left)
        want += [cap] * took
        left -= took
    assert [r for _q, r in rows] == want


@pytest.mark.parametrize("obj", list(OBJECTS))
def test_no_progress_raises_naming_the_method(probe, obj):
    cls = obj.title()
    msg = probe[obj]["stall"]
    assert msg.startswith(f"{cls}.stall: "), msg
    assert "made no progress" in msg, msg
    assert "took 0 of the 5" in msg, msg
    # Nothing offered, nothing taken: not an error.
    assert probe[obj]["empty"] == []


@pytest.mark.parametrize("obj", list(OBJECTS))
def test_taking_more_than_offered_raises(probe, obj):
    msg = probe[obj]["greedy"]
    assert msg.startswith(f"{obj.title()}.greedy: "), msg
    assert "took 6 of the 5" in msg and "more than it was given" in msg, msg


# -- refused at load, and before `jm method` writes ---------------------------

_REFUSED = {
    # no array input to re-offer
    "no input": f'arg_type = "void"\n{_DIV}',
    # one record, not a list of them
    "single": f'arg_type = "float"\n{_DIV}\nsingle = true',
    # a record ARRAY: its buffer is sized for the call
    "record array": (
        'arg_type = "float"\nreturn_type = "div_t"\nvariable_output = true\n'
        'record_dtype = "div_t"\n'
        'result_fields = [{name = "quot", type = "int"}]'
    ),
    # no records at all
    "plain": 'arg_type = "float"\nreturn_type = "float"',
}

_WHY = {
    "no input": "has no array input",
    "single": "does not return a list of records",
    "record array": "does not return a list of records",
    "plain": "does not return a list of records",
}


@pytest.mark.parametrize("case", list(_REFUSED))
def test_refused_at_load(tmp_path, case):
    root = tmp_path / "p"
    r = run_cli("new", "p", str(root))
    assert r.returncode == 0, r.stdout + r.stderr
    frag = tmp_path / "frag.toml"
    frag.write_text(
        '[o]\narg_type = "void"\nreturn_type = "void"\n'
        'no_state = "true"\nno_step = "true"\n'
        f'[[o.methods]]\nname = "m"\n{_REFUSED[case]}\n',
        encoding="utf-8",
    )
    r = run_cli("apply", str(frag), cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    _add_consumed_fn(root, "m", "o_m_used")
    # `script` only reads the manifest -- it renders nothing and replays no
    # command that could refuse instead -- so this is the loader's answer.
    r = run_cli("script", cwd=root)
    out = " ".join((r.stdout + r.stderr).split())
    assert r.returncode != 0, out
    assert "consumed_fn 'o_m_used'" in out and _WHY[case] in out, out


_CLI = {
    "no input": ["--arg-type", "void", "--return-type", "div_t",
                 "--result-field", "quot:int"],
    "single": ["--arg-type", "float", "--return-type", "div_t",
               "--result-field", "quot:int", "--single"],
    "plain": ["--arg-type", "float", "--return-type", "float"],
    "not a name": ["--arg-type", "float", "--return-type", "div_t",
                   "--result-field", "quot:int"],
}  # fmt: skip


@pytest.mark.parametrize("case", list(_CLI))
def test_jm_method_refuses_before_writing(tmp_path, case):
    root = tmp_path / "p"
    r = run_cli("new", "p", str(root))
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli("object", "o", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    before = {
        f: f.read_bytes() for f in sorted(root.rglob("*")) if f.is_file()
    }
    fn = "1o_used" if case == "not a name" else "o_m_used"
    r = run_cli("method", "o", "m", *_CLI[case], "--consumed-fn", fn, cwd=root)
    out = " ".join((r.stdout + r.stderr).split())
    assert r.returncode != 0, out
    why = _WHY.get(case, "is not a C function name")
    assert f"consumed_fn '{fn}'" in out and why in out, out
    # Refused up front, not written and then undone by a later load.
    assert "update" not in r.stdout, r.stdout
    after = {f: f.read_bytes() for f in sorted(root.rglob("*")) if f.is_file()}
    assert after == before, "a refused method wrote the tree"


def test_jm_method_accepts_and_script_replays_it(tmp_path):
    root = tmp_path / "p"
    r = run_cli("new", "p", str(root))
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli("object", "o", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli(
        "method", "o", "push", "--arg-type", "float", "--return-type",
        "div_t", "--result-field", "quot:int", "--consumed-fn", "o_push_used",
        cwd=root,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    # Told what to write, since jm declares nothing for it.
    assert "size_t o_push_used(const" in r.stdout, r.stdout
    m = next(m for m in C.methods(C.load(root), "o") if m["name"] == "push")
    assert m["consumed_fn"] == "o_push_used"
    r = run_cli("script", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "--consumed-fn o_push_used" in r.stdout, r.stdout


# -- a module fragment rendered before the key --------------------------------

_STALE = "the manifest declares consumed_fn and this fragment calls the"


def test_a_stale_module_fragment_is_reported(tmp_path):
    """doppler's path: the detectors' fragments exist; the key is added to
    the manifest afterwards. `apply` cannot re-render a sacred fragment, so
    it must SAY the method still drops input -- and stop saying it once the
    fragment is regenerated."""
    root = tmp_path / "p"
    r = run_cli("new", "p", str(root))
    assert r.returncode == 0, r.stdout + r.stderr
    frag = tmp_path / "frag.toml"
    frag.write_text(
        _object_toml("hits").replace('consumed_fn = "hits_push_used"\n', "")
        + '\n[module.det]\nobjects = ["hits"]\n',
        encoding="utf-8",
    )
    r = run_cli("apply", str(frag), cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    _declare(root, "hits")
    _add_consumed_fn(root, "push", "hits_push_used")
    assert C.methods(C.load(root), "hits")[0]["consumed_fn"]
    stale = run_cli("apply", cwd=root)
    assert stale.returncode == 0, stale.stdout + stale.stderr
    said = " ".join((stale.stdout + stale.stderr).split())
    assert _STALE in said and "push:" in said, said
    # Only the method that changed: its siblings were rendered with the key.
    assert "scan:" not in said, said
    # The progress guard raises, and it is jm's: no result shape the
    # manifest declared is missing.
    assert "result shape needs" not in said, said
    # `both` re-offers too, and was rendered before its capacity was
    # declared: told so as a list of records (gh-2184), not as an ndarray's
    # "negative dimension".
    assert "both: the header declares this method's" in said, said
    assert "negative dimension" not in said, said
    _fragment_path(root, "hits").unlink()
    fresh = run_cli("apply", cwd=root)
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    assert _STALE not in " ".join((fresh.stdout + fresh.stderr).split())
    text = _fragment_path(root, "hits").read_text(encoding="utf-8")
    assert "hits_push_used(self->handle)" in text
