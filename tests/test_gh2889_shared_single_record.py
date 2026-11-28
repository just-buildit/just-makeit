"""gh-2889: two objects' ``single`` methods share one declared record.

doppler's ``AsyncDsssReceiver.status()`` and ``AsyncDsssPool.status(rx)``
both return ``async_dsss_receiver_status_t`` by value, and each manifest
carried its own 18-row ``result_fields`` for it: a field added to the
struct had to be added twice, and nothing tied the copies. A record was
per component (`_config.records`) and only ``record_dtype`` read one.

Now a ``single`` method's ``return_type`` struct, when declared
(``[[<obj>.records]]``) by ANY component, supplies its columns: one
resolver (`_record.resolved` over `_config.record_scope`) that every face
reads -- the binding, both ``.pyi`` producers, the record registrations,
`jm script`, `apply`'s replay, `status` and the CLI's ``--single``. A
struct is declared once per project, a method restating its columns is
refused, and `jm record` re-renders every component returning it. Each
binding keeps its own Python type name.

GATE: a compiled fixture -- the struct declared by a MODULE object, returned
      by it and by a STANDALONE object -- where one `jm record` edit adding
      a column reaches both bindings at runtime and both stubs, with
      `status --check` at 0, and `jm script` and `apply` agree; the
      refusals (two declarations, a restatement either way round); and
      every caller of the resolver is a face this file names.
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

from _compilers import default_cc
from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _textio

SRC = Path(__file__).resolve().parents[1] / "src" / "just_makeit"
STRUCT = "rx_status_t"
COLS = ("--field", "locked:int", "--field", "snr:double")


def _ok(root: Path, *argv: str) -> str:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (argv, (r.stdout + r.stderr)[-3000:])
    return r.stdout


def _clean(root: Path) -> None:
    s = run_cli("status", "--check", cwd=root)
    assert s.returncode == 0, s.stdout + s.stderr


def _typedef(root: Path, cols: "list[tuple[str, str]]") -> None:
    """The author's half: the struct, in the declaring object's header."""
    header = root / "native" / "inc" / "sr" / "rx" / "rx_core.h"
    text = header.read_text(encoding="utf-8")
    body = "".join(f"    {t} {n};\n" for n, t in cols)
    block = (
        f"/* {STRUCT} */\ntypedef struct {{\n{body}}} {STRUCT};\n/* end */\n"
    )
    old = re.compile(rf"/\* {STRUCT} \*/\n.*?/\* end \*/\n", re.S)
    if old.search(text):
        text = old.sub(lambda _m: block, text)
    else:
        text = text.replace("typedef struct", block + "\ntypedef struct", 1)
    _textio.write_text(header, text)


def _project(tmp_path: Path) -> Path:
    """`rx` in module `m` declares the struct; it and standalone `pool`
    (which depends on `rx`, for its header) both return it."""
    _ok(tmp_path, "new", "sr")
    root = tmp_path / "sr"
    _ok(root, "module", "m")
    _ok(root, "object", "rx", "--module", "m")
    _ok(root, "record", "rx", STRUCT, *COLS)
    _ok(root, "method", "rx", "status", "--module", "m", "--single",
        "--return-type", STRUCT, "--arg-type", "void")  # fmt: skip
    _ok(root, "object", "pool")
    cfg = C.load(root)
    cfg["pool"]["depends_on"] = ["rx"]
    C.save(root, cfg)
    _ok(root, "apply")
    _ok(root, "method", "pool", "status", "--single", "--return-type",
        STRUCT, "--arg-type", "void")  # fmt: skip
    # A module function returning the same struct; the module's header
    # includes the declaring object's, the author's edit for a foreign type.
    _ok(root, "function", "rx_report", "--module", "m", "--single",
        "--return-type", STRUCT)  # fmt: skip
    mh = root / "native" / "inc" / "sr" / "m" / "m_core.h"
    _textio.write_text(
        mh,
        mh.read_text(encoding="utf-8").replace(
            '#include "sr/clib_common.h"',
            '#include "sr/clib_common.h"\n#include "sr/rx/rx_core.h"',
            1,
        ),
    )
    _typedef(root, [("locked", "int"), ("snr", "double")])
    _clean(root)
    return root


STUBS = ("src/sr/m/m.pyi", "src/sr/pool.pyi")
BINDINGS = (
    "native/src/m/m_ext_rx.c",
    "native/src/pool/pool_ext.c",
    "native/src/m/m_ext.c",  # the module function's wrapper
)


def test_the_manifest_states_the_columns_once(tmp_path):
    root = _project(tmp_path)
    cfg = C.load(root)
    for comp in ("rx", "pool"):
        (m,) = C.methods(cfg, comp)
        assert "result_fields" not in m, m
    (fn,) = C.module_functions(cfg, "m")
    assert "result_fields" not in fn, fn
    for rel in STUBS:
        text = (root / rel).read_text(encoding="utf-8")
        assert "def snr(self) -> float" in text, (rel, text)
        # The member returns the record class, not the fallback type.
        assert "def status(self) -> RxStatus" in text, (rel, text)
    mod = (root / STUBS[0]).read_text(encoding="utf-8")
    assert "def rx_report() -> RxStatus" in mod, mod
    # ...and the functions' contract test asserts the record (gh-2127).
    contract = root / "src" / "sr" / "m" / "tests" / "test_m_functions.py"
    assert "assertIsInstance(result, module.RxStatus)" in contract.read_text(
        encoding="utf-8"
    )


def test_one_record_edit_reaches_both_faces(tmp_path):
    root = _project(tmp_path)
    _ok(root, "record", "rx", STRUCT, *COLS, "--field", "lag:int")
    _typedef(root, [("locked", "int"), ("snr", "double"), ("lag", "int")])
    _clean(root)
    for rel in STUBS:
        assert "def lag(self) -> int" in (root / rel).read_text(
            encoding="utf-8"
        ), rel
    for rel in BINDINGS:
        assert "_r.lag" in (root / rel).read_text(encoding="utf-8"), rel


def test_script_and_apply_agree(tmp_path):
    """`jm script` replays to the same manifest, and `apply` on the
    project changes nothing."""
    root = _project(tmp_path)
    before = {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and "build" not in p.parts
    }
    _ok(root, "apply")
    after = {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and "build" not in p.parts
    }
    assert [k for k in after if after[k] != before.get(k)] == []
    script = _ok(root, "script")
    assert f"record rx {STRUCT}" in script, script
    lines = [ln for ln in script.splitlines() if ln.startswith("just-makeit")]
    rec = next(i for i, ln in enumerate(lines) if f"record rx {STRUCT}" in ln)
    pool = next(i for i, ln in enumerate(lines) if "method pool status" in ln)
    assert rec < pool, "the pool's method replays before the record"
    assert "--result-field" not in script, script


def test_a_struct_declared_twice_is_refused(tmp_path):
    root = _project(tmp_path)
    r = run_cli("record", "pool", STRUCT, *COLS, cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "is declared by both" in r.stderr, r.stderr


def test_a_restatement_is_refused_either_way_round(tmp_path):
    root = _project(tmp_path)
    restate = ("--result-field", "locked:int", "--result-field", "snr:double")
    r = run_cli(
        "method", "pool", "status2", "--single", "--return-type", STRUCT,
        "--arg-type", "void", *restate, cwd=root,
    )  # fmt: skip
    assert r.returncode == 1, r.stdout + r.stderr
    assert "already declares its columns" in r.stderr, r.stderr
    r = run_cli(
        "function", "f2", "--module", "m", "--single", "--return-type",
        STRUCT, *restate, cwd=root,
    )  # fmt: skip
    assert r.returncode == 1, r.stdout + r.stderr
    assert "already declares its columns" in r.stderr, r.stderr

    # The other way round: a method that states them, then the declaration.
    _ok(tmp_path, "new", "q")
    q = tmp_path / "q"
    _ok(q, "object", "a")
    _ok(q, "method", "a", "st", "--single", "--return-type", "a_st_t",
        "--arg-type", "void", "--result-field", "n:int")  # fmt: skip
    r = run_cli("record", "a", "a_st_t", "--field", "n:int", cwd=q)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "'a.st'" in r.stderr and "gh-2889" in r.stderr, r.stderr


def test_single_without_columns_or_declaration_is_refused(tmp_path):
    _ok(tmp_path, "new", "q")
    q = tmp_path / "q"
    _ok(q, "object", "a")
    r = run_cli("method", "a", "st", "--single", "--return-type", "a_st_t",
                "--arg-type", "void", cwd=q)  # fmt: skip
    assert r.returncode == 1, r.stdout + r.stderr
    assert "just-makeit record <obj> a_st_t" in r.stderr, r.stderr


#: Every function that calls the resolver, and the face it is. Held to the
#: source both ways: a new caller fails here until it is named, and one
#: that stops calling it fails until it is dropped.
FACES = {
    "_cli_function.py:run": "jm function --single",
    "_cli_method.py:run": "jm method --single",
    "_coerce.py:str_hint_rows": "an input record's columns, in scope",
    "_composer.py:_delegation_refusal": "a delegated generator method",
    "_composer.py:_source_delegated": "a delegated method's render scope",
    "_config.py:function_record_errors": "load: a function's single",
    "_config.py:manifest_type_errors": "load: a declared struct return type",
    "_config.py:module_record_regs": "the module's record registrations",
    "_config.py:resolved_functions": "the function-table form",
    "_config.py:resolved_methods": "the method-table form",
    "_context/_methods.py:make_methods_ctx": "a method binding and .pyi",
    "_docgaps.py:gaps": "status --docs",
    "_fncontract.py:sync": "the functions' contract test",
    "_function.py:run": "jm function / apply's replay",
    "_glue.py:component_ctx": "a standalone re-render's scope",
    "_init.py:run": "a standalone render's scope",
    "_method.py:run": "jm method / apply's replay",
    "_object.py:_make_view_ctx": "a view binding's scope",
    "_object.py:_regenerate_module_now": "a module's function binding",
    "_object.py:build_component_ctxs": "a module object binding's scope",
    "_object.py:jm_owned_functions": "a shared record's wrapper is jm's",
    "_object.py:render_module_ext_c": "the module's function records",
    "_stubs.py:_obj_stub": "a module stub's class",
    "_stubs.py:make_module_pyi": "the module stub's records and functions",
}

_RESOLVER = {
    "resolved",
    "resolved_methods",
    "resolved_functions",
    "record_scope",
    "declared_struct",
}


def _callers() -> "set[str]":
    out = set()
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if "templates" in rel or rel == "_record.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for n in ast.walk(fn):
                if not isinstance(n, ast.Call):
                    continue
                name = (
                    n.func.attr
                    if isinstance(n.func, ast.Attribute)
                    else getattr(n.func, "id", "")
                )
                if name in _RESOLVER:
                    out.add(f"{rel}:{fn.name}")
                    break
    return out


def test_every_caller_of_the_resolver_is_a_named_face():
    assert _callers() == set(FACES)


def _no_toolchain() -> "str | None":
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    return None


_DRIVER = """\
import json, sys
sys.path.insert(0, "src")
from sr.m import Rx
from sr.pool import Pool
out = {}
from sr.m import rx_report
for name, call in (("Rx", lambda: Rx().status()),
                   ("Pool", lambda: Pool().status()),
                   ("rx_report", rx_report)):
    r = call()
    out[name] = [type(r).__name__, [repr(v) for v in r],
                 [repr(getattr(r, f)) for f in ("locked", "snr", "lag")]]
print(json.dumps(out))
"""


@pytest.mark.slow
@pytest.mark.skipif(bool(_no_toolchain()), reason=str(_no_toolchain()))
def test_compiled_one_edit_reaches_both_bindings(tmp_path):
    """doppler's acceptance, compiled: a column added once is in both
    objects' records at runtime -- each a whole record, under its own
    module's type -- and the generated tests pass."""
    root = _project(tmp_path)
    _ok(root, "record", "rx", STRUCT, *COLS, "--field", "lag:int")
    _typedef(root, [("locked", "int"), ("snr", "double"), ("lag", "int")])
    _clean(root)
    r = run_cli("test", cwd=root)
    assert r.returncode == 0, (r.stdout + r.stderr)[-4000:]
    driver = root / "drive.py"
    _textio.write_text(driver, _DRIVER)
    p = subprocess.run(
        [sys.executable, str(driver)], cwd=root, capture_output=True, text=True
    )
    assert p.returncode == 0, p.stdout + p.stderr
    got = json.loads(p.stdout)
    # Each a whole record: the module object's wrapper was restored from
    # the fragment (gh-770) and filled the OLD columns, so `lag` read
    # `<NULL>` while the stub promised it (`jm_owned_functions`).
    for face in ("Rx", "Pool", "rx_report"):
        name, values, named = got[face]
        assert name == "RxStatus", got
        assert len(values) == 3 and "<NULL>" not in values, got
        assert named == ["0", "0.0", "0"], got


def test_script_orders_a_producers_record_first(tmp_path):
    """A producer -- a component an `object` init-param names -- has its
    members emitted right after its object line (gh-1893), ahead of the
    records section. When its method returns another component's
    declared struct, that component is a producer of it too, so the
    `jm record` line runs first and the script replays."""
    root = _project(tmp_path)
    _ok(root, "property", "pool", "_capsule", "--type", "capsule",
        "--capsule", "sr.pool")  # fmt: skip
    _ok(root, "object", "user", "--init-param", "p:object:pool")
    script = _ok(root, "script")
    lines = [ln for ln in script.splitlines() if ln.startswith("just-makeit")]
    rec = next(i for i, ln in enumerate(lines) if f"record rx {STRUCT}" in ln)
    pool = next(i for i, ln in enumerate(lines) if "method pool status" in ln)
    assert rec < pool, script
