"""gh-2584: a count method's refusal picks its exception per cause.

``status_out = true`` (``jm method --status-out``) on a method that refuses
a returned COUNT -- ``error_sentinel``, ``error_negative`` or
``error_on_empty`` -- gives its kernel an ``int *status`` out-param, ahead
of gh-2574's ``const char **why`` when both are declared. The binding
initialises the local to 0 and reads it ONLY when the declared refusal
fires: a matching ``status_errors`` row picks the class (and, under
``why``, the kernel's sentence is still the text); a code no row names
falls through to the declared ``error`` / ``error_message``. Success is
decided by the count alone.

The shape doppler's ``Correlator.bank_power_max`` needs (doppler#2752 step
4c): a ``SIZE_MAX`` refusal whose cause is an argument (``ValueError``) or
a scratch ``malloc`` (``MemoryError``).

Every project is built by running jm -- the manifest is the author's,
``jm apply`` writes the header, the stub, the binding and the bench -- and
only the kernel BODIES are written by hand, as an author would. Each
kernel refuses by mode (``in[0]`` or a ``mode`` param):

* 1 -- writes ``RQ_E_ARG`` and, under ``why``, a sentence;
* 2 -- writes ``RQ_E_NOMEM``, no sentence;
* 3 -- writes an unmapped code (99) and, under ``why``, a sentence;
* 4 -- writes nothing, so ``status`` stays 0: unmapped, no sentence;
* anything else succeeds.

Faces: a standalone object (``cor``), a module object (``acc``), a view's
own method (``AccView.vpmax``); a ``kind = "handle"`` method's refusal IS
its returned status, so ``status_out`` is refused there at load.
"""

from __future__ import annotations

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

#: Both rows every method below declares, in this order.
ROWS = (
    '\n[[{t}.status_errors]]\nstatus = "RQ_E_ARG"\nerror = "ValueError"\n'
    '\n[[{t}.status_errors]]\nstatus = "RQ_E_NOMEM"\n'
    'error = "MemoryError"\n'
)

_VO = (
    'arg_type = "float[]"\nreturn_type = "float"\nvariable_output = true\n'
    "max_out = 4\n"
)


def _method(table: str, name: str, keys: str, params: str = "") -> str:
    """One method entry: *keys*, the common refusal keys, its params and
    the two `ROWS`."""
    return (
        f"\n[[{table}]]\nname = {name!r}\n{keys}"
        'error = "RuntimeError"\n'
        f'error_message = "{name} refused"\n'
        "status_out = true\n" + params + ROWS.format(t=table)
    ).replace("'", '"')


_MODE = '\n[[{t}.params]]\nname = "mode"\ntype = "int"\n'

#: (name, manifest keys, extra params, whether it declares `why`)
COR = [
    ("pmax", _VO + 'error_sentinel = "SIZE_MAX"\nwhy = true\n', ""),
    (
        "pcap",
        _VO + 'error_sentinel = "SIZE_MAX"\npass_capacity = true\n'
        "why = true\n",
        "",
    ),
    ("vneg", _VO + 'count_type = "int64_t"\nerror_negative = true\n', ""),
    ("vemp", _VO + "error_on_empty = true\nnogil = true\nwhy = true\n", ""),
    # doppler's `bank_power_max` itself: no block input, array params,
    # extra outputs, the capacity, the GIL released.
    (
        "bank",
        'arg_type = "void"\nreturn_type = "float"\nvariable_output = true\n'
        'max_out = 4\npass_capacity = true\nmulti_output = ["uint32_t"]\n'
        'error_sentinel = "SIZE_MAX"\nnogil = true\nwhy = true\n',
        '\n[[{t}.params]]\nname = "x"\ntype = "float[]"\n'
        '\n[[{t}.params]]\nname = "L"\ntype = "size_t"\n',
    ),
    (
        "vmo",
        _VO + 'error_sentinel = "SIZE_MAX"\nmulti_output = ["float"]\n'
        "why = true\n",
        "",
    ),
    (
        "vrec",
        'arg_type = "float[]"\nreturn_type = "float"\n'
        "variable_output = true\nmax_out = 4\n"
        'record_dtype = "div_t"\n'
        'result_fields = [{name = "quot", type = "int"}, '
        '{name = "rem", type = "int"}]\n'
        'error_sentinel = "SIZE_MAX"\nwhy = true\n',
        "",
    ),
    (
        "fill",
        'arg_type = "void"\nreturn_type = "size_t"\n'
        'error_sentinel = "SIZE_MAX"\nwhy = true\n',
        '\n[[{t}.params]]\nname = "buf"\ntype = "float[]"\nout = true\n'
        + _MODE,
    ),
    (
        "neg",
        'arg_type = "void"\nreturn_type = "int"\nerror_negative = true\n'
        "why = true\n",
        _MODE,
    ),
]


def _toml() -> "tuple[str, str]":
    cor = '[cor]\narg_type = "float"\nreturn_type = "float"\n'
    for name, keys, params in COR:
        t = "cor.methods"
        cor += _method(t, name, keys, params.format(t=t))
    acc = '[acc]\narg_type = "float"\nreturn_type = "float"\n'
    acc += _method(
        "acc.methods",
        "pmax",
        _VO + 'error_sentinel = "SIZE_MAX"\nnogil = true\nwhy = true\n',
    )
    acc += (
        '\n[[acc.views]]\nclass_name = "AccView"\n'
        'create_fn = "rq_acc_create_view"\n'
    )
    acc += _method(
        "acc.views.methods",
        "vpmax",
        'fn = "rq_acc_vpmax"\n'
        + _VO
        + 'error_sentinel = "SIZE_MAX"\nwhy = true\n',
    )
    return cor, acc


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
    cor, acc = _toml()
    (proj / "objects" / "cor.toml").write_text(cor, encoding="utf-8")
    (proj / "objects" / "acc.toml").write_text(acc, encoding="utf-8")
    mod = proj / "modules" / "dsp.toml"
    text = mod.read_text(encoding="utf-8")
    assert text.count("objects = []") == 1, text
    mod.write_text(text.replace("objects = []", 'objects = ["acc"]'), "utf-8")
    _ok("apply", cwd=proj)
    return proj


@pytest.fixture(scope="module")
def scaffold(tmp_path_factory) -> Path:
    return _project(tmp_path_factory.mktemp("gh2584"))


def _read(proj: Path, rel: str) -> str:
    return (proj / rel).read_text(encoding="utf-8")


def _kernels() -> "list[str]":
    return [f"rq_cor_{n}" for n, _k, _p in COR] + [
        "rq_acc_pmax",
        "rq_acc_vpmax",
    ]


def _declares_why(fn: str) -> bool:
    return fn != "rq_cor_vneg"


# -- the scaffold jm writes ----------------------------------------------------


def test_the_scaffold_is_clean(scaffold: Path) -> None:
    r = run_cli("status", "--check", cwd=scaffold)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("fn", _kernels())
def test_header_and_stub_take_status_before_why(
    scaffold: Path, fn: str
) -> None:
    """The prototype jm declares and the stub it scaffolds end in
    ``int *status`` -- then ``const char **why`` when it is declared."""
    comp = fn.split("_")[1]
    tail = ["int*status"] + ["constchar**why"] * _declares_why(fn)
    found = [
        (s.kind, [p.replace(" ", "") for p in s.params.split(",")])
        for rel in (
            f"native/inc/rq/{comp}/{comp}_core.h",
            f"native/src/{comp}/{comp}_core.c",
        )
        for s in _ctorsig.signatures(_read(scaffold, rel))
        if s.name == fn
    ]
    assert sorted(k for k, _ in found) == ["decl", "def"], found
    for _kind, params in found:
        assert params[-len(tail) :] == tail, (fn, params)


@pytest.mark.parametrize("fn", _kernels())
def test_the_binding_passes_status_before_why(scaffold: Path, fn: str) -> None:
    """Every call the binding makes to the kernel ends in ``&_status`` (and
    ``&_why``) -- both call paths of a variable_output method."""
    ext = "".join(
        p.read_text("utf-8")
        for p in sorted((scaffold / "native/src").rglob("*_ext*.c"))
    )
    calls = re.findall(rf"\b{fn}\(([^;]*?)\);", ext, re.S)
    assert calls, fn
    want = "&_status, &_why" if _declares_why(fn) else "&_status"
    for args in calls:
        assert args.rstrip().endswith(want), (fn, args)


def test_the_bench_passes_a_status_local(scaffold: Path) -> None:
    bench = _read(scaffold, "native/benchmarks/bench_cor_core.c")
    assert re.search(r"rq_cor_neg\(obj, 0, &neg_status, NULL\)", bench), bench


def test_the_pyi_lists_each_row_class(scaffold: Path) -> None:
    """The rows raise ahead of the declared class, so each is documented,
    then the declared refusal, in the order the binding tests them."""
    pyi = _read(scaffold, "src/rq/cor.pyi")
    body = pyi[pyi.index("def pmax(") :]
    body = " ".join(body[: body.index("def ", 10)].split())
    raises = body[body.index("Raises") :]
    order = [raises.index(c) for c in ("ValueError", "MemoryError")]
    assert order == sorted(order), raises
    assert raises.index("MemoryError") < raises.index("RuntimeError"), raises
    assert "refuses and reports ``RQ_E_NOMEM`` in ``*status``" in raises


def test_script_replays_the_flag(tmp_path: Path) -> None:
    """``jm method --status-out`` writes the key, and ``jm script`` replays
    it."""
    _ok("new", "sq", cwd=tmp_path)
    proj = tmp_path / "sq"
    _ok("object", "cor", cwd=proj)
    _ok(
        "method", "cor", "pmax", "--arg-type", "float[]", "--return-type",
        "float", "--variable-output", "--error-sentinel", "SIZE_MAX",
        "--status-out", "--status-error", "SQ_E_NOMEM:MemoryError",
        cwd=proj,
    )  # fmt: skip
    assert "--status-out" in _ok("script", cwd=proj)
    orig, replayed = script_round_trip(proj, tmp_path / "replay")
    assert orig["cor"]["methods"][0]["status_out"] is True
    assert orig == replayed


# -- the header jm does not rewrite --------------------------------------------

_ACC_DECL = (
    "size_t rq_acc_pmax(rq_acc_state_t *state, const float *in, "
    "size_t n_in, float *out"
)


def _drop_status(
    scaffold: Path, tmp_path: Path, keep: str = ", const char **why"
) -> Path:
    """A copy of *scaffold* whose ``rq_acc_pmax`` declaration ends in
    *keep* instead of the manifest's ``int *status, const char **why``."""
    proj = tmp_path / "rq"
    shutil.copytree(scaffold, proj)
    hdr = proj / "native/inc/rq/acc/acc_core.h"
    text = hdr.read_text("utf-8")
    full = f"{_ACC_DECL}, int *status, const char **why);"
    assert text.count(full) == 1, text
    hdr.write_text(text.replace(full, f"{_ACC_DECL}{keep});"), "utf-8")
    return proj


def test_a_header_without_status_is_a_finding(
    scaffold: Path, tmp_path: Path
) -> None:
    """A module object's sacred header only gains declarations on `apply`,
    so a method that takes ``status_out`` later keeps its old prototype
    there -- `status` names it (WHY), as it names a missing ``why``."""
    proj = _drop_status(scaffold, tmp_path)
    r = run_cli("status", "--check", cwd=proj)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "WHY (1)" in out and "rq_acc_pmax(" in out, out


@pytest.mark.parametrize(
    "keep, remedy",
    [(", const char **why",
      "Add `int *status` before `const char **why` in the declaration"),
     ("", "Add `int *status, const char **why` last to the declaration"),
     (", int *status", "Add `const char **why` last to the declaration")],
    ids=["status-only", "both", "why-only"],
)  # fmt: skip
def test_apply_warns_by_what_the_header_lacks(
    scaffold: Path, tmp_path: Path, keep: str, remedy: str
) -> None:
    """The warning names what is missing, in the order jm appends it: a
    ``status`` missing beside a present ``why`` goes BEFORE it."""
    proj = _drop_status(scaffold, tmp_path, keep)
    r = run_cli("apply", cwd=proj)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    warned = [ln for ln in out.splitlines() if ln.startswith("warning")]
    assert len(warned) == 1 and "rq_acc_pmax()" in warned[0], out
    assert remedy in " ".join(out.split()), out


# -- refused at load -----------------------------------------------------------


def _refusal(tmp_path: Path, toml: str) -> str:
    _ok("new", "rz", "--object", "seed", cwd=tmp_path)
    proj = tmp_path / "rz"
    (proj / "objects" / "cor.toml").write_text(toml, encoding="utf-8")
    r = run_cli("apply", cwd=proj)
    assert r.returncode != 0, r.stdout + r.stderr
    return r.stdout + r.stderr


_PLAIN = '[cor]\narg_type = "float"\nreturn_type = "float"\n' + _method(
    "cor.methods", "pmax", _VO + 'error_sentinel = "SIZE_MAX"\n'
)


def test_the_plain_declaration_loads(tmp_path: Path) -> None:
    """The base every refusal below edits one key of is itself accepted."""
    _ok("new", "rz", "--object", "seed", cwd=tmp_path)
    proj = tmp_path / "rz"
    (proj / "objects" / "cor.toml").write_text(_PLAIN, encoding="utf-8")
    _ok("apply", cwd=proj)


def test_status_out_without_a_count_refusal_is_refused(tmp_path) -> None:
    out = _refusal(
        tmp_path, _PLAIN.replace('error_sentinel = "SIZE_MAX"\n', "")
    )
    assert "'pmax' declares status_out = true, but no count refusal" in out


def test_status_out_with_status_return_is_refused(tmp_path) -> None:
    toml = _PLAIN.replace(_VO, 'arg_type = "void"\nreturn_type = "int"\n')
    toml = toml.replace(
        'error_sentinel = "SIZE_MAX"\n', "status_return = true\n"
    )
    out = _refusal(tmp_path, toml)
    assert "declares status_out = true, but its status is its return" in out


def test_status_out_without_rows_is_refused(tmp_path: Path) -> None:
    out = _refusal(tmp_path, _PLAIN.replace(ROWS.format(t="cor.methods"), ""))
    assert "declares status_out = true, but no `status_errors` rows" in out
    assert "Declare `status_errors` rows, or drop status_out." in out


def test_the_cli_refuses_status_out_without_rows(tmp_path: Path) -> None:
    _ok("new", "sq", cwd=tmp_path)
    proj = tmp_path / "sq"
    _ok("object", "cor", cwd=proj)
    before = sorted(p.as_posix() for p in proj.rglob("*"))
    r = run_cli(
        "method", "cor", "pmax", "--arg-type", "float[]", "--return-type",
        "float", "--variable-output", "--error-sentinel", "SIZE_MAX",
        "--status-out", cwd=proj,
    )  # fmt: skip
    assert r.returncode != 0, r.stdout
    assert "but no `status_errors` rows" in r.stderr
    assert sorted(p.as_posix() for p in proj.rglob("*")) == before


def test_a_non_bool_status_out_is_refused(tmp_path: Path) -> None:
    out = _refusal(
        tmp_path, _PLAIN.replace("status_out = true", 'status_out = "yes"')
    )
    assert "status_out = 'yes' must be true or false" in out, out


def test_a_param_named_status_is_refused(tmp_path: Path) -> None:
    out = _refusal(
        tmp_path,
        _PLAIN.replace(
            "status_out = true\n",
            'status_out = true\n\n[[cor.methods.params]]\nname = "status"\n'
            'type = "int"\n',
        ),
    )
    assert "declares status_out = true and a parameter named `status`" in out


def test_a_view_inheriting_a_param_named_status_is_refused(
    tmp_path: Path,
) -> None:
    """A view's override of a parent method takes the parent's params, so
    a parent param named ``status`` meets the override's ``status``."""
    base = _PLAIN.replace("status_out = true\n", "").replace(
        ROWS.format(t="cor.methods"),
        '\n[[cor.methods.params]]\nname = "status"\ntype = "int"\n',
    )
    toml = (
        base
        + '\n[[cor.views]]\nclass_name = "V"\ncreate_fn = "rz_cor_create_v"\n'
        '\n[[cor.views.methods]]\nname = "pmax"\nstatus_out = true\n'
    )
    out = _refusal(tmp_path, toml)
    assert "'V' method 'pmax' declares status_out = true and a param" in out


def test_the_cli_refuses_before_writing(tmp_path: Path) -> None:
    _ok("new", "sq", cwd=tmp_path)
    proj = tmp_path / "sq"
    _ok("object", "cor", cwd=proj)
    before = sorted(p.as_posix() for p in proj.rglob("*"))
    r = run_cli(
        "method", "cor", "pmax", "--arg-type", "float[]", "--return-type",
        "float", "--variable-output", "--status-out", cwd=proj,
    )  # fmt: skip
    assert r.returncode != 0, r.stdout
    assert "declares status_out = true, but no count refusal" in r.stderr
    assert sorted(p.as_posix() for p in proj.rglob("*")) == before


def test_a_handle_method_is_refused(tmp_path: Path) -> None:
    """A handle method's refusal is its non-zero ``int`` return -- that IS
    the status, which its ``status_errors`` rows already match."""
    from just_makeit import _config as C
    from test_handle_build import _placed

    _ok("new", "hp", "--object", "seed", cwd=tmp_path)
    proj = tmp_path / "hp"
    mod = {
        "kind": "handle",
        "backing": "hb",
        "header": "hb/hb.h",
        "type_name": "Hb",
        "create_fn": "hb_open",
        "close_fn": "hb_close",
        "methods": [
            {"name": "check", "fn": "hb_check", "returns": "int",
             "error": "ValueError", "status_out": True,
             "args": [{"name": "mode", "type": "int"}]},
        ],
    }  # fmt: skip
    cfg = C.load(proj)
    cfg.setdefault("module", {})["hb"] = _placed(mod, proj)
    C.save(proj, cfg)
    r = run_cli("apply", cwd=proj)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert (
        "[[module.hb.methods]] 'check' declares status_out = true, but its "
        "status is its return value"
    ) in out, out


# -- one emitter ---------------------------------------------------------------


def test_without_the_key_the_render_is_unchanged() -> None:
    """``status`` absent renders every raise byte for byte as before."""
    for why in ("", "_why"):
        assert D._rc_raise_c("ValueError", "m", why=why) == D._rc_raise_c(
            "ValueError", "m", why=why, status=""
        )
        assert D.empty_raise_c(
            "ValueError", "m", "Py_DECREF(a); ", why=why
        ) == D.empty_raise_c(
            "ValueError", "m", "Py_DECREF(a); ", why=why, status=""
        )


# -- built and run -------------------------------------------------------------


def _refuse_c(said: str, refuse: str, why: bool) -> str:
    """The refusals every kernel shares, by *mode*."""

    def say(s):
        return f'        if (why)\n            *why = "{s}";\n' if why else ""

    return (
        "    if (mode == 1) {\n        *status = RQ_E_ARG;\n"
        + say(f"{said}: bad argument")
        + f"        return {refuse};\n    }}\n"
        "    if (mode == 2) {\n        *status = RQ_E_NOMEM;\n"
        f"        return {refuse};\n    }}\n"
        "    if (mode == 3) {\n        *status = 99;\n"
        + say(f"{said}: unmapped")
        + f"        return {refuse};\n    }}\n"
        f"    if (mode == 4)\n        return {refuse};\n"
    )


_VO_MODE = "    (void)state;\n    int mode = n_in ? (int)in[0] : 0;\n"

BODIES = {
    "rq_cor_pmax": _VO_MODE
    + _refuse_c("pmax", "SIZE_MAX", True)
    + "    out[0] = 4.0f;\n    return 1;\n",
    "rq_cor_pcap": _VO_MODE
    + "    (void)max_out;\n"
    + _refuse_c("pcap", "SIZE_MAX", True)
    + "    out[0] = 4.0f;\n    return 1;\n",
    "rq_cor_vneg": _VO_MODE
    + _refuse_c("vneg", "-2", False)
    + "    out[0] = 4.0f;\n    return 1;\n",
    "rq_cor_vemp": _VO_MODE
    + _refuse_c("vemp", "0", True)
    + "    out[0] = 4.0f;\n    return 1;\n",
    "rq_cor_vmo": _VO_MODE
    + _refuse_c("vmo", "SIZE_MAX", True)
    + "    out[0] = 4.0f;\n    out1[0] = 5.0f;\n    return 1;\n",
    "rq_cor_vrec": _VO_MODE
    + _refuse_c("vrec", "SIZE_MAX", True)
    + "    out[0].quot = 4;\n    out[0].rem = 5;\n    return 1;\n",
    "rq_cor_fill": "    (void)state;\n"
    + _refuse_c("fill", "SIZE_MAX", True)
    + "    if (buf_len)\n        buf[0] = 4.0f;\n    return 1;\n",
    "rq_cor_bank": "    (void)state;\n    (void)L;\n    (void)max_out;\n"
    "    int mode = x_len ? (int)x[0] : 0;\n"
    + _refuse_c("bank", "SIZE_MAX", True)
    + "    out[0] = 4.0f;\n    out1[0] = 5u;\n    return 1;\n",
    "rq_cor_neg": "    (void)state;\n"
    + _refuse_c("neg", "-1", True)
    + "    return 5;\n",
    # The view's constructor: the scaffold's returns NULL.
    "rq_acc_create_view": "    return calloc(1, sizeof(rq_acc_state_t));\n",
    "rq_acc_pmax": _VO_MODE
    + _refuse_c("acc.pmax", "SIZE_MAX", True)
    + "    out[0] = 4.0f;\n    return 1;\n",
    "rq_acc_vpmax": _VO_MODE
    + _refuse_c("AccView.vpmax", "SIZE_MAX", True)
    + "    out[0] = 4.0f;\n    return 1;\n",
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


def _codes(hdr: Path) -> None:
    """The author's status codes, in the sacred header the binding
    includes."""
    text = hdr.read_text("utf-8")
    hdr.write_text(
        text.replace(
            "#include", "#define RQ_E_ARG 1\n#define RQ_E_NOMEM 2\n#include", 1
        ),
        "utf-8",
    )


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
    proj = _project(tmp_path_factory.mktemp("gh2584b"))
    _codes(proj / "native/inc/rq/cor/cor_core.h")
    _codes(proj / "native/inc/rq/acc/acc_core.h")
    _give_bodies(
        proj / "native/src/cor/cor_core.c",
        [f for f in BODIES if f.startswith("rq_cor_")],
    )
    _give_bodies(
        proj / "native/src/acc/acc_core.c",
        [f for f in BODIES if f.startswith("rq_acc_")],
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
calls = {
    "pmax": lambda m: c.pmax(x(m)),
    "pmax_out": lambda m: c.pmax(x(m), out=np.zeros(4, np.float32)),
    "pcap": lambda m: c.pcap(x(m)),
    "pcap_out": lambda m: c.pcap(x(m), out=np.zeros(4, np.float32)),
    "vneg": lambda m: c.vneg(x(m)),
    "vneg_out": lambda m: c.vneg(x(m), out=np.zeros(4, np.float32)),
    "vemp": lambda m: c.vemp(x(m)),
    "vemp_out": lambda m: c.vemp(x(m), out=np.zeros(4, np.float32)),
    "vmo": lambda m: c.vmo(x(m)),
    "vrec": lambda m: c.vrec(x(m)),
    "fill": lambda m: c.fill(np.zeros(2, np.float32), m),
    "neg": lambda m: c.neg(m),
    "bank": lambda m: c.bank(x(m), 3),
    "acc.pmax": lambda m: a.pmax(x(m)),
    "acc.pmax_out": lambda m: a.pmax(x(m), out=np.zeros(4, np.float32)),
    "AccView.vpmax": lambda m: v.vpmax(x(m)),
    "AccView.vpmax_out": lambda m: v.vpmax(x(m), out=np.zeros(4, np.float32)),
}
out = {f"{k}{m}": run(lambda: f(m)) for k, f in calls.items()
       for m in range(5)}
try:
    c.pmax(x(0), status=None)
    out["status_kw"] = "accepted"
except TypeError:
    out["status_kw"] = "TypeError"
print(json.dumps(out, default=lambda o: o.tolist()))
"""


@pytest.fixture(scope="module")
def probe(built: Path) -> dict:
    r = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=built, capture_output=True,
        text=True,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


#: Every probed call: (key, whether it declares `why`, the `(rc=N)` its
#: declared raise appends -- an `error_negative` count's code).
CALLS = [
    ("pmax", True, ""),
    ("pmax_out", True, ""),
    ("pcap", True, ""),
    ("pcap_out", True, ""),
    ("vneg", False, " (rc=-2)"),
    ("vneg_out", False, " (rc=-2)"),
    ("vemp", True, ""),
    ("vemp_out", True, ""),
    ("vmo", True, ""),
    ("vrec", True, ""),
    ("fill", True, ""),
    ("neg", True, " (rc=-1)"),
    ("bank", True, ""),
    ("acc.pmax", True, ""),
    ("acc.pmax_out", True, ""),
    ("AccView.vpmax", True, ""),
    ("AccView.vpmax_out", True, ""),
]


def _said(key: str) -> str:
    return key.removesuffix("_out")


@pytest.mark.parametrize("key, why, _rc", CALLS)
def test_an_argument_code_raises_its_row(probe, key, why, _rc) -> None:
    """``RQ_E_ARG`` picks the ``ValueError`` row; the kernel's sentence,
    when it wrote one, is the text."""
    name = _said(key).rsplit(".", 1)[-1]
    text = (
        f"{_said(key)}: bad argument" if why else f"{name} failed (RQ_E_ARG)"
    )
    assert probe[f"{key}1"] == ["ValueError", text], probe[f"{key}1"]


@pytest.mark.parametrize("key, _why, _rc", CALLS)
def test_the_memory_code_raises_memory_error(probe, key, _why, _rc) -> None:
    name = _said(key).rsplit(".", 1)[-1]
    assert probe[f"{key}2"] == [
        "MemoryError",
        f"{name} failed (RQ_E_NOMEM)",
    ], probe[f"{key}2"]


@pytest.mark.parametrize("key, why, rc", CALLS)
def test_an_unmapped_code_raises_the_declared_error(probe, key, why, rc):
    """A cause no row names is the declared ``error`` -- with the kernel's
    sentence when it wrote one (3), the declared message when not (3
    without ``why``, and 4, where ``status`` stayed 0)."""
    name = _said(key).rsplit(".", 1)[-1]
    declared = f"{name} refused{rc}"
    said = f"{_said(key)}: unmapped" if why else declared
    assert probe[f"{key}3"] == ["RuntimeError", said], probe[f"{key}3"]
    assert probe[f"{key}4"] == ["RuntimeError", declared], probe[f"{key}4"]


@pytest.mark.parametrize("key, _why, _rc", CALLS)
def test_a_call_that_succeeds_raises_nothing(probe, key, _why, _rc) -> None:
    assert probe[f"{key}0"][0] == "ok", probe[f"{key}0"]


def test_status_is_not_a_python_argument(probe) -> None:
    assert probe["status_kw"] == "TypeError"
