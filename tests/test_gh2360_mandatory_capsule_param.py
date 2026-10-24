"""gh-2360 -- a method capsule param declared mandatory.

A method's capsule param takes `None` as `NULL`, the C-side detach idiom
(gh-432). A kernel that refuses `NULL` -- doppler's `Writer.annotate(scene)`
-- had no way to say so: the stub typed the param `object | None`, so a type
checker accepted the call that always fails, and the refusal came back as
the kernel's status (`ValueError: annotate failed`) instead of a binding
`TypeError` naming what to pass.

`required = true` on the param fixes both faces from one declaration
(`_context/_parse.capsule_nullable`): the binding unwraps through
`capsule_unwrap_c(allow_none=False)`, the constructor's mandatory form, and
every stub writer types the param `object`. Without the key,
`set_telemetry(None)` keeps detaching. It is the constructor's spelling of
the same choice (gh-805 §H), and declared `true` because every manifest
reader treats a bool key's `false` as its absence -- `jm script` would have
replayed a `nullable = false` away.
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
from conftest import flatten_signatures

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit._context._parse import (  # noqa: E402
    REQUIRED_CAPSULE_RE,
    required_errors,
)

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

#: The consumer's attach kernel keeps the pointer and refuses NULL itself,
#: as doppler's does -- so a None that reached it would come back as the
#: kernel's status, a ValueError, not the binding's TypeError.
_POOL_C = {
    "pool_attach": (
        "    if (!t)\n        return -1;\n"
        "    state->held = t;\n    return 0;\n"
    ),
    "pool_peek": "    return state->held ? state->held->k : -1;\n",
}


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


def _attach(required: bool) -> str:
    """The `attach` method's TOML, its param `required = true` or not."""
    key = ", required = true" if required else ""
    return (
        '\n[[pool.methods]]\nname = "attach"\nreturn_type = "int"\n'
        "status_return = true\n"
        'params = [{ name = "t", type = "tt_state_t *", capsule = "p.tt", '
        f'header = "p/tt/tt_core.h", retain = true, doc = "The scene."{key} }}]\n'
        '\n[[pool.methods]]\nname = "peek"\nreturn_type = "int"\n'
    )


def _project(tmp_path: Path, variant: str, required: bool) -> Path:
    """A producer `Tt` lending its state as `_capsule`, and a consumer
    `Pool` whose `attach` takes it, standalone or in module `dsp` (where
    *variant* ``"view"`` adds a `PoolView` over the same core)."""
    _ok("new", "p", "--no-c-prefix", cwd=tmp_path)
    proj = tmp_path / "p"
    where = []
    if variant != "standalone":
        _ok("module", "dsp", cwd=proj)
        where = ["--module", "dsp"]
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
        frag.read_text(encoding="utf-8") + _attach(required),
        encoding="utf-8",
    )
    _ok("apply", cwd=proj)
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
    return proj


def _ext(proj: Path, variant: str) -> str:
    rel = (
        "native/src/pool/pool_ext.c"
        if variant == "standalone"
        else "native/src/dsp/dsp_ext_pool.c"
    )
    return (proj / rel).read_text(encoding="utf-8")


def _pyi(proj: Path, variant: str) -> str:
    rel = "src/p/pool.pyi" if variant == "standalone" else "src/p/dsp/dsp.pyi"
    return flatten_signatures((proj / rel).read_text(encoding="utf-8"))


_VARIANTS = ["standalone", "module", "view"]


# -- 1. the loader refuses a key it would not honour -----------------------


def _cfg(param: dict) -> dict:
    return {"o": {"methods": [{"name": "m", "params": [param]}]}}


class TestTheLoaderRefuses:
    def test_a_param_that_is_not_a_capsule(self):
        (msg,) = required_errors(
            _cfg({"name": "n", "type": "int", "required": True})
        )
        assert msg.startswith("[[o.methods.params]] m.n: required says")

    def test_a_value_that_is_not_a_bool(self):
        (msg,) = required_errors(
            _cfg({"name": "t", "capsule": "x.t", "required": "true"})
        )
        assert "must be true or false" in msg

    @pytest.mark.parametrize("value", [True, False])
    def test_a_capsule_param_may_say_either(self, value):
        assert not required_errors(
            _cfg({"name": "t", "capsule": "x.t", "required": value})
        )

    def test_a_view_method_is_read(self):
        cfg = {
            "o": {
                "views": [
                    {
                        "name": "V",
                        "methods": [
                            {
                                "name": "m",
                                "params": [{"name": "n", "required": True}],
                            }
                        ],
                    }
                ]
            }
        }
        (msg,) = required_errors(cfg)
        assert msg.startswith("[[o.views.methods.params]] m.n")

    def test_load_refuses_through_the_manifest(self, tmp_path):
        _ok("new", "p", "--no-c-prefix", cwd=tmp_path)
        proj = tmp_path / "p"
        _ok("object", "o", cwd=proj)
        frag = proj / "objects" / "o.toml"
        frag.write_text(
            frag.read_text(encoding="utf-8")
            + '\n[[o.methods]]\nname = "m"\nreturn_type = "int"\n'
            'params = [{ name = "n", type = "int", required = true }]\n',
            encoding="utf-8",
        )
        r = run_cli("apply", cwd=proj)
        assert r.returncode != 0, r.stdout
        assert "o.methods.params]] m.n: required says" in r.stderr


# -- 2. both faces, from the one declaration -------------------------------


@pytest.fixture(scope="module", params=_VARIANTS)
def declared(request, tmp_path_factory):
    variant = request.param
    root = tmp_path_factory.mktemp(f"declared-{variant}")
    return variant, _project(root, variant, required=True)


@pytest.fixture(scope="module", params=_VARIANTS)
def undeclared(request, tmp_path_factory):
    variant = request.param
    root = tmp_path_factory.mktemp(f"undeclared-{variant}")
    return variant, _project(root, variant, required=False)


def test_the_binding_refuses_none_up_front(declared):
    variant, proj = declared
    ext = _ext(proj, variant)
    assert REQUIRED_CAPSULE_RE.search(ext), ext
    assert "if (t_obj != Py_None) {" not in ext


def test_a_retained_required_param_is_kept_without_a_detach(declared):
    """`retain = true`'s swap keeps the owner or, on None, drops it. The
    binding has already refused None, so the swap keeps it unconditionally
    rather than carrying a detach branch that cannot run."""
    variant, proj = declared
    ext = _ext(proj, variant)
    assert "Py_XSETREF(self->_jm_retain_attach_t, t_obj);" in ext, ext
    assert "t_obj != Py_None ? t_obj : NULL" not in ext
    assert "None detaches it" not in ext


def test_the_stub_types_it_object(declared):
    variant, proj = declared
    pyi = _pyi(proj, variant)
    assert "def attach(self, t: object) -> None:" in pyi, pyi
    assert "t: object | None" not in pyi
    if variant == "view":
        # A view's method table is rendered apart from its core object's;
        # the second class must read the declaration too.
        view = pyi[pyi.index("class PoolView") :]
        assert "def attach(self, t: object) -> None:" in view, view


def test_the_docstrings_say_object(declared):
    """The `Parameters` line is a third face of the declaration: the stub's
    docstring and the runtime `__doc__` read it from their own lists."""
    variant, proj = declared
    rel = "src/p/pool.pyi" if variant == "standalone" else "src/p/dsp/dsp.pyi"
    pyi = (proj / rel).read_text(encoding="utf-8")
    assert re.search(r"^ +t : object\n", pyi, re.M), pyi
    assert '"t : object\\n"' in _ext(proj, variant)


def test_undeclared_it_still_detaches(undeclared):
    """The default is the detach idiom, byte for byte the gh-432 shape."""
    variant, proj = undeclared
    ext = _ext(proj, variant)
    assert "if (t_obj != Py_None) {" in ext
    assert not REQUIRED_CAPSULE_RE.search(ext), ext
    assert "t_obj != Py_None ? t_obj : NULL" in ext, ext
    assert "def attach(self, t: object | None) -> None:" in _pyi(proj, variant)


# -- 3. a fragment rendered before the declaration is named ----------------


def test_apply_names_a_fragment_that_still_passes_none(tmp_path):
    """A sacred module fragment only gains MISSING members, so declaring
    `required = true` on a method it already binds changes nothing in it
    -- `apply` names it, and the remedy it names delivers it."""
    proj = _project(tmp_path, "module", required=False)
    frag = proj / "objects" / "pool.toml"
    text = frag.read_text(encoding="utf-8")
    assert text.count("retain = true,") == 1, text
    frag.write_text(
        text.replace("retain = true,", "retain = true, required = true,"),
        encoding="utf-8",
    )
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stderr
    said = r.stdout + r.stderr
    assert "attach: the manifest declares required on a capsule" in said, said
    (proj / "native/src/dsp/dsp_ext_pool.c").unlink()
    _ok("apply", cwd=proj)
    assert REQUIRED_CAPSULE_RE.search(_ext(proj, "module"))


def test_a_replay_names_it(declared):
    """`jm script` cannot spell the key on `--param`, so it must NAME it
    rather than replay a mandatory param as a nullable one."""
    variant, proj = declared
    r = run_cli("script", cwd=proj)
    assert r.returncode == 0, r.stderr
    note = [ln for ln in r.stdout.splitlines() if "param 't' declares" in ln]
    assert note and "required = true" in note[0], r.stdout


# -- 4. compiled: None is the binding's TypeError, not the kernel's status --

_PROBE = """\
import sys
sys.path.insert(0, "src")
{imp}
pool = {make}
try:
    pool.attach(None)
except TypeError as e:
    print("none", type(e).__name__, "t is required" in str(e))
except ValueError as e:
    print("none", type(e).__name__)
print("peek", pool.peek())
a = Tt(k=5)
pool.attach(a)
print("kept", pool.peek())
"""

_EXPECT = ["none TypeError True", "peek -1", "kept 5"]


def _implement(path: Path, fn: str, body: str) -> None:
    text = path.read_text(encoding="utf-8")
    head = re.search(rf"^{fn}\([^)]*\)\n\{{\n", text, re.M)
    assert head, (fn, text)
    end = text.index("\n}\n", head.end())
    path.write_text(
        text[: head.end()] + body.rstrip("\n") + text[end:], encoding="utf-8"
    )


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
@pytest.mark.parametrize("variant", _VARIANTS)
def test_none_raises_type_error_at_the_binding(tmp_path, variant):
    proj = _project(tmp_path, variant, required=True)
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
    make = "Pool(k=0)"
    if variant == "view":
        _implement(core, "pool_create_view", "    return pool_create(k);\n")
        make = "PoolView(k=0)"
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
