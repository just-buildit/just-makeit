"""gh-2680: a method names its capacity function (``max_out_fn``).

A ``variable_output`` method with ``fn = X`` derived its capacity function
as ``X_max_out``, with no key to name another. doppler binds a status twin
``fn = "dp_ofdm_modulate_why"`` beside the plain ``dp_ofdm_modulate`` whose
capacity function, ``dp_ofdm_modulate_max_out``, already exists -- and jm
called, declared and stubbed a ``dp_ofdm_modulate_why_max_out`` that only
an alias could satisfy.

``max_out_fn`` names it. ``<method C symbol>_max_out`` was spelled in some
forty places; it is now ONE derivation, `_config.method_max_out_symbol`,
and every reader asks it: the binding's call, both ``.pyi`` writers, the
header prototype, the ``_core.c`` stub, apply's additive header refresh,
the gh-761 arity scans, ``status`` and the doc lookup. An authored name is
treated like an authored ``fn``: called as written, and never given a
second definition when the tree already has it.

GATES:

1. Registration-free: no module but `_config.method_max_out_symbol` appends
   ``_max_out`` to anything but a method's PYTHON name (the accessor
   ``<name>_max_out`` is the Python face and keeps its name). The scanner
   is shown to see each shape it guards.
2. The issue's shape, applied: the binding calls the named function, no
   derived ``<fn>_max_out`` appears in any file, ``status`` is clean,
   ``apply`` is a no-op, ``jm script`` round-trips.
3. A name that does not end in ``_max_out`` (a plain method, and a module
   object): scaffolded under that name, its arity read off the header, its
   header block the accessor's doc, its author-owned declaration kept.
4. ``jm method`` beside an existing capacity function writes no second
   definition or declaration of it.
5. Built and run: the Python accessors answer from the named functions.
6. Refusals: a method with no capacity function, and a non-identifier.
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
from just_makeit import _config as C

SRC = Path(__file__).resolve().parent.parent / "src" / "just_makeit"

# -- 1. the one derivation ----------------------------------------------------

#: The function allowed to spell the derivation.
_OWNER = ("_config.py", "method_max_out_symbol")

#: How a method's PYTHON name is spelled where ``<name>_max_out`` is the
#: Python accessor (or the C wrapper named after it): a local ``name`` /
#: ``m_name`` / ``_name``, or the entry's ``name`` key read directly.
_PY_NAME = re.compile(r"(?:m_|_)?name")

#: What a C symbol stem is spelled from, ahead of a Python name: the
#: object's stem (``csym`` / ``stem``), a method's resolved symbol.
_C_STEM = re.compile(r"csym|stem|c_fn|c_symbol|\bsym\b|\bfn\b")


def _python_name(node: ast.expr) -> bool:
    """Whether *node* is a method's Python name, by its spelling."""
    if isinstance(node, ast.Name):
        return bool(_PY_NAME.fullmatch(node.id))
    if isinstance(node, ast.Subscript):
        s = node.slice
        return isinstance(s, ast.Constant) and s.value == "name"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        a = node.args
        return (
            node.func.attr == "get"
            and bool(a)
            and isinstance(a[0], ast.Constant)
            and a[0].value == "name"
        )
    return False


def _docstrings(tree: ast.AST) -> "set[int]":
    """The ids of every docstring constant: prose, not code."""
    out: "set[int]" = set()
    for n in ast.walk(tree):
        if isinstance(
            n,
            (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            body = n.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
            ):
                out.add(id(body[0].value))
    return out


def _owner_nodes(tree: ast.AST, rel: str) -> "set[int]":
    """The nodes inside the owner function, when *rel* is its module."""
    if rel != _OWNER[0]:
        return set()
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == _OWNER[1]:
            return {id(x) for x in ast.walk(n)}
    return set()


def _findings(source: str, rel: str) -> "list[str]":
    """Every hand-spelled ``_max_out`` derivation in *source*."""
    tree = ast.parse(source)
    skip = _docstrings(tree) | _owner_nodes(tree, rel)
    hits: "list[str]" = []
    for n in ast.walk(tree):
        if id(n) in skip:
            continue
        if isinstance(n, ast.JoinedStr):
            vals = n.values
            for i, (a, b) in enumerate(zip(vals, vals[1:])):
                if not (
                    isinstance(a, ast.FormattedValue)
                    and isinstance(b, ast.Constant)
                    and isinstance(b.value, str)
                    and b.value.startswith("_max_out")
                ):
                    continue
                # The name right before it, and any C stem in front of
                # that: ``{csym}_{name}_max_out`` is the derivation too.
                stems = [
                    v
                    for v in vals[:i]
                    if isinstance(v, ast.FormattedValue)
                    and _C_STEM.search(ast.unparse(v.value))
                ]
                if stems or not _python_name(a.value):
                    hits.append(f"{rel}:{n.lineno} {ast.unparse(n)[:60]}")
        elif (
            isinstance(n, ast.BinOp)
            and isinstance(n.op, ast.Add)
            and isinstance(n.right, ast.Constant)
            and isinstance(n.right.value, str)
            and n.right.value.startswith("_max_out")
        ):
            hits.append(f"{rel}:{n.lineno} + {n.right.value!r}")
        elif (
            isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and re.search(r"(?:%s|\{\w*\})_max_out", n.value)
        ):
            hits.append(f"{rel}:{n.lineno} {n.value[:50]!r}")
    return hits


def _modules() -> "list[Path]":
    return sorted(p for p in SRC.rglob("*.py") if "templates" not in p.parts)


def test_no_module_spells_the_derivation_by_hand() -> None:
    hits = [
        h
        for p in _modules()
        for h in _findings(
            p.read_text(encoding="utf-8"), p.relative_to(SRC).as_posix()
        )
    ]
    assert not hits, (
        "spell a capacity function through "
        "`_config.method_max_out_symbol`:\n" + "\n".join(hits)
    )


def test_no_template_spells_the_derivation() -> None:
    hits = [
        p.relative_to(SRC).as_posix()
        for p in (SRC / "templates").rglob("*")
        if p.is_file()
        and re.search(r">>(?:\*/)?_max_out", p.read_text(encoding="utf-8"))
    ]
    assert not hits, hits


@pytest.mark.parametrize(
    "line",
    [
        'x = f"{c_fn}_max_out"',
        'x = f"size_t {sym}_max_out(state)"',
        'x = f"{C.method_c_symbol(s, m)}_max_out"',
        'x = c_fn + "_max_out"',
        'x = "%s_max_out" % c_fn',
        'x = "{}_max_out".format(c_fn)',
        'x = f"{c_name_fn}_max_out"',
        'x = f"{csym}_{m_name}_max_out"',
        'x = f"{CSYM.stem(cfg, o)}_{name}_max_out"',
    ],
)
def test_the_scanner_sees_each_shape(line: str) -> None:
    assert _findings(line, "_x.py"), line


@pytest.mark.parametrize(
    "line",
    [
        'x = f"{name}_max_out"',
        'x = f"{m_name}_max_out({sig})"',
        "x = f\"{m['name']}_max_out\"",
        "x = f\"{m.get('name', '')}_max_out\"",
        'x = f"{wrapper_prefix}_{name}_max_out"',
        'def f():\n    """A {c_fn}_max_out in prose."""',
        'x = s.removesuffix("_max_out")',
    ],
)
def test_the_scanner_lets_the_python_face_through(line: str) -> None:
    assert not _findings(line, "_x.py"), line


def test_the_owner_is_exempt_and_found() -> None:
    """Armed: the owner exists and spells it, and only it is exempt."""
    src = (SRC / _OWNER[0]).read_text(encoding="utf-8")
    tree = ast.parse(src)
    assert _owner_nodes(tree, _OWNER[0])
    stripped = _findings(src, "elsewhere.py")
    assert any("method_c_symbol" in h for h in stripped), stripped


# -- 2-4. the project ---------------------------------------------------------

OFDM_TOML = """
[ofdm]
arg_type = "float"
return_type = "float"

[[ofdm.methods]]
name = "modulate"
arg_type = "float[]"
return_type = "float"
variable_output = true

[[ofdm.methods]]
name = "modulate_checked"
fn = "rq_ofdm_modulate_why"
max_out_fn = "rq_ofdm_modulate_max_out"
arg_type = "float[]"
return_type = "float"
variable_output = true
pass_capacity = true
count_type = "int64_t"
error_negative = true
error_message = "modulate refused"
why = true

[[ofdm.methods]]
name = "scale"
max_out_fn = "rq_ofdm_bound"
arg_type = "float[]"
return_type = "float"
variable_output = true

[[ofdm.methods]]
name = "burst"
max_out_fn = "rq_ofdm_burst_bound"
arg_type = "void"
return_type = "float"
variable_output = true

[[ofdm.methods.params]]
name = "k"
type = "int"

[[ofdm.methods]]
name = "peaks"
max_out_fn = "rq_ofdm_peak_cap"
arg_type = "float"
return_type = "div_t"
result_fields = [{name = "quot", type = "int"}, {name = "rem", type = "int"}]
"""

ACC_TOML = """
[acc]
arg_type = "float"
return_type = "float"

[[acc.methods]]
name = "run"
max_out_fn = "rq_acc_capacity"
arg_type = "float[]"
return_type = "float"
variable_output = true

[[acc.views]]
class_name = "AccView"
create_fn = "rq_acc_create_view"

[[acc.views.methods]]
name = "vrun"
fn = "rq_acc_vrun"
max_out_fn = "rq_acc_capacity"
arg_type = "float[]"
return_type = "float"
variable_output = true
"""

#: The derived names the authored keys replace: none may appear anywhere.
DERIVED = (
    "rq_ofdm_modulate_why_max_out",
    "rq_ofdm_modulate_checked_max_out",
    "rq_ofdm_scale_max_out",
    "rq_ofdm_burst_max_out",
    "rq_ofdm_peaks_max_out",
    "rq_acc_run_max_out",
    "rq_acc_vrun_max_out",
)

OFDM_H = "native/inc/rq/ofdm/ofdm_core.h"
OFDM_C = "native/src/ofdm/ofdm_core.c"
OFDM_EXT = "native/src/ofdm/ofdm_ext.c"
ACC_H = "native/inc/rq/acc/acc_core.h"
ACC_C = "native/src/acc/acc_core.c"


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout + r.stderr


def _read(proj: Path, rel: str) -> str:
    return (proj / rel).read_text(encoding="utf-8")


def _write(proj: Path, rel: str, text: str) -> None:
    (proj / rel).write_text(text, encoding="utf-8")


def _project(root: Path) -> Path:
    """``rq``: standalone ``ofdm`` and module ``dsp``'s ``acc``, declared
    in the manifest and written by ``apply``."""
    _ok("new", "rq", "--object", "seed", cwd=root)
    proj = root / "rq"
    _ok("module", "dsp", cwd=proj)
    _write(proj, "objects/ofdm.toml", OFDM_TOML)
    _write(proj, "objects/acc.toml", ACC_TOML)
    mod = proj / "modules" / "dsp.toml"
    text = mod.read_text(encoding="utf-8")
    assert text.count("objects = []") == 1, text
    mod.write_text(text.replace("objects = []", 'objects = ["acc"]'), "utf-8")
    _ok("apply", cwd=proj)
    return proj


@pytest.fixture(scope="module")
def scaffold(tmp_path_factory) -> Path:
    return _project(tmp_path_factory.mktemp("gh2680"))


def _tree(proj: Path) -> "dict[str, str]":
    return {
        p.relative_to(proj).as_posix(): p.read_text("utf-8", "replace")
        for p in sorted(proj.rglob("*"))
        if p.is_file()
        and p.suffix in (".c", ".h", ".pyi", ".py", ".toml", ".txt")
        and "__pycache__" not in p.parts
        and "build" not in p.parts
    }


def _defs(text: str, sym: str) -> int:
    """How many definitions or declarations of *sym* start a line."""
    return len(re.findall(rf"^(?:size_t\s+)?{sym}\s*\(", text, re.M))


def test_the_scaffold_is_clean(scaffold: Path) -> None:
    r = run_cli("status", "--check", cwd=scaffold)
    assert r.returncode == 0, r.stdout + r.stderr


def test_no_derived_name_reaches_any_file(scaffold: Path) -> None:
    found = {
        (rel, d)
        for rel, text in _tree(scaffold).items()
        for d in DERIVED
        if d in text
    }
    assert not found, sorted(found)


def test_the_twin_calls_the_named_capacity_function(scaffold: Path) -> None:
    ext = _read(scaffold, OFDM_EXT)
    body = ext.split("Ofdm_modulate_checked(")[1].split("\n}\n")[0]
    assert "rq_ofdm_modulate_max_out(self->handle" in body, body
    acc = body.split("Ofdm_modulate_checked_max_out")[0]
    assert "rq_ofdm_modulate_why(" in ext and acc


def test_the_named_function_is_declared_and_defined_once(
    scaffold: Path,
) -> None:
    h, c = _read(scaffold, OFDM_H), _read(scaffold, OFDM_C)
    for sym in ("rq_ofdm_modulate_max_out", "rq_ofdm_bound"):
        assert _defs(h, sym) == 1, (sym, h)
        assert _defs(c, sym) == 1, (sym, c)
    assert _defs(_read(scaffold, ACC_H), "rq_acc_capacity") == 1
    assert _defs(_read(scaffold, ACC_C), "rq_acc_capacity") == 1


def test_a_views_own_method_calls_the_named_function(scaffold: Path) -> None:
    """A view method carrying its own ``fn`` names its capacity function
    the same way, and shares the object's."""
    text = "".join(
        p.read_text("utf-8")
        for p in scaffold.joinpath("native/src").rglob("*_ext*.c")
    )
    wrapper = text.split("\nAccView_vrun(")[1].split("\n}\n")[0]
    assert "rq_acc_capacity(self->handle" in wrapper, wrapper


@pytest.mark.parametrize(
    "stub, member",
    [
        ("src/rq/ofdm.pyi", "modulate_checked_max_out"),
        ("src/rq/ofdm.pyi", "scale_max_out"),
        ("src/rq/dsp/__init__.pyi", "run_max_out"),
    ],
)
def test_the_python_accessor_keeps_its_name(
    scaffold: Path, stub: str, member: str
) -> None:
    candidates = [scaffold / stub, *scaffold.joinpath("src/rq").rglob("*.pyi")]
    text = "".join(p.read_text("utf-8") for p in candidates if p.is_file())
    assert f"def {member}(" in text, member


def test_apply_is_a_no_op(scaffold: Path, tmp_path: Path) -> None:
    proj = tmp_path / "rq"
    shutil.copytree(scaffold, proj)
    before = _tree(proj)
    _ok("apply", cwd=proj)
    assert _tree(proj) == before


DOC = "How many samples modulate can write for n_in."
PEAK_DECL = "size_t rq_ofdm_peak_cap(rq_ofdm_state_t *state, size_t n_in);"
PEAK_DEF = (
    "\nsize_t\nrq_ofdm_peak_cap(rq_ofdm_state_t *state, size_t n_in)\n"
    "{\n    (void)state;\n    return n_in + 2;\n}\n"
)
ACC_DOC = "How many samples run can write for n_in."


def _authored(scaffold: Path, tmp_path: Path) -> Path:
    """The scaffold after the author documents the shared capacity
    function and makes ``rq_ofdm_bound`` state-only, in header and source
    alike, and documents ``rq_acc_capacity``.

    Only the standalone object's arity is changed: a module object's
    method wrapper lives in a per-object fragment that is sacred once
    written (gh-761's comment in `make_methods_ctx`), for a derived name
    as for a named one."""
    proj = tmp_path / "rq"
    shutil.copytree(scaffold, proj)
    h = _read(proj, OFDM_H)
    decl = "size_t rq_ofdm_modulate_max_out("
    assert h.count(decl) == 1, h
    h = h.replace(decl, f"/** @brief {DOC} */\n{decl}")
    old = "rq_ofdm_bound(rq_ofdm_state_t *state, size_t n_in)"
    c = _read(proj, OFDM_C)
    assert c.count(old) == 1, c
    _write(
        proj, OFDM_C, c.replace(old, "rq_ofdm_bound(rq_ofdm_state_t *state)")
    )
    old += ";"
    assert h.count(old) == 1, h
    _write(
        proj, OFDM_H, h.replace(old, "rq_ofdm_bound(rq_ofdm_state_t *state);")
    )
    # gh-2184: a list of records has a capacity only when the author
    # declares one; this one is named.
    h = _read(proj, OFDM_H)
    anchor = f"/** @brief {DOC} */"
    _write(proj, OFDM_H, h.replace(anchor, PEAK_DECL + "\n" + anchor, 1))
    _write(proj, OFDM_C, _read(proj, OFDM_C) + PEAK_DEF)
    ah = _read(proj, ACC_H)
    decl = "size_t rq_acc_capacity("
    assert ah.count(decl) == 1, ah
    _write(proj, ACC_H, ah.replace(decl, f"/** @brief {ACC_DOC} */\n{decl}"))
    _ok("apply", cwd=proj)
    return proj


@pytest.fixture(scope="module")
def authored(scaffold: Path, tmp_path_factory) -> Path:
    return _authored(scaffold, tmp_path_factory.mktemp("gh2680a"))


def test_the_accessor_doc_is_the_named_functions_block(
    authored: Path,
) -> None:
    """Both accessors read the one header block: the plain method's,
    whose capacity function it is, and the twin's, which names it."""
    pyi = _read(authored, "src/rq/ofdm.pyi")
    for member in ("modulate_max_out", "modulate_checked_max_out"):
        body = pyi.split(f"def {member}(")[1].split("def ")[0]
        assert DOC in body, (member, body)


def test_a_module_accessor_doc_is_the_named_functions_block(
    authored: Path,
) -> None:
    """The module stub's writer (`_stubs`) is the peer of the object's,
    and asks the same helper."""
    text = "".join(
        p.read_text("utf-8")
        for p in authored.joinpath("src/rq").rglob("*.pyi")
        if "def run_max_out(" in p.read_text("utf-8")
    )
    body = text.split("def run_max_out(")[1].split("def ")[0]
    assert ACC_DOC in body, body


def test_the_authored_arity_is_kept(authored: Path) -> None:
    """A named capacity function declared state-only stays so through
    `apply`: the scan that protects an author's ``*_max_out`` reads the
    names the manifest gives, whatever their spelling."""
    h = _read(authored, OFDM_H)
    assert "rq_ofdm_bound(rq_ofdm_state_t *state);" in h, h


def test_a_record_list_sizes_by_its_named_capacity(authored: Path) -> None:
    """gh-2184's capacity, read off the header by its NAMED spelling: the
    scan is told the names the manifest gives."""
    ext = _read(authored, OFDM_EXT)
    body = ext.split("\nOfdm_peaks(")[1].split("\n}\n")[0]
    assert "rq_ofdm_peak_cap(self->handle, " in body, body


def test_a_scalar_shape_refuses_a_zero_from_the_named_function(
    scaffold: Path,
) -> None:
    """gh-1085's guard, for the one shape with no call length: emitted for
    the named function's call, and naming it."""
    ext = _read(scaffold, OFDM_EXT)
    body = ext.split("\nOfdm_burst(")[1].split("\n}\n")[0]
    assert '"burst", "rq_ofdm_burst_bound");' in body, body


def test_the_binding_follows_the_authored_arity(authored: Path) -> None:
    ext = _read(authored, OFDM_EXT)
    assert "rq_ofdm_bound(self->handle)" in ext, ext
    assert "rq_ofdm_bound(self->handle," not in ext, ext


def test_a_module_header_gains_only_the_named_function(
    scaffold: Path, tmp_path: Path
) -> None:
    """A module object's sacred header gains a new method's prototypes
    additively (gh-1302) -- offered by name, so the named capacity function
    is declared and no derived one is."""
    proj = tmp_path / "rq"
    shutil.copytree(scaffold, proj)
    frag = proj / "objects" / "acc.toml"
    frag.write_text(
        frag.read_text("utf-8")
        + '\n[[acc.methods]]\nname = "run2"\nmax_out_fn = "rq_acc_cap2"\n'
        'arg_type = "float[]"\nreturn_type = "float"\n'
        "variable_output = true\n",
        "utf-8",
    )
    _ok("apply", cwd=proj)
    h = _read(proj, ACC_H)
    assert _defs(h, "rq_acc_cap2") == 1, h
    assert "rq_acc_run2_max_out" not in h, h


def test_the_authored_tree_is_clean(authored: Path) -> None:
    r = run_cli("status", "--check", cwd=authored)
    assert r.returncode == 0, r.stdout + r.stderr


def test_jm_method_beside_an_existing_capacity_function(
    tmp_path: Path,
) -> None:
    """The issue's order: the plain method and its capacity function
    exist, then the twin is added naming it. Nothing is injected for it --
    not a second definition, not a second declaration."""
    _ok("new", "rq", cwd=tmp_path)
    proj = tmp_path / "rq"
    _ok("object", "ofdm", cwd=proj)
    _ok(
        "method", "ofdm", "modulate", "--arg-type", "float[]",
        "--return-type", "float", "--variable-output", cwd=proj,
    )  # fmt: skip
    _ok(
        "method", "ofdm", "modulate_checked", "--arg-type", "float[]",
        "--return-type", "float", "--variable-output", "--pass-capacity",
        "--count-type", "int64_t", "--error-negative", "--error-message",
        "refused", "--why", "--fn", "rq_ofdm_modulate_why",
        "--max-out-fn", "rq_ofdm_modulate_max_out", cwd=proj,
    )  # fmt: skip
    h, c = _read(proj, OFDM_H), _read(proj, OFDM_C)
    assert _defs(h, "rq_ofdm_modulate_max_out") == 1, h
    assert _defs(c, "rq_ofdm_modulate_max_out") == 1, c
    assert _defs(c, "rq_ofdm_modulate_why") == 1, c
    for d in DERIVED:
        assert d not in h + c + _read(proj, OFDM_EXT), d
    r = run_cli("status", "--check", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    # ...and `jm script` replays the key, to the same manifest.
    out = _ok("script", cwd=proj)
    assert "--max-out-fn rq_ofdm_modulate_max_out" in out, out
    orig, replayed = script_round_trip(proj, tmp_path / "replay")
    rows = {m["name"]: m for m in orig["ofdm"]["methods"]}
    assert rows["modulate_checked"]["max_out_fn"] == "rq_ofdm_modulate_max_out"
    assert orig == replayed


def test_a_lifted_body_returns_the_named_bound(tmp_path: Path) -> None:
    """gh-2248's completing ``return``: a ``no_count`` kernel's count is
    the bound the binding sized ``out`` with -- the named function's."""
    _ok("new", "rq", cwd=tmp_path)
    proj = tmp_path / "rq"
    _ok("object", "o", cwd=proj)
    (proj / "impl.c").write_text(
        "void\nlifted(void)\n{\n    int k = 1;\n    (void)k;\n}\n", "utf-8"
    )
    _ok(
        "method", "o", "g", "--arg-type", "void", "--return-type", "float",
        "--variable-output", "--pass-capacity", "--no-count",
        "--max-out-fn", "rq_o_cap", "--impl", "impl.c::lifted", cwd=proj,
    )  # fmt: skip
    c = _read(proj, "native/src/o/o_core.c")
    assert "return rq_o_cap(state);" in c, c
    assert "rq_o_g_max_out" not in c, c


# -- 5. built and run -----------------------------------------------------------

#: Each capacity function's body: a value only it returns, so the Python
#: answer names the function the binding called.
BODIES = {
    (OFDM_C, "rq_ofdm_modulate_max_out"): "    (void)state;\n"
    "    return n_in + 7;\n",
    (OFDM_C, "rq_ofdm_bound"): "    (void)state;\n    return 11;\n",
    (ACC_C, "rq_acc_capacity"): "    (void)state;\n    return n_in + 8;\n",
}

_PROBE = """
import json, sys
import numpy as np
sys.path.insert(0, "src")
from rq import Ofdm
from rq.dsp import Acc

def run(f):
    try:
        return ["ok", f().tolist()]
    except Exception as e:
        return [type(e).__name__, str(e)]


o, a = Ofdm(), Acc()
x = np.zeros(5, dtype=np.float32)
print(json.dumps({
    "modulate": o.modulate_max_out(5),
    "checked": o.modulate_checked_max_out(5),
    "scale": o.scale_max_out(),
    "run": a.run_max_out(5),
    "checked_len": len(o.modulate_checked(x)),
    "scale_len": len(o.scale(x)),
    "run_len": len(a.run(x)),
    "peaks": o.peaks(x),
    "burst": run(lambda: o.burst(3)),
}))
"""


@pytest.fixture(scope="module")
def probe(authored: Path, tmp_path_factory) -> dict:
    if not shutil.which("cmake"):
        pytest.skip("cmake not found")
    proj = tmp_path_factory.mktemp("gh2680b") / "rq"
    shutil.copytree(authored, proj)
    for (rel, fn), body in BODIES.items():
        text = _read(proj, rel)
        pat = re.compile(rf"^({fn}\([^)]*\))\n\{{.*?^\}}", re.M | re.S)
        text, n = pat.subn(lambda m: f"{m.group(1)}\n{{\n{body}}}", text)
        assert n == 1, (rel, fn)
        _write(proj, rel, text)
    for cmd in (
        ["cmake", "-S", ".", "-B", "build",
         f"-DPython3_EXECUTABLE={sys.executable}"],
        ["cmake", "--build", "build"],
        ["ctest", "--test-dir", "build", "--output-on-failure"],
        [sys.executable, "-c", _PROBE],
    ):  # fmt: skip
        r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def test_the_accessors_answer_from_the_named_functions(probe: dict) -> None:
    assert probe["modulate"] == 12, probe
    assert probe["checked"] == 12, probe
    assert probe["scale"] == 11, probe
    assert probe["run"] == 13, probe


def test_the_methods_run(probe: dict) -> None:
    # The kernels are jm's untouched stubs, which write nothing and say so:
    # what runs is the binding sizing each call by the named function and
    # handing it to the kernel.
    for key in ("checked_len", "scale_len", "run_len"):
        assert probe[key] == 0, probe
    assert probe["peaks"] == [], probe


def test_a_zero_names_the_named_function(probe: dict) -> None:
    assert probe["burst"] == [
        "RuntimeError",
        "burst() cannot size its output: rq_ofdm_burst_bound() returned 0, "
        "and this method has no input length to fall back on. Implement it "
        "to return the worst-case count.",
    ], probe["burst"]


# -- 6. refusals --------------------------------------------------------------


@pytest.mark.parametrize(
    "extra, said",
    [
        ('return_type = "float"\narg_type = "float"', "no capacity"),
        (
            'return_type = "float"\narg_type = "float[]"\n'
            "variable_output = true",
            "",
        ),
    ],
)
def test_the_loader_refuses_what_nothing_would_call(
    tmp_path: Path, extra: str, said: str
) -> None:
    _ok("new", "rq", cwd=tmp_path)
    proj = tmp_path / "rq"
    _ok("object", "o", cwd=proj)
    frag = proj / "objects" / "o.toml"
    name = "1bad" if not said else "rq_o_cap"
    frag.write_text(
        frag.read_text("utf-8")
        + f'\n[[o.methods]]\nname = "m"\nmax_out_fn = "{name}"\n{extra}\n',
        "utf-8",
    )
    # `status`, which replays no method, so only the loader can refuse.
    r = run_cli("status", cwd=proj)
    assert r.returncode != 0
    assert "[[o.methods]] 'm': max_out_fn" in r.stderr, r.stderr
    assert (said or "C function name") in r.stderr, r.stderr


def test_the_cli_refuses_before_writing(tmp_path: Path) -> None:
    _ok("new", "rq", cwd=tmp_path)
    proj = tmp_path / "rq"
    _ok("object", "o", cwd=proj)
    before = {p: p.read_bytes() for p in proj.rglob("*") if p.is_file()}
    r = run_cli(
        "method", "o", "m", "--arg-type", "float", "--return-type", "float",
        "--max-out-fn", "rq_o_cap", cwd=proj,
    )  # fmt: skip
    assert r.returncode != 0
    assert "max_out_fn" in r.stderr, r.stderr
    after = {p: p.read_bytes() for p in proj.rglob("*") if p.is_file()}
    assert after == before


def test_the_helper_is_what_the_view_keys_name() -> None:
    """`view_member_doc_keys` keys the accessor's doc on the named
    function, so a view's text for ``<m>_max_out`` reaches its block."""
    cfg = {
        "project": {"name": "p"},
        "o": {
            "methods": [
                {
                    "name": "run",
                    "arg_type": "float[]",
                    "variable_output": True,
                    "max_out_fn": "o_cap",
                }
            ]
        },
    }
    assert C.view_member_doc_keys(cfg, "o")["run_max_out"] == "o_cap"
