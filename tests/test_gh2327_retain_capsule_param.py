"""gh-2327 / gh-2328: a capsule whose pointer the C side keeps, kept alive.

gh-2327. A method param declared ``capsule = "<name>"`` is borrowed for the
call (gh-432). A kernel that STORES the pointer -- doppler's
``set_telemetry`` -- then holds it past the call while nothing holds the
Python object that owns it::

    pool.set_telemetry(Telemetry(1 << 20), "p")   # Telemetry freed here
    pool.push(x)                                  # writes into freed memory

``retain = true`` on the param makes the binding keep what it was passed
(`just_makeit._retain`): replaced by the next successful call, cleared by
``None``, kept as it was by a refused call, released by dealloc.

gh-2328. What the caller passes may be the bare capsule (``t._capsule``),
which held no reference to the object that lent it, so keeping the capsule
kept nothing. A lent capsule now holds its lender (``jm_capsule_lend``).

The gates, cheapest first:

1. the load refusals -- a ``retain`` that would keep nothing;
2. every method shape gh-2259's roster names (`SHAPES`), given a retained
   param, keeps it after each kernel call and after the kernel's own
   refusal, with nothing but a refusal between them;
3. a hand-owned module fragment rendered before the key is NAMED by apply;
4. compiled: the whole roster builds, and at runtime the owner's refcount
   says it is kept, kept through a refusal, replaced, detached, released --
   on a standalone object, a module object and a view -- and a bare
   capsule keeps its lender.
"""

# gh-1591: this file's hand-written C spells jm's bare derived names, so its
# projects opt out of the prefix `jm new` now defaults to.

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli
from test_gh2259_batch_method_faces import SHAPES

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _composer  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit import _retain  # noqa: E402

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

#: The foreign type every retained param here takes.
_TT_H = (
    "#ifndef TT_H\n#define TT_H\ntypedef struct tt { int k; } tt_t;\n#endif\n"
)


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


def _param(pkg: str) -> dict:
    return {
        "name": "t",
        "type": "tt_t *",
        "capsule": f"{pkg}.tt",
        "header": f"{pkg}/tt.h",
        "retain": True,
    }


# -- 1. what the loader refuses -------------------------------------------


def _cfg(**method) -> dict:
    m = {"name": "attach", "params": [dict(_param("p"))]}
    m.update(method)
    return {"o": {"methods": [m]}}


class TestTheLoaderRefuses:
    def test_a_param_that_is_not_a_capsule(self):
        cfg = {
            "o": {
                "methods": [
                    {
                        "name": "attach",
                        "params": [
                            {"name": "n", "type": "int", "retain": True}
                        ],
                    }
                ]
            }
        }
        (err,) = _retain.errors(cfg)
        assert "n declares no `capsule`" in err, err

    def test_a_value_that_is_not_a_bool(self):
        cfg = _cfg()
        cfg["o"]["methods"][0]["params"][0]["retain"] = "yes"
        (err,) = _retain.errors(cfg)
        assert "must be true or false" in err, err

    @pytest.mark.parametrize("key", ["manual_stub", "varargs"])
    def test_a_hand_written_binding(self, key):
        (err,) = _retain.errors(_cfg(**{key: True}))
        assert "binding is hand-written" in err, err

    def test_a_view_method_is_read(self):
        cfg = {
            "o": {
                "views": [
                    {
                        "class_name": "V",
                        "methods": [
                            {
                                "name": "attach",
                                "manual_stub": True,
                                "params": [_param("p")],
                            }
                        ],
                    }
                ]
            }
        }
        (err,) = _retain.errors(cfg)
        assert err.startswith("[[o.views.methods.params]] attach.t"), err

    @pytest.mark.parametrize(
        "shape", ["status_return", "variable_output", "borrow", "single"]
    )
    def test_every_shape_jm_writes_is_accepted(self, shape):
        assert _retain.errors(_cfg(**{shape: True})) == []

    def test_false_is_accepted_anywhere(self):
        cfg = _cfg(manual_stub=True)
        cfg["o"]["methods"][0]["params"][0]["retain"] = False
        assert _retain.errors(cfg) == []

    def test_a_composer_source_does_not_delegate_it(self):
        cfg = {"g": {"methods": [_cfg()["o"]["methods"][0]]}}
        why = _composer._delegation_refusal(cfg, "g", cfg["g"]["methods"][0])
        assert "retains a capsule param" in why, why

    def test_load_refuses_through_the_manifest(self, tmp_path):
        _ok("new", "p", "--no-c-prefix", cwd=tmp_path)
        proj = tmp_path / "p"
        _ok("object", "o", cwd=proj)
        frag = proj / "objects" / "o.toml"
        frag.write_text(
            frag.read_text(encoding="utf-8")
            + '\n[[o.methods]]\nname = "m"\nreturn_type = "int"\n'
            'params = [{ name = "n", type = "int", retain = true }]\n',
            encoding="utf-8",
        )
        r = run_cli("apply", cwd=proj)
        assert r.returncode != 0, r.stdout
        assert "o.methods.params]] m.n: retain keeps the owner" in r.stderr


# -- 2. every shape keeps it, after the kernel's own refusal --------------

#: The roster's shapes that take params. A batch method refuses `params`
#: (gh-2261's `BATCH_METHOD_KEYS`), so it has nothing to retain.
_RETAINING = sorted(s for s in SHAPES if not s.startswith("batch"))

#: Beyond the roster: the refusals a variable_output count declares, and a
#: list of records that calls its kernel in a loop (gh-2204).
_EXTRA = {
    "vo_empty": {
        "variable_output": True,
        "error_on_empty": True,
        "arg_type": "float",
        "return_type": "float",
    },
    "vo_sentinel": {
        "variable_output": True,
        "error_sentinel": "0",
        "arg_type": "float",
        "return_type": "float",
    },
    "vo_none": {
        "variable_output": True,
        "none_on_empty": True,
        "arg_type": "float",
        "return_type": "float",
    },
}


def _roster(root: Path) -> Path:
    """Every shape, each with a retained capsule param, applied."""
    _ok("new", "mp", "--no-c-prefix", cwd=root)
    proj = root / "mp"
    hdr = proj / "native/inc/mp/tt.h"
    hdr.write_text(_TT_H, encoding="utf-8")
    _ok("object", "o", "--state", "k:double:0.0", cwd=proj)
    for name, flags in SHAPES.items():
        _ok("method", "o", name, *flags, cwd=proj)
    cfg = C.load(proj)
    for m in cfg["o"]["methods"]:
        if not m.get("batch"):
            m.setdefault("params", []).append(_param("mp"))
        if m.get("borrow"):
            m["borrow_count"] = "n"  # the roster's count, beside `t` now
    for name, keys in _EXTRA.items():
        cfg["o"]["methods"].append(
            {"name": name, **keys, "params": [_param("mp")]}
        )
    C.save(proj, cfg)
    # The kernels' stubs and their header are the author's, written once;
    # every signature just changed, so both are re-scaffolded together.
    (proj / "native/src/o/o_core.c").unlink()
    (proj / "native/inc/mp/o/o_core.h").unlink()
    _ok("apply", cwd=proj)
    # The two record shapes return the author's structs.
    core = proj / "native/inc/mp/o/o_core.h"
    text = core.read_text(encoding="utf-8")
    eol = text.index("\n", text.index("#include")) + 1
    core.write_text(
        text[:eol]
        + "typedef struct { double a; } rec_t;\n"
        + "typedef struct { double a; } rec2_t;\n"
        + text[eol:],
        encoding="utf-8",
    )
    return proj


@pytest.fixture(scope="module")
def roster(tmp_path_factory) -> Path:
    return _roster(tmp_path_factory.mktemp("retain"))


def _wrapper(ext: str, name: str) -> str:
    head = re.search(rf"^O_{name}\(OObject \*self", ext, re.M)
    assert head, f"no O_{name} wrapper"
    end = ext.index("\n}\n", head.start())
    return ext[head.start() : end]


_SWAP = "Py_XSETREF(self->_jm_retain_{}_t,"

#: The kernel's own refusal tests: a status, a negative or sentinel count,
#: an empty one under `error_on_empty` (the multi-line raise; the
#: `none_on_empty` answer is one line), a NULL borrow. A refused call has
#: not taken the new pointer, so the swap must come after every one.
_REFUSAL = re.compile(
    r"_rc\s*!=\s*0|_rc\s*<\s*0|n_out\s*==\s*\(|if \(!n_out\) \{\n"
    r"|if \(!_p\)"
)


@pytest.mark.parametrize("shape", _RETAINING + sorted(_EXTRA))
def test_every_kernel_call_is_followed_by_the_swap(roster, shape):
    ext = (roster / "native/src/o/o_ext.c").read_text(encoding="utf-8")
    body = _wrapper(ext, shape)
    swap = _SWAP.format(shape)
    calls = [m.end() for m in re.finditer(rf"\bo_{shape}\(", body)]
    assert calls, body
    for at in calls:
        nxt = body.find(swap, at)
        assert nxt > 0, f"o_{shape}(): no swap after the call\n{body}"
        # Between the kernel and the swap only a refusal may leave: the
        # owner is kept on every way out the kernel accepted.
        between = body[at:nxt]
        leaves = re.findall(r"\breturn\b[^;]*;|Py_RETURN_\w+", between)
        assert all(x == "return NULL;" for x in leaves), (
            f"o_{shape}(): a success path skips the swap: {leaves}\n{body}"
        )
    # And never before the kernel ran: a parse or acquisition failure must
    # not drop the owner the C side still holds.
    assert body.find(swap) > calls[0], body
    # Nor before the kernel's own refusal is read, on any path.
    for i, at in enumerate(calls):
        nxt = body.find(swap, at)
        end = calls[i + 1] if i + 1 < len(calls) else len(body)
        late = _REFUSAL.search(body, nxt, end)
        assert not late, (
            f"o_{shape}(): kept before the refusal {late.group(0)!r}\n{body}"
        )


def test_the_struct_holds_and_dealloc_releases(roster):
    ext = (roster / "native/src/o/o_ext.c").read_text(encoding="utf-8")
    struct = ext[: ext.index("} OObject;")]
    dealloc = ext[ext.index("O_dealloc(OObject *self)") :]
    dealloc = dealloc[: dealloc.index("\n}\n")]
    for shape in _RETAINING + sorted(_EXTRA):
        field = _retain.field(shape, "t")
        assert f"PyObject *{field};" in struct, shape
        assert f"Py_XDECREF(self->{field});" in dealloc, shape
    # The C state goes first, then the pointers it held.
    assert dealloc.index("o_destroy(") < dealloc.index("_jm_retain_"), dealloc


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_every_shape_compiles(roster):
    build = subprocess.run(
        ["make", f"PYTHON={sys.executable}"],
        cwd=roster,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert build.returncode == 0, build.stderr[-3000:]
    built = list((roster / "src/mp").glob("o*.so")) + list(
        (roster / "src/mp").glob("o*.pyd")
    )
    assert built, sorted(p.name for p in (roster / "src/mp").iterdir())


# -- 3. a hand-owned fragment rendered before the key is named -------------


def test_apply_names_a_fragment_that_does_not_keep_it(tmp_path):
    """A sacred module fragment only gains MISSING members, so turning
    `retain` on for a method it already binds changes nothing in it --
    `apply` names it, as it names every other feature it cannot deliver."""
    _ok("new", "p", "--no-c-prefix", cwd=tmp_path)
    proj = tmp_path / "p"
    (proj / "native/inc/p/tt.h").write_text(_TT_H, encoding="utf-8")
    _ok("module", "dsp", cwd=proj)
    _ok("object", "o", "--module", "dsp", cwd=proj)
    frag = proj / "objects" / "o.toml"
    frag.write_text(
        frag.read_text(encoding="utf-8")
        + '\n[[o.methods]]\nname = "attach"\nreturn_type = "int"\n'
        "status_return = true\n"
        'params = [{ name = "t", type = "tt_t *", capsule = "p.tt", '
        'header = "p/tt.h" }]\n',
        encoding="utf-8",
    )
    _ok("apply", cwd=proj)
    text = frag.read_text(encoding="utf-8")
    frag.write_text(
        text.replace(
            'header = "p/tt.h" }', 'header = "p/tt.h", retain = true }'
        ),
        encoding="utf-8",
    )
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stderr
    said = r.stdout + r.stderr
    assert "attach: the manifest declares retain on a capsule param" in said, (
        said
    )
    # The remedy it names delivers it.
    (proj / "native/src/dsp/dsp_ext_o.c").unlink()
    _ok("apply", cwd=proj)
    ext = (proj / "native/src/dsp/dsp_ext_o.c").read_text(encoding="utf-8")
    assert _SWAP.format("attach") in ext, ext


# -- 4. compiled: the owner's refcount ------------------------------------

#: The consumer's kernel keeps the pointer; a negative `k` is refused.
_POOL_C = {
    "pool_attach": (
        "    if (t && t->k < 0)\n        return -1;\n"
        "    state->held = t;\n    return 0;\n"
    ),
    "pool_peek": "    return state->held ? state->held->k : -1;\n",
}

#: Each probe line prints one fact; the expected output is beside it.
_PROBE = """\
import gc, sys
sys.path.insert(0, "src")
{imp}
rc = sys.getrefcount
pool = {make}
a, b, bad = Tt(k=5), Tt(k=7), Tt(k=-1)
base = rc(a)
pool.attach(a)
print("kept", rc(a) - base, pool.peek())
try:
    pool.attach(bad)
except ValueError:
    pass
print("refused", rc(a) - base, rc(bad) - base, pool.peek())
pool.attach(a)
print("again", rc(a) - base)
pool.attach(b)
print("replaced", rc(a) - base, rc(b) - base, pool.peek())
pool.attach(None)
print("detached", rc(b) - base, pool.peek())
pool.attach(b)
del pool
gc.collect()
print("dealloc", rc(b) - base)
pool = {make}
d = Tt(k=4)
pool.attach(d._capsule)
gc.collect()
print("bare", rc(d) - base, pool.peek())
"""

_EXPECT = [
    "kept 1 5",
    "refused 1 0 5",
    "again 1",
    "replaced 0 1 7",
    "detached 0 -1",
    "dealloc 0",
    "bare 1 4",
]


def _implement(path: Path, fn: str, body: str) -> None:
    text = path.read_text(encoding="utf-8")
    head = re.search(rf"^{fn}\([^)]*\)\n\{{\n", text, re.M)
    assert head, (fn, text)
    end = text.index("\n}\n", head.end())
    path.write_text(
        text[: head.end()] + body.rstrip("\n") + text[end:], encoding="utf-8"
    )


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
@pytest.mark.parametrize("variant", ["standalone", "module", "view"])
def test_the_owner_is_kept_replaced_detached_released(tmp_path, variant):
    _ok("new", "p", "--no-c-prefix", cwd=tmp_path)
    proj = tmp_path / "p"
    where = []
    if variant != "standalone":
        _ok("module", "dsp", cwd=proj)
        where = ["--module", "dsp"]
    # The producer: an object lending its state as a capsule (gh-788).
    _ok("object", "tt", *where, "--state", "k:int:0", cwd=proj)
    _ok(
        "property",
        "tt",
        "_capsule",
        *where,
        "--type",
        "capsule",
        "--capsule",
        "p.tt",
        cwd=proj,
    )
    _ok("object", "pool", *where, "--state", "k:int:0", cwd=proj)
    frag = proj / "objects" / "pool.toml"
    frag.write_text(
        frag.read_text(encoding="utf-8")
        + '\n[[pool.methods]]\nname = "attach"\nreturn_type = "int"\n'
        "status_return = true\n"
        'params = [{ name = "t", type = "tt_state_t *", capsule = "p.tt", '
        'header = "p/tt/tt_core.h", retain = true }]\n'
        '\n[[pool.methods]]\nname = "peek"\nreturn_type = "int"\n',
        encoding="utf-8",
    )
    _ok("apply", cwd=proj)
    make = "Pool(k=0)"
    if variant == "view":
        _ok(
            "view",
            "pool",
            "PoolView",
            "--module",
            "dsp",
            "--create-fn",
            "pool_create_view",
            cwd=proj,
        )
        make = "PoolView(k=0)"
    hdr = proj / "native/inc/p/pool/pool_core.h"
    text = hdr.read_text(encoding="utf-8")
    assert text.count("    int k;\n") == 1, text
    hdr.write_text(
        text.replace("    int k;\n", "    int k;\n    tt_state_t *held;\n"),
        encoding="utf-8",
    )
    core = proj / "native/src/pool/pool_core.c"
    for fn, body in _POOL_C.items():
        _implement(core, fn, body)
    if variant == "view":
        _implement(core, "pool_create_view", "    return pool_create(k);\n")
    build = subprocess.run(
        ["make", f"PYTHON={sys.executable}"],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert build.returncode == 0, build.stderr[-3000:]
    cls = make.split("(")[0]
    imp = (
        f"from p import Pool as {cls}, Tt"
        if variant == "standalone"
        else f"from p.dsp import {cls}, Tt"
    )
    script = proj / "probe.py"
    script.write_text(_PROBE.format(imp=imp, make=make), encoding="utf-8")
    out = subprocess.run(
        [sys.executable, str(script)],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, (out.returncode, out.stderr[-2000:])
    assert out.stdout.split("\n")[:-1] == _EXPECT, out.stdout
