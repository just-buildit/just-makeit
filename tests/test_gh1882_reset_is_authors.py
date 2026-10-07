"""gh-1882: a component whose reset() is the author's ships green tests.

The ``delay_line`` README's own fragment -- a ``reset_impl`` that zeroes the
ring buffer and KEEPS ``length`` -- scaffolded a project that failed three of
its own checks on the first ``make test``, with no edits:

- the C test set every field, reset, then ``CHECK``-ed each declared default;
- the Python ``test_reset`` did the same;
- the ``.pyi`` doctest demonstrated "Reset restores defaults" on ``length``.

Each asked its own question, and none of them asked about ``reset_impl``: the
docstring builders tested ``bool(init_params) or no_reset`` at six sites
(``bool(init_params)`` alone at two in ``jm bind``), and the tests tested
nothing. The example worked around it by overwriting both generated suites.

Now one predicate, ``_context._state.reset_is_authors``, decides -- a
``reset_impl`` / ``reset_impl_file``, init_params or ``no_reset`` -- and
every artefact that claims something about reset() reads it. Where it holds,
the tests call reset() (the call-only test a no-state object already had)
and the doctest leaves the demo out.

GATES

1. Structural, registration-free: every function that takes a
   ``custom_reset`` is passed one, and the value is the predicate (or the
   caller's own ``custom_reset``, passed through); every function that takes
   a ``reset_impl`` is passed one explicitly. Derived from the source, so a
   new docstring builder or ctx builder is covered with no edit here.
2. Per input of the predicate, read from its signature: the reset tests are
   call-only and the doctest has no reset demo.
3. Compiled: one project with an object per ``reset_impl*`` manifest key
   (read from ``_keys.OBJECT_KEYS``, not from the fix's own table) in each
   placement -- standalone and inside a module -- each with a method, so the
   ``.pyi`` is re-rendered from the replay's scratch manifest. Every reset
   keeps ``length``. ``jm test`` and every ``.pyi`` doctest pass.
"""

from __future__ import annotations

import ast
import inspect
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc

from _jmrun import run_cli
from just_makeit import _context as Ctx
from just_makeit import _keys

SRC = Path(Ctx.__file__).resolve().parent.parent

#: The predicate, under every name a call site may reach it by.
PREDICATES = {"reset_is_authors", "manifest_reset_is_authors"}

#: Every manifest key that hands reset()'s body to the author -- from the
#: manifest vocabulary, so narrowing the fix's own table narrows nothing here.
RESET_KEYS = sorted(k for k in _keys.OBJECT_KEYS if k.startswith("reset_impl"))

#: The demo `_stubs._build_class_docstring` writes when reset is jm's.
DEMO = "Reset restores defaults"


# ── 1. every site reads the one predicate ──────────────────────────────────


def _callee(call: ast.Call) -> str:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def _functions_taking(trees: dict, param: str) -> "set[str]":
    """Names of every function in *trees* with a parameter called *param*."""
    out = set()
    for tree in trees.values():
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = fn.args
                names = {x.arg for x in a.args + a.kwonlyargs}
                if param in names:
                    out.add(fn.name)
    return out


def _reads_the_predicate(value: ast.AST, fn: ast.AST) -> bool:
    """*value* is the predicate's answer, inside enclosing function *fn*."""
    if isinstance(value, ast.Call):
        return _callee(value) in PREDICATES
    if not isinstance(value, ast.Name):
        return False
    # The caller's own parameter, handed on.
    args = fn.args
    if value.id == "custom_reset" and value.id in {
        x.arg for x in args.args + args.kwonlyargs
    }:
        return True
    # A local bound to the predicate's answer.
    for n in ast.walk(fn):
        if (
            isinstance(n, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == value.id for t in n.targets
            )
            and isinstance(n.value, ast.Call)
            and _callee(n.value) in PREDICATES
        ):
            return True
    return False


def _calls_in_functions(tree: ast.AST):
    """``(call, enclosing function)`` for every call inside a function."""
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(fn):
                if isinstance(n, ast.Call):
                    yield n, fn


@pytest.fixture(scope="module")
def trees() -> dict:
    """jm's own Python: not the templates it renders, nor the examples."""
    return {
        p: ast.parse(p.read_text(encoding="utf-8"))
        for p in sorted(SRC.rglob("*.py"))
        if not {"examples", "templates"} & set(p.relative_to(SRC).parts)
    }


def test_every_custom_reset_reads_the_one_predicate(trees):
    takers = _functions_taking(trees, "custom_reset")
    # Armed: the class docstring's builder and both of its faces.
    assert {"class_docstring_block", "class_runtime_doc"} <= takers, takers
    sites, bad = [], []
    for path, tree in trees.items():
        for call, fn in _calls_in_functions(tree):
            if _callee(call) not in takers:
                continue
            kw = {k.arg: k.value for k in call.keywords}
            where = f"{path.relative_to(SRC)}:{call.lineno}"
            sites.append(where)
            if "custom_reset" not in kw:
                bad.append(f"{where}: no custom_reset= at all")
            elif not _reads_the_predicate(kw["custom_reset"], fn):
                bad.append(f"{where}: {ast.unparse(kw['custom_reset'])}")
    # Armed: the issue named sites in five modules.
    assert len({s.split(":")[0] for s in sites}) >= 5, sites
    assert not bad, (
        "these say whether reset() restores the declared defaults without "
        "asking `Ctx.reset_is_authors` -- the drift that let a reset_impl "
        "ship a red doctest (gh-1882):\n  " + "\n  ".join(bad)
    )


def test_every_reset_impl_taker_is_told(trees):
    """`make_state_ctx` defaults *reset_impl* to False, so a caller that
    forgets it renders tests asserting a reset the author did not write."""
    takers = _functions_taking(trees, "reset_impl") - PREDICATES
    assert "make_state_ctx" in takers, takers
    sites, bad = [], []
    for path, tree in trees.items():
        for call, _fn in _calls_in_functions(tree):
            if _callee(call) not in takers:
                continue
            sites.append(call)
            if "reset_impl" not in {k.arg for k in call.keywords}:
                bad.append(f"{path.relative_to(SRC)}:{call.lineno}")
    assert len(sites) >= 6, len(sites)
    assert not bad, "pass reset_impl= explicitly (gh-1882):\n  " + "\n  ".join(
        bad
    )


def test_the_predicate_covers_every_manifest_key():
    assert RESET_KEYS == ["reset_impl", "reset_impl_file"], RESET_KEYS
    assert sorted(Ctx.RESET_IMPL_KEYS) == RESET_KEYS


# ── 2. per input of the predicate ──────────────────────────────────────────


#: (name, type, default, default_raw, real_type, real_create_fn, optional,
#:  create_fn, required, doc) -- an optional scalar with a default.
_IP = ("n", "uint32_t", "4", "", "", "", False, "", False, "")
_TRUTHY = {"init_params": [_IP]}

INPUTS = sorted(inspect.signature(Ctx.reset_is_authors).parameters)


def _state_ctx(**kw):
    return Ctx.make_state_ctx(
        "dl",
        "Dl",
        [("length", "uint32_t", "64"), ("idx", "uint32_t", "0")],
        csym="dl",
        **kw,
    )


def test_control_asserts_the_declared_defaults():
    """Without any input, reset is jm's, and both tests say what it does."""
    ctx = _state_ctx()
    assert "CHECK(dl_get_length(obj) == 64" in ctx["reset_test_c"]
    assert "assert obj.get_length() == 64" in ctx["reset_test_py"]


@pytest.mark.parametrize("name", INPUTS)
def test_each_input_makes_the_reset_tests_call_only(name):
    assert set(INPUTS) == {"reset_impl", "init_params", "no_reset"}, INPUTS
    ctx = _state_ctx(**{name: _TRUTHY.get(name, True)})
    # `no_reset` removes the tests outright (gh-542); the others call it.
    for slot in ("reset_test_c", "reset_test_py"):
        assert "get_length" not in ctx[slot], (name, slot, ctx[slot])
    if name != "no_reset":
        assert ctx["reset_test_c"].endswith("dl_reset(obj);"), name
        assert ctx["reset_test_py"].endswith("obj.reset()"), name


def _fragment(objects: dict, module: "str | None" = None) -> str:
    """A manifest fragment: *objects* maps a name to its extra TOML lines."""
    out = []
    for name, extra in objects.items():
        out += [f"[{name}]", 'arg_type = "float"', 'return_type = "float"']
        out += extra
        out += [
            f"[[{name}.state]]",
            'name = "length"',
            'type = "uint32_t"',
            'default = "64"',
            f"[[{name}.state]]",
            'name = "idx"',
            'type = "uint32_t"',
            'default = "0"',
            f"[[{name}.methods]]",
            'name = "peek"',
            'arg_type = "float"',
            'return_type = "float"',
            "",
        ]
    if module:
        names = ", ".join(f'"{n}"' for n in objects)
        out += [f"[module.{module}]", f"objects = [{names}]", ""]
    return "\n".join(out)


#: A reset that zeroes `idx` and keeps `length`, as the delay line does.
_KEEP_BODY = '"""\nstate->idx = 0;\n"""'
_KEEP_FILE = "legacy/keep.c"
_KEEP_SRC = "void\nkeep_reset(void *state)\n{\n    state->idx = 0;\n}\n"


def _reset_lines(key: str) -> "list[str]":
    if key.endswith("_file"):
        return [f'{key} = "{_KEEP_FILE}::keep_reset"']
    return [f"{key} = {_KEEP_BODY}"]


def _declares(name: str) -> "list[str]":
    """The TOML that makes *name*'s reset the author's, per input."""
    if name in RESET_KEYS:
        return _reset_lines(name)
    if name == "no_reset":
        return ['no_reset = "true"']
    return []


def _project(tmp_path: Path, objects: dict, module=None) -> Path:
    r = run_cli("new", "p", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    proj = tmp_path / "p"
    (proj / "legacy").mkdir()
    (proj / _KEEP_FILE).write_text(_KEEP_SRC, encoding="utf-8")
    frag = tmp_path / "frag.toml"
    text = _fragment(objects, module)
    # An init_params object constructs from the declared param.
    text = text.replace(
        "[[ipobj.state]]",
        '[[ipobj.init_params]]\nname = "n"\ntype = "uint32_t"\n'
        'default = "4"\n[[ipobj.state]]',
        1,
    )
    frag.write_text(text, encoding="utf-8")
    r = run_cli("apply", str(frag), cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    return proj


def _after_reset(c_test: str) -> str:
    """The C test from its reset() call to the teardown -- what it asserts
    about the state reset() left. Empty when there is no reset() at all."""
    i = c_test.find("_reset(obj);")
    return "" if i == -1 else c_test[i : c_test.index("_destroy(obj);")]


def _pyis(proj: Path) -> "list[Path]":
    return sorted(
        p
        for p in (proj / "src" / "p").rglob("*.pyi")
        if p.name != "__init__.pyi"
    )


@pytest.mark.parametrize("placement", ["standalone", "module"])
def test_no_face_demonstrates_a_reset_the_author_owns(tmp_path, placement):
    """Every input, in each placement: both `.pyi` generators leave the demo
    out, and the generated tests only call reset()."""
    names = RESET_KEYS + ["no_reset", "init_params"]
    objects = {
        ("ipobj" if n == "init_params" else f"o_{n}"): _declares(n)
        for n in names
    }
    module = "grp" if placement == "module" else None
    proj = _project(tmp_path, objects, module)
    texts = {p.name: p.read_text(encoding="utf-8") for p in _pyis(proj)}
    assert texts, "no .pyi generated"
    for name, text in texts.items():
        assert "class " in text, name
        assert DEMO not in text, (placement, name, text)
    for obj in objects:
        c_test = proj / "native" / "tests" / f"test_{obj}_core.c"
        assert "CHECK" not in _after_reset(c_test.read_text("utf-8")), obj


def test_control_still_demonstrates_it(tmp_path):
    """Armed: the same project shape with jm's own reset does demo it."""
    proj = _project(tmp_path, {"plain": []})
    (pyi,) = _pyis(proj)
    assert DEMO in pyi.read_text(encoding="utf-8")
    c_test = proj / "native" / "tests" / "test_plain_core.c"
    assert "get_length(obj) == 64" in _after_reset(c_test.read_text("utf-8"))


def test_bind_reads_whose_reset_it_is_off_the_body(tmp_path):
    """`jm bind` has no manifest: a field reset() does not assign is kept."""
    proj = _project(tmp_path, {"kept": _reset_lines("reset_impl")})
    r = run_cli("bind", "kept", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    (pyi,) = _pyis(proj)
    assert DEMO not in pyi.read_text(encoding="utf-8")


# ── 3. compiled: the issue's own trigger, every key, both placements ───────


def _no_toolchain() -> "str | None":
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    return None


@pytest.mark.slow
@pytest.mark.skipif(bool(_no_toolchain()), reason=str(_no_toolchain()))
def test_a_reset_that_keeps_a_field_passes_its_own_checks(tmp_path):
    objects = {f"s_{k}": _reset_lines(k) for k in RESET_KEYS}
    proj = _project(tmp_path, objects)
    # The module's members go in by a second fragment: one fragment's
    # `[module.X]` claims every object it declares.
    mfrag = tmp_path / "mfrag.toml"
    mfrag.write_text(
        _fragment({f"m_{k}": _reset_lines(k) for k in RESET_KEYS}, "grp"),
        encoding="utf-8",
    )
    r = run_cli("apply", str(mfrag), cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr

    # Armed: every reset really keeps `length`, so asserting its default --
    # in C, in Python, or in a doctest -- fails at runtime.
    for obj in [*objects, *(f"m_{k}" for k in RESET_KEYS)]:
        (core_c,) = (proj / "native" / "src").rglob(f"{obj}_core.c")
        m = re.search(
            r"_reset\([^)]*\)\s*\{(.*?)\n\}", core_c.read_text("utf-8"), re.S
        )
        assert m, obj
        assert "state->idx = 0;" in m.group(1), (obj, m.group(1))
        assert "length" not in m.group(1), (obj, m.group(1))

    r = run_cli("test", cwd=proj)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "100% tests passed" in out, out

    env = dict(os.environ, PYTHONPATH=str(proj / "src"))
    pyis = _pyis(proj)
    assert len(pyis) == len(RESET_KEYS) + 1, pyis
    for pyi in pyis:
        d = subprocess.run(
            [sys.executable, "-m", "doctest", "-v", str(pyi)],
            cwd=proj,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert d.returncode == 0, (pyi.name, d.stdout + d.stderr)
        # Armed: the doctest ran, and constructed something.
        assert "get_length()" in d.stdout, (pyi.name, d.stdout)
