"""gh-2574 / gh-2407: a method's refusal raises the reason its C names.

``why = true`` -- gh-1706's ``why`` on a module function, gh-2552's
``create_why`` on a constructor -- on ``[[<obj>.methods]]``, a view's
methods and a ``kind = "handle"`` module's methods. The kernel takes a
trailing ``const char **why`` after every other parameter, the binding
passes the address of a local initialised to NULL, and when the method's
DECLARED refusal fires it raises the declared exception with the sentence
the kernel wrote, or the declared message when it wrote none. The pointer
never reaches Python.

A method can refuse five ways, and each is here, built and run:

* ``error_negative`` -- a negative return (``neg``);
* ``status_return``, with a ``status_errors`` row (``stat``), and under
  ``nogil`` (``ng``), where the sentence is read after the GIL is back;
* a ``variable_output`` count: ``error_on_empty`` (``vo``), a signed
  count's ``error_negative`` (``vneg``), an ``error_sentinel`` (``vsen``)
  -- each on both of its call paths, ``out=`` and allocating;
* the ``out=`` count of gh-2559 (``fill``);
* a ``borrow``'s NULL (``bor``);
* the ``exit`` finalizer (``fin``), which ``__exit__`` calls too.

Faces: a standalone object (``cor``), a module object (``acc``), a view's
own method (``AccView.vstat``) and a handle module (``Hb``). Every project
is built by running jm -- the manifest is the author's, ``jm apply`` writes
the header, the stub, the binding, the C test and the bench -- and only the
kernel BODIES are written by hand, as an author would.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli, script_round_trip
from just_makeit import _ctorsig
from just_makeit._context import _diagnostics as D

#: The sentence each kernel writes on its ``mode == 1`` refusal.
SAID = {
    "neg": "neg: mode 1 is refused",
    "stat": "stat: mode 1 is refused",
    "stat_row": "stat: mode 3 is a key",
    "ng": "ng: mode 1 is refused",
    "vo": "vo: mode 1 is refused",
    "vneg": "vneg: mode 1 is refused",
    "vsen": "vsen: mode 1 is refused",
    "fill": "fill: mode 1 is refused",
    "bor": "bor: a count of 1 is refused",
    "fin": "fin: odd calls are refused",
    "acc_stat": "acc.stat: mode 1 is refused",
    "acc_vstat": "AccView.vstat: mode 1 is refused",
}

COR_TOML = """
[cor]
arg_type = "float"
return_type = "float"

[cor.destroy]
exit = "fin"

[[cor.methods]]
name = "fin"
arg_type = "void"
return_type = "int"
status_return = true
error_message = "fin refused"
why = true

[[cor.methods]]
name = "neg"
arg_type = "void"
return_type = "int"
error_negative = true
error_message = "neg refused"
why = true

[[cor.methods.params]]
name = "mode"
type = "int"

[[cor.methods]]
name = "stat"
arg_type = "void"
return_type = "int"
status_return = true
error = "RuntimeError"
error_message = "stat refused"
why = true

[[cor.methods.params]]
name = "mode"
type = "int"

[[cor.methods.status_errors]]
status = "RQ_E_KEY"
error = "KeyError"
message = "stat row"

[[cor.methods]]
name = "ng"
arg_type = "void"
return_type = "int"
status_return = true
nogil = true
error_message = "ng refused"
why = true

[[cor.methods.params]]
name = "mode"
type = "int"

[[cor.methods]]
name = "vo"
arg_type = "float[]"
return_type = "float"
variable_output = true
max_out = 4
error_on_empty = true
error_message = "vo refused"
why = true

[[cor.methods]]
name = "vneg"
arg_type = "float[]"
return_type = "float"
variable_output = true
max_out = 4
count_type = "int64_t"
error_negative = true
error_message = "vneg refused"
why = true

[[cor.methods]]
name = "vsen"
arg_type = "float[]"
return_type = "float"
variable_output = true
max_out = 4
error_sentinel = "SIZE_MAX"
error_message = "vsen refused"
why = true

[[cor.methods]]
name = "bor"
arg_type = "void"
return_type = "float"
borrow = true
why = true

[[cor.methods.params]]
name = "n"
type = "size_t"

[[cor.methods]]
name = "fill"
arg_type = "void"
return_type = "size_t"
error_sentinel = "SIZE_MAX"
error_message = "fill refused"
why = true

[[cor.methods.params]]
name = "buf"
type = "float[]"
out = true

[[cor.methods.params]]
name = "mode"
type = "int"
"""

ACC_TOML = """
[acc]
arg_type = "float"
return_type = "float"

[[acc.methods]]
name = "stat"
arg_type = "void"
return_type = "int"
status_return = true
error_message = "acc stat refused"
why = true

[[acc.methods.params]]
name = "mode"
type = "int"

[[acc.views]]
class_name = "AccView"
create_fn = "rq_acc_create_view"

[[acc.views.methods]]
name = "vstat"
fn = "rq_acc_vstat"
arg_type = "void"
return_type = "int"
status_return = true
error = "OSError"
error_message = "view stat refused"
why = true

[[acc.views.methods.params]]
name = "mode"
type = "int"
"""

#: Every object kernel, with the definition's parameter list jm renders.
#: The trailing `why` is the claim; the rest is each shape's own.
PROTOS = {
    "rq_cor_fin": "rq_cor_state_t *state, const char **why",
    "rq_cor_neg": "rq_cor_state_t *state, int mode, const char **why",
    "rq_cor_stat": "rq_cor_state_t *state, int mode, const char **why",
    "rq_cor_ng": "rq_cor_state_t *state, int mode, const char **why",
    "rq_cor_vo": (
        "rq_cor_state_t *state, const float *in, size_t n_in, float *out, "
        "const char **why"
    ),
    "rq_cor_vneg": (
        "rq_cor_state_t *state, const float *in, size_t n_in, float *out, "
        "const char **why"
    ),
    "rq_cor_vsen": (
        "rq_cor_state_t *state, const float *in, size_t n_in, float *out, "
        "const char **why"
    ),
    "rq_cor_fill": (
        "rq_cor_state_t *state, float *buf, size_t buf_len, int mode, "
        "const char **why"
    ),
    "rq_cor_bor": "rq_cor_state_t *state, size_t n, const char **why",
    "rq_acc_stat": "rq_acc_state_t *state, int mode, const char **why",
    "rq_acc_vstat": "rq_acc_state_t *state, int mode, const char **why",
}


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout + r.stderr


def _project(root: Path) -> Path:
    """``rq`` with standalone ``cor`` and module ``dsp``'s ``acc`` +
    ``AccView``, each declared in the manifest and written by ``apply``."""
    _ok("new", "rq", "--object", "seed", cwd=root)
    proj = root / "rq"
    _ok("module", "dsp", cwd=proj)
    (proj / "objects" / "cor.toml").write_text(COR_TOML, encoding="utf-8")
    (proj / "objects" / "acc.toml").write_text(ACC_TOML, encoding="utf-8")
    mod = proj / "modules" / "dsp.toml"
    text = mod.read_text(encoding="utf-8")
    assert text.count("objects = []") == 1, text
    mod.write_text(text.replace("objects = []", 'objects = ["acc"]'), "utf-8")
    _ok("apply", cwd=proj)
    return proj


@pytest.fixture(scope="module")
def scaffold(tmp_path_factory) -> Path:
    return _project(tmp_path_factory.mktemp("gh2574"))


def _read(proj: Path, rel: str) -> str:
    return (proj / rel).read_text(encoding="utf-8")


# -- the scaffold jm writes ----------------------------------------------------


def test_the_scaffold_is_clean(scaffold: Path) -> None:
    r = run_cli("status", "--check", cwd=scaffold)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("fn", sorted(PROTOS))
def test_header_and_stub_take_why_last(scaffold: Path, fn: str) -> None:
    """The prototype jm declares and the stub it scaffolds are one
    signature, ending in the trailing ``const char **why``."""
    comp = fn.split("_")[1]
    found = {
        (s.kind, _ctorsig._norm(s.params))
        for rel in (
            f"native/inc/rq/{comp}/{comp}_core.h",
            f"native/src/{comp}/{comp}_core.c",
        )
        for s in _ctorsig.signatures(_read(scaffold, rel))
        if s.name == fn
    }
    want = _ctorsig._norm(PROTOS[fn])
    assert found == {("decl", want), ("def", want)}, found


@pytest.mark.parametrize("fn", sorted(PROTOS))
def test_the_binding_passes_the_pointer_last(scaffold: Path, fn: str) -> None:
    """Every call the binding makes to the kernel ends in ``&_why`` --
    both call paths of a variable_output method, and ``__exit__``'s."""
    # A view's binding is a fragment of its own (`dsp_ext_accview.c`).
    ext = "".join(
        p.read_text("utf-8")
        for p in sorted((scaffold / "native/src").rglob("*_ext*.c"))
    )
    calls = re.findall(rf"\b{fn}\(([^;]*?)\);", ext, re.S)
    assert calls, fn
    for args in calls:
        assert args.rstrip().endswith("&_why"), (fn, args)


def test_the_rendered_header_slot_takes_why(scaffold: Path) -> None:
    """A standalone header's ``method_decls`` is the second builder of a
    method's prototype (the peer of `_build_method_prototype`); it ends in
    the parameter too, for every shape."""
    from just_makeit import _config as C
    from just_makeit import _glue

    cfg = C.load(scaffold)
    decls = _glue.component_ctx(cfg, "cor", "rq", scaffold)["method_decls"]
    for fn, params in PROTOS.items():
        if fn.startswith("rq_cor_"):
            assert re.search(
                rf"\b{fn}\({re.escape(params)}\);", " ".join(decls.split())
            ), (fn, decls)


def test_the_bench_passes_null(scaffold: Path) -> None:
    bench = _read(scaffold, "native/benchmarks/bench_cor_core.c")
    assert re.search(r"rq_cor_neg\(obj, 0, NULL\)", bench), bench


@pytest.mark.parametrize("stub", ["src/rq/cor.pyi", "src/rq/dsp/dsp.pyi"])
def test_why_is_not_in_python(scaffold: Path, stub: str) -> None:
    tree = ast.parse(_read(scaffold, stub))
    fns = [
        fn
        for cls in tree.body
        if isinstance(cls, ast.ClassDef)
        for fn in cls.body
        if isinstance(fn, ast.FunctionDef)
    ]
    assert {"stat", "vstat"} & {f.name for f in fns}, stub
    for fn in fns:
        assert "why" not in [a.arg for a in fn.args.args], ast.unparse(fn)


def test_the_pyi_says_where_the_text_comes_from(scaffold: Path) -> None:
    pyi = " ".join(_read(scaffold, "src/rq/cor.pyi").split())
    assert (
        "The exception message is the reason the C call gives, or "
        "``stat refused``, with the return code appended (gh-869), when it "
        "gives none."
    ) in pyi
    # A row's message is the fallback for the row's own class.
    assert (
        "If the C call returns ``RQ_E_KEY``. The exception message is the "
        "reason the C call gives, or ``stat row`` when it gives none."
    ) in pyi


def test_script_replays_the_flag(tmp_path: Path) -> None:
    """``jm method --why`` writes the key, and ``jm script`` replays it."""
    _ok("new", "sq", cwd=tmp_path)
    proj = tmp_path / "sq"
    _ok("object", "cor", cwd=proj)
    _ok(
        "method", "cor", "stat", "--param", "mode:int", "--return-type",
        "int", "--status-return", "--why", cwd=proj,
    )  # fmt: skip
    assert "--why" in _ok("script", cwd=proj)
    orig, replayed = script_round_trip(proj, tmp_path / "replay")
    assert orig["cor"]["methods"][0]["why"] is True
    assert orig == replayed


# -- the header jm does not rewrite --------------------------------------------


@pytest.mark.parametrize(
    "decl",
    ["int rq_acc_stat(rq_acc_state_t *state, int mode",
     "int rq_acc_vstat(rq_acc_state_t *state, int mode"],
)  # fmt: skip
def test_a_header_without_why_is_a_finding(
    scaffold: Path, tmp_path: Path, decl: str
) -> None:
    """A module object's sacred header only gains declarations on `apply`,
    so a method that takes ``why`` later keeps its old prototype there.
    `status` names it -- for the object's method and the view's alike."""
    proj = tmp_path / "rq"
    shutil.copytree(scaffold, proj)
    hdr = proj / "native/inc/rq/acc/acc_core.h"
    text = hdr.read_text("utf-8")
    full = f"{decl}, const char **why);"
    assert text.count(full) == 1, text
    hdr.write_text(text.replace(full, f"{decl});"), "utf-8")
    r = run_cli("status", "--check", cwd=proj)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "WHY (1)" in out and f"header:   {decl.split(' ', 1)[1]})" in out


# -- refused at load -----------------------------------------------------------


def _refusal(tmp_path: Path, toml: str, name: str = "cor") -> str:
    _ok("new", "rz", "--object", "seed", cwd=tmp_path)
    proj = tmp_path / "rz"
    (proj / "objects" / f"{name}.toml").write_text(toml, encoding="utf-8")
    r = run_cli("apply", cwd=proj)
    assert r.returncode != 0, r.stdout + r.stderr
    return r.stdout + r.stderr


_PLAIN = """
[cor]
arg_type = "float"
return_type = "float"

[[cor.methods]]
name = "stat"
arg_type = "void"
return_type = "int"
status_return = true
why = true

[[cor.methods.params]]
name = "mode"
type = "int"
"""


def test_why_without_a_refusal_is_refused(tmp_path: Path) -> None:
    out = _refusal(tmp_path, _PLAIN.replace("status_return = true\n", ""))
    assert "[[cor.methods]] 'stat' declares why = true, but its" in out, out


def test_a_non_bool_why_is_refused(tmp_path: Path) -> None:
    out = _refusal(
        tmp_path, _PLAIN.replace("why = true", 'why = "rq_cor_stat_why"')
    )
    assert "why = 'rq_cor_stat_why' must be true or false" in out, out


def test_a_param_named_why_is_refused(tmp_path: Path) -> None:
    out = _refusal(tmp_path, _PLAIN.replace('name = "mode"', 'name = "why"'))
    assert "declares why = true and a parameter named `why`" in out, out


def test_the_cli_refuses_before_writing(tmp_path: Path) -> None:
    _ok("new", "sq", cwd=tmp_path)
    proj = tmp_path / "sq"
    _ok("object", "cor", cwd=proj)
    before = sorted(p.as_posix() for p in proj.rglob("*"))
    r = run_cli(
        "method", "cor", "stat", "--param", "mode:int", "--return-type",
        "int", "--why", cwd=proj,
    )  # fmt: skip
    assert r.returncode != 0, r.stdout
    assert "declares why = true, but its binding raises" in r.stderr
    assert sorted(p.as_posix() for p in proj.rglob("*")) == before


def test_a_view_inheriting_a_param_named_why_is_refused(
    tmp_path: Path,
) -> None:
    """A view's override of a parent method takes the parent's params, so
    a parent param named ``why`` meets the override's ``why``."""
    toml = (
        _PLAIN.replace("why = true\n", "").replace(
            'name = "mode"', 'name = "why"'
        )
        + '\n[[cor.views]]\nclass_name = "V"\ncreate_fn = "rz_cor_create_v"\n'
        '\n[[cor.views.methods]]\nname = "stat"\nwhy = true\n'
    )
    out = _refusal(tmp_path, toml)
    assert "'V' method 'stat' declares why = true and a parameter" in out


# -- one emitter ---------------------------------------------------------------


def test_without_the_key_the_render_is_unchanged() -> None:
    """``why`` absent renders every raise byte for byte as before."""
    assert D._rc_raise_c("ValueError", "m") == D._rc_raise_c(
        "ValueError", "m", why=""
    )
    assert "_why" not in D._rc_raise_c("ValueError", "m")
    assert "_why" not in D.empty_raise_c("ValueError", "m", "Py_DECREF(a); ")


# -- built and run -------------------------------------------------------------

#: Each kernel body, by C symbol: mode 1 refuses with a sentence, mode 2
#: refuses without one, anything else succeeds.
_STATUS = (
    "    if (mode == 1) {{\n"
    '        if (why)\n            *why = "{said}";\n'
    "        return 3;\n    }}\n"
    "    if (mode == 2)\n        return 3;\n"
    "    return 0;\n"
)
_COUNT = (
    "    int mode = n_in ? (int)in[0] : 0;\n"
    "    if (mode == 1) {{\n"
    '        if (why)\n            *why = "{said}";\n'
    "        return {refuse};\n    }}\n"
    "    if (mode == 2)\n        return {refuse};\n"
    "    out[0] = 4.0f;\n"
    "    return 1;\n"
)
BODIES = {
    "rq_cor_fin": (
        "    static int calls;\n"
        "    (void)state;\n"
        "    if (++calls % 2) {\n"
        f'        if (why)\n            *why = "{SAID["fin"]}";\n'
        "        return 1;\n    }\n"
        "    return 2;\n"
    ),
    "rq_cor_neg": (
        "    (void)state;\n"
        "    if (mode == 1) {\n"
        f'        if (why)\n            *why = "{SAID["neg"]}";\n'
        "        return -1;\n    }\n"
        "    if (mode == 2)\n        return -1;\n"
        "    return 5;\n"
    ),
    "rq_cor_stat": (
        "    (void)state;\n"
        "    if (mode == 3) {\n"
        f'        if (why)\n            *why = "{SAID["stat_row"]}";\n'
        "        return RQ_E_KEY;\n    }\n"
        "    if (mode == 4)\n        return RQ_E_KEY;\n"
        + _STATUS.format(said=SAID["stat"])
    ),
    "rq_cor_ng": "    (void)state;\n" + _STATUS.format(said=SAID["ng"]),
    "rq_cor_vo": "    (void)state;\n"
    + _COUNT.format(said=SAID["vo"], refuse="0"),
    "rq_cor_vneg": "    (void)state;\n"
    + _COUNT.format(said=SAID["vneg"], refuse="-2"),
    "rq_cor_vsen": "    (void)state;\n"
    + _COUNT.format(said=SAID["vsen"], refuse="SIZE_MAX"),
    "rq_cor_bor": (
        "    static float buf[4];\n"
        "    (void)state;\n"
        "    if (n == 1) {\n"
        f'        if (why)\n            *why = "{SAID["bor"]}";\n'
        "        return NULL;\n    }\n"
        "    if (n == 2)\n        return NULL;\n"
        "    return buf;\n"
    ),
    "rq_cor_fill": (
        "    (void)state;\n"
        "    if (mode == 1) {\n"
        f'        if (why)\n            *why = "{SAID["fill"]}";\n'
        "        return SIZE_MAX;\n    }\n"
        "    if (mode == 2)\n        return SIZE_MAX;\n"
        "    if (buf_len)\n        buf[0] = 4.0f;\n"
        "    return 1;\n"
    ),
    # The view's constructor: the scaffold's returns NULL.
    "rq_acc_create_view": "    return calloc(1, sizeof(rq_acc_state_t));\n",
    "rq_acc_stat": "    (void)state;\n"
    + _STATUS.format(said=SAID["acc_stat"]),
    "rq_acc_vstat": "    (void)state;\n"
    + _STATUS.format(said=SAID["acc_vstat"]),
}


def _give_bodies(path: Path, fns: "list[str]") -> None:
    """Replace each scaffolded definition of *fns* with its `BODIES` body,
    keeping the signature jm wrote."""
    text = path.read_text("utf-8")
    for fn in fns:
        pat = re.compile(
            rf"^({re.escape(fn)}\([^)]*\))\n\{{.*?^\}}", re.M | re.S
        )
        text, n = pat.subn(
            lambda m, fn=fn: f"{m.group(1)}\n{{\n{BODIES[fn]}}}", text
        )
        assert n == 1, (path, fn)
    text = "#include <stdint.h>\n#include <stdlib.h>\n" + text
    path.write_text(text, "utf-8")


def _cmake(proj: Path) -> None:
    for cmd in (
        ["cmake", "-S", ".", "-B", "build",
         f"-DPython3_EXECUTABLE={sys.executable}"],
        ["cmake", "--build", "build"],
        ["ctest", "--test-dir", "build", "--output-on-failure"],
    ):  # fmt: skip
        r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    if not shutil.which("cmake"):
        pytest.skip("cmake not found")
    proj = _project(tmp_path_factory.mktemp("gh2574b"))
    # The author's status code, in the sacred header the binding includes.
    hdr = proj / "native/inc/rq/cor/cor_core.h"
    text = hdr.read_text("utf-8")
    hdr.write_text(
        text.replace("#include", "#define RQ_E_KEY 7\n#include", 1), "utf-8"
    )
    _give_bodies(
        proj / "native/src/cor/cor_core.c",
        [f for f in BODIES if f.startswith("rq_cor_")],
    )
    _give_bodies(
        proj / "native/src/acc/acc_core.c",
        ["rq_acc_create_view", "rq_acc_stat", "rq_acc_vstat"],
    )
    _cmake(proj)
    return proj


_PROBE = """
import json, sys
import numpy as np
sys.path.insert(0, "src")
from rq import Cor
from rq.dsp import Acc, AccView


def run(f):
    try:
        r = f()
        return ["ok", r.tolist() if hasattr(r, "tolist") else r]
    except Exception as e:
        return [type(e).__name__, str(e).strip("'")]


c, a, v = Cor(), Acc(), AccView()
x = lambda m: np.array([m], dtype=np.float32)
out = {}
for m in (0, 1, 2):
    out[f"neg{m}"] = run(lambda: c.neg(m))
    out[f"ng{m}"] = run(lambda: c.ng(m))
    out[f"bor{m}"] = run(lambda: c.bor(m))
    out[f"fill{m}"] = run(lambda: c.fill(np.zeros(2, np.float32), m))
    out[f"acc_stat{m}"] = run(lambda: a.stat(m))
    out[f"acc_vstat{m}"] = run(lambda: v.vstat(m))
    for vo in ("vo", "vneg", "vsen"):
        out[f"{vo}{m}"] = run(lambda: getattr(c, vo)(x(m)))
        out[f"{vo}_out{m}"] = run(
            lambda: getattr(c, vo)(x(m), out=np.zeros(4, np.float32)))
for m in (0, 1, 2, 3, 4):
    out[f"stat{m}"] = run(lambda: c.stat(m))
out["fin1"] = run(lambda: c.fin())
out["fin2"] = run(lambda: c.fin())


def exit_block():
    with Cor():
        pass


out["exit"] = run(exit_block)
try:
    c.stat(0, why=None)
    out["why_kw"] = "accepted"
except TypeError:
    out["why_kw"] = "TypeError"
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def probe(built: Path) -> dict:
    r = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=built, capture_output=True,
        text=True,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


#: (probe key stem, exception, the text a refusal with no sentence raises)
REFUSALS = [
    ("neg", "ValueError", "neg refused (rc=-1)"),
    ("ng", "ValueError", "ng refused (rc=3)"),
    ("fill", "ValueError", "fill refused"),
    ("bor", "ValueError", "bor failed"),
    ("acc_stat", "ValueError", "acc stat refused (rc=3)"),
    ("acc_vstat", "OSError", "view stat refused (rc=3)"),
    ("vo", "ValueError", "vo refused"),
    ("vo_out", "ValueError", "vo refused"),
    ("vneg", "ValueError", "vneg refused (rc=-2)"),
    ("vneg_out", "ValueError", "vneg refused (rc=-2)"),
    ("vsen", "ValueError", "vsen refused"),
    ("vsen_out", "ValueError", "vsen refused"),
    ("stat", "RuntimeError", "stat refused (rc=3)"),
]


@pytest.mark.parametrize("key, exc, _msg", REFUSALS)
def test_a_refusal_with_a_sentence_raises_it(probe, key, exc, _msg) -> None:
    said = SAID[key.removesuffix("_out")]
    assert probe[f"{key}1"] == [exc, said], probe[f"{key}1"]


@pytest.mark.parametrize("key, exc, msg", REFUSALS)
def test_a_refusal_with_no_sentence_keeps_the_message(
    probe, key, exc, msg
) -> None:
    assert probe[f"{key}2"] == [exc, msg], probe[f"{key}2"]


@pytest.mark.parametrize("key, _exc, _msg", REFUSALS)
def test_a_call_that_succeeds_raises_nothing(probe, key, _exc, _msg) -> None:
    assert probe[f"{key}0"][0] == "ok", probe[f"{key}0"]


def test_a_status_row_takes_the_sentence_as_its_text(probe) -> None:
    """The row picks the class; the kernel's sentence, when it wrote one,
    the text -- and the row's own message when it did not."""
    assert probe["stat3"] == ["KeyError", SAID["stat_row"]]
    assert probe["stat4"] == ["KeyError", "stat row"]


def test_the_exit_finalizer_raises_the_sentence(probe) -> None:
    """``fin()`` and ``__exit__`` call one kernel: odd calls refuse with a
    sentence, even ones without."""
    assert probe["fin1"] == ["ValueError", SAID["fin"]]
    assert probe["fin2"] == ["ValueError", "fin refused (rc=2)"]
    assert probe["exit"] == ["ValueError", SAID["fin"]]


def test_why_is_not_a_python_argument(probe) -> None:
    assert probe["why_kw"] == "TypeError"


# -- a kind = "handle" module (gh-2407) ----------------------------------------

_HB_H = """\
#ifndef HB_H
#define HB_H
#include <stddef.h>
#define HB_E_KEY 9
typedef struct hb hb_t;
hb_t *hb_open(void);
void hb_close(hb_t *h);
int hb_check(hb_t *h, int mode, const char **why);
int hb_push(hb_t *h, const float *x, size_t n, const char **why);
int hb_ngcheck(hb_t *h, int mode, const char **why);
#endif
"""

_HB_C = """\
#include "hb/hb.h"
#include <stdlib.h>
struct hb { int n; };
hb_t *hb_open(void) { return calloc(1, sizeof(hb_t)); }
void hb_close(hb_t *h) { free(h); }
static int refuse(int mode, const char *said, const char **why) {
    if (mode == 1) {
        if (why)
            *why = said;
        return 4;
    }
    if (mode == 2)
        return 4;
    if (mode == 3) {
        if (why)
            *why = said;
        return HB_E_KEY;
    }
    return 0;
}
int hb_check(hb_t *h, int mode, const char **why) {
    (void)h;
    return refuse(mode, "check: mode refused", why);
}
int hb_push(hb_t *h, const float *x, size_t n, const char **why) {
    (void)h;
    return refuse(n ? (int)x[0] : 0, "push: block refused", why);
}
int hb_ngcheck(hb_t *h, int mode, const char **why) {
    (void)h;
    return refuse(mode, "ngcheck: mode refused", why);
}
"""


def _hb_module() -> dict:
    return {
        "kind": "handle",
        "backing": "hb",
        "header": "hb/hb.h",
        "type_name": "Hb",
        "create_fn": "hb_open",
        "close_fn": "hb_close",
        "methods": [
            {
                "name": "check",
                "fn": "hb_check",
                "returns": "int",
                "error": "ValueError",
                "error_message": "check refused",
                "why": True,
                "args": [{"name": "mode", "type": "int"}],
                "status_errors": [{"status": "HB_E_KEY", "error": "KeyError"}],
            },
            {
                "name": "push",
                "fn": "hb_push",
                "returns": "int",
                "status_return": True,
                "error_message": "push refused",
                "why": True,
                "args": [{"name": "x", "type": "float[]"}],
            },
            {
                "name": "ngcheck",
                "fn": "hb_ngcheck",
                "returns": "int",
                "error": "OSError",
                "nogil": True,
                "why": True,
                "args": [{"name": "mode", "type": "int"}],
            },
        ],
    }


def _hb_project(tmp: Path) -> Path:
    from just_makeit import _config as C
    from test_handle_build import _placed

    _ok("new", "hp", "--object", "seed", cwd=tmp)
    proj = tmp / "hp"
    cfg = C.load(proj)
    cfg.setdefault("module", {})["hb"] = _placed(_hb_module(), proj)
    C.save(proj, cfg)
    _ok("apply", cwd=proj)
    return proj


def test_the_handle_binding_passes_the_pointer(tmp_path: Path) -> None:
    ext = _read(_hb_project(tmp_path), "native/src/hb/hb_ext.c")
    for fn in ("hb_check", "hb_push", "hb_ngcheck"):
        calls = re.findall(rf"\b{fn}\(([^;]*?)\);", ext, re.S)
        assert calls and all(c.endswith("&_why") for c in calls), (fn, ext)


@pytest.fixture(scope="module")
def hb(tmp_path_factory):
    from test_handle_build import _CC, _compile_import

    if _CC is None:
        pytest.skip("no C compiler available")
    proj = _hb_project(tmp_path_factory.mktemp("gh2407"))
    return _compile_import(proj, "hb", _HB_H, _HB_C)


def _call(f):
    try:
        f()
        return ["ok"]
    except Exception as e:
        return [type(e).__name__, str(e).strip("'")]


@pytest.mark.parametrize(
    "method, arg, exc, said, msg",
    [("check", lambda m: m, "ValueError", "check: mode refused",
      "check refused (rc=4)"),
     ("push", lambda m: [float(m)], "ValueError", "push: block refused",
      "push refused (rc=4)"),
     ("ngcheck", lambda m: m, "OSError", "ngcheck: mode refused",
      "hb_ngcheck failed (rc=4)")],
)  # fmt: skip
def test_a_handle_method_raises_the_sentence(
    hb, method, arg, exc, said, msg
) -> None:
    import numpy as np

    h = hb.Hb()

    def call(m):
        a = arg(m)
        return getattr(h, method)(
            np.array(a, np.float32) if isinstance(a, list) else a
        )

    assert _call(lambda: call(0)) == ["ok"]
    assert _call(lambda: call(1)) == [exc, said]
    assert _call(lambda: call(2)) == [exc, msg]


def test_a_handle_status_row_takes_the_sentence(hb) -> None:
    h = hb.Hb()
    assert _call(lambda: h.check(3)) == ["KeyError", "check: mode refused"]


def test_a_handle_method_without_a_refusal_is_refused(tmp_path: Path) -> None:
    from just_makeit import _config as C
    from test_handle_build import _placed

    _ok("new", "hp", "--object", "seed", cwd=tmp_path)
    proj = tmp_path / "hp"
    mod = _hb_module()
    mod["methods"] = [
        {"name": "check", "fn": "hb_check", "returns": "int", "why": True,
         "args": [{"name": "mode", "type": "int"}]},
        {"name": "other", "fn": "hb_other", "returns": "int",
         "error": "ValueError", "why": True,
         "args": [{"name": "why", "type": "int"}]},
    ]  # fmt: skip
    cfg = C.load(proj)
    cfg.setdefault("module", {})["hb"] = _placed(mod, proj)
    C.save(proj, cfg)
    r = run_cli("apply", cwd=proj)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "[[module.hb.methods]] 'check' declares why = true, but" in out
    assert "'other' declares why = true and a parameter named" in out
