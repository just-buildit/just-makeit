"""gh-2120 -- a manifest flag means one thing, and a stale spelling stops.

Before gh-2046 the manifest writer put a TOML bool through ``str()`` in
quotes, so ``streamable = true`` came back ``streamable = "True"``. The
writer was fixed; the manifests it had rewritten were not. Every flag
reader asked ``v is True or v == "true"``, so ``"True"`` read as false: a
streamable producer lost ``stream()`` / ``__iter__`` from its ``.pyi`` and
its ``_ext.c``, ``apply`` and ``status --check`` exited 0, and ``jm
upgrade`` said "already up to date".

Now:

- ``_keys.truthy`` is the one reading of a flag, and every flag reader asks
  it -- before, ``process_global`` took ``"1"``, a property's ``writable``
  took ``"yes"``, and ``why`` / ``bench`` / ``required`` compared by
  identity;
- ``_keys.flag_errors`` refuses at load any value of a ``FLAG_KEYS`` key
  that is not a flag spelling, naming the key, and names ``jm upgrade`` for
  the spelling the old writer left;
- ``jm upgrade`` respells ``"True"`` / ``"False"`` on those keys, before it
  loads anything (schema-independent, like the ``_Complex`` respell).

The two source scans are registration-free: the keys read through
``truthy`` are derived from the source and held equal to ``FLAG_KEYS``,
which is what the refusal and the respell walk; and a comparison against a
flag literal anywhere else in ``src/`` fails, so a new flag read another
way is caught without this file naming it.
"""

from __future__ import annotations

import ast
import importlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli

SRC = Path(__file__).parent.parent / "src"
PKG = SRC / "just_makeit"
sys.path.insert(0, str(SRC))

from just_makeit import _keys  # noqa: E402
from just_makeit._upgrade import _respell_flags  # noqa: E402


# --- end to end -------------------------------------------------------------


def _project(tmp_path: Path) -> Path:
    """A project with one streamable producer, `s4`."""
    r = run_cli("new", "p", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    root = tmp_path / "p"
    r = run_cli(
        "object",
        "s4",
        "--streamable",
        "--arg-type",
        "void",
        "--return-type",
        "float",
        cwd=root,
    )
    assert r.returncode == 0, r.stderr
    return root


def _manifest_with(root: Path, key: str) -> Path:
    """The one manifest file that declares *key*."""
    hits = [
        p
        for p in [root / "just-makeit.toml", *root.glob("objects/*.toml")]
        if f"\n{key} = " in p.read_text(encoding="utf-8")
    ]
    assert len(hits) == 1, hits
    return hits[0]


def _spell(root: Path, key: str, value: str) -> Path:
    """Rewrite *key*'s line to ``key = <value>`` (TOML source)."""
    path = _manifest_with(root, key)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    hit = [i for i, ln in enumerate(lines) if ln.startswith(f"{key} = ")]
    assert len(hit) == 1, hit
    lines[hit[0]] = f"{key} = {value}\n"
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _stream_members(root: Path) -> int:
    pyi = (root / "src/p/s4.pyi").read_text(encoding="utf-8")
    return pyi.count("def stream(") + pyi.count("def __iter__(")


def test_python_spelling_is_refused_naming_the_key(tmp_path):
    root = _project(tmp_path)
    path = _spell(root, "streamable", '"True"')
    before = path.read_text(encoding="utf-8")
    for verb in (["apply"], ["status", "--check"]):
        r = run_cli(*verb, cwd=root)
        assert r.returncode != 0, (verb, r.stdout)
        err = r.stderr
        assert 's4: streamable = "True"' in err, err
        assert "just-makeit upgrade" in err, err
    # Refused, not half-applied: the manifest is as it was, and the
    # producer still has the members the last good apply wrote.
    assert path.read_text(encoding="utf-8") == before
    assert _stream_members(root) == 2


@pytest.mark.parametrize("value", ['"yes"', '"1"', "1", '"TRUE"', '""'])
def test_any_other_spelling_is_refused(tmp_path, value):
    root = _project(tmp_path)
    _spell(root, "streamable", value)
    r = run_cli("apply", cwd=root)
    assert r.returncode != 0, r.stdout
    assert "s4: streamable = " in r.stderr, r.stderr
    assert "must be true or false" in r.stderr, r.stderr


def test_upgrade_respells_and_apply_restores_stream(tmp_path):
    root = _project(tmp_path)
    path = _spell(root, "streamable", '"True"')
    r = run_cli("upgrade", cwd=root)
    assert r.returncode == 0, r.stderr
    assert "objects/s4.toml: s4.streamable" in r.stdout or (
        "just-makeit.toml: s4.streamable" in r.stdout
    ), r.stdout
    assert "\nstreamable = true\n" in path.read_text(encoding="utf-8")
    # Idempotent: nothing left to respell.
    r = run_cli("upgrade", cwd=root)
    assert r.returncode == 0, r.stderr
    assert "respelled" not in r.stdout, r.stdout
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stderr
    assert _stream_members(root) == 2
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr


def _skip_reason():
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    return None


_SKIP = _skip_reason()


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
def test_upgraded_producer_streams(tmp_path):
    """The gate's end: built from a manifest that said ``"True"``,
    ``stream()`` and ``__iter__`` exist and produce blocks."""
    np = pytest.importorskip("numpy")
    root = _project(tmp_path)
    _spell(root, "streamable", '"True"')
    assert run_cli("upgrade", cwd=root).returncode == 0
    assert run_cli("apply", cwd=root).returncode == 0
    build = root / "build"
    for cmd in (
        ["cmake", "-S", str(root), "-B", str(build)],
        ["cmake", "--build", str(build)],
    ):
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"
    src = str(root / "src")
    sys.path.insert(0, src)
    for mod in ("p", "p.s4"):
        sys.modules.pop(mod, None)
    try:
        p = importlib.import_module("p")
        s4 = p.S4()
        blocks = list(s4.stream(8, count=3))
        assert [b.shape for b in blocks] == [(8,)] * 3
        assert blocks[0].dtype == np.float32
        assert next(iter(s4)).ndim == 1
    finally:
        sys.path.remove(src)
        for mod in ("p", "p.s4"):
            sys.modules.pop(mod, None)


def test_respell_reaches_every_table_and_keeps_comments(tmp_path):
    """Each kind of table a flag sits in: ``[project]``, an object, an
    array-of-tables row and an inline table. Comments stay, a ``"true"``
    jm writes itself stays, and a non-flag ``"True"`` is left alone."""
    (tmp_path / "just-makeit.toml").write_text(
        "# keep me\n"
        "[project]\n"
        'name = "p"\n'
        'perf = "False"  # and me\n'
        'pytest = "true"\n'
        "\n"
        "[o]\n"
        'streamable = "True"\n'
        'doc = "True"\n'
        "\n"
        "[[o.init_params]]\n"
        'name = "w"\n'
        'optional = "False"\n'
        "\n"
        "[module.m]\n"
        'json = { enabled = "True" }\n',
        encoding="utf-8",
    )
    got = _respell_flags(tmp_path)
    assert got == [
        "just-makeit.toml: project.perf",
        "just-makeit.toml: o.streamable",
        "just-makeit.toml: o.init_params[w].optional",
        "just-makeit.toml: module.m.json.enabled",
    ]
    text = (tmp_path / "just-makeit.toml").read_text(encoding="utf-8")
    assert "# keep me\n" in text and "perf = false  # and me\n" in text
    assert 'pytest = "true"\n' in text
    assert "streamable = true\n" in text and 'doc = "True"\n' in text
    assert "optional = false\n" in text
    assert "json = { enabled = true }" in text
    assert _respell_flags(tmp_path) == []


def _gain_project(tmp_path: Path, writable: str, required: str) -> Path:
    """An object ``g`` with a defaulted init param ``gain`` spelling
    ``required = <required>`` and a property ``level`` spelling
    ``writable = <writable>`` (TOML source), applied."""
    r = run_cli("new", "p", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    root = tmp_path / "p"
    for argv in (
        ("object", "g", "--init-param", "gain:double:1.0"),
        ("property", "g", "level", "--type", "double"),
    ):
        r = run_cli(*argv, cwd=root)
        assert r.returncode == 0, r.stderr
    path = _manifest_with(root, "default")
    text = path.read_text(encoding="utf-8")
    anchor = 'name = "gain"\ntype = "double"\ndefault = "1.0"\n'
    assert text.count(anchor) == 1, text
    text = text.replace(anchor, f"{anchor}required = {required}\n")
    anchor = 'name = "level"\ntype = "double"\n'
    assert text.count(anchor) == 1, text
    text = text.replace(anchor, f"{anchor}writable = {writable}\n")
    path.write_text(text, encoding="utf-8")
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stderr
    return root


@pytest.mark.parametrize(
    "spelling, on",
    [('"false"', False), ("false", False), ('"true"', True), ("true", True)],
)
def test_a_string_false_flag_is_off(tmp_path, spelling, on):
    """Load accepts ``"false"`` for every flag, so every reader must read
    it as off: tested for truth bare, ``writable = "false"`` generated a
    setter and ``required = "false"`` made a defaulted init param
    required. Each spelling against its bool, on both faces."""
    root = _gain_project(tmp_path, writable=spelling, required=spelling)
    ext = (root / "native/src/g/g_ext.c").read_text(encoding="utf-8")
    pyi = (root / "src/p/g.pyi").read_text(encoding="utf-8")
    assert ("G_setprop_level" in ext) is on, ext
    assert ("@level.setter" in pyi) is on, pyi
    init = "def __init__(self, gain: float) -> None"
    optional = "def __init__(self, gain: float = 1.0) -> None"
    assert (init if on else optional) in pyi, pyi


# --- source scans (registration-free) ---------------------------------------

#: The function that IS the reading of a flag.
_DEFINITION = {("_keys.py", "truthy")}

#: Comparisons against a bool or flag literal that do not read a manifest
#: flag, each with why. Keyed by (file, enclosing function).
_NOT_A_FLAG = {
    # A local tri-state (`_never_placed` returns None / False / a set).
    ("_apply.py", "_new_places"),
    # A generated Python expression's text, not a manifest value.
    ("_context/_types.py", "_py_eq"),
    # Any manifest value: is it a declaration? `0` is, `False` is not.
    ("_script.py", "_declares"),
    # A C default literal's spelling (`default = "1"`), not a flag.
    ("_types.py", "bool_default_py"),
}


def _sources():
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        if rel.startswith(("templates/", "examples/")):
            continue
        yield rel, ast.parse(path.read_text(encoding="utf-8"))


def _enclosing(tree: ast.AST):
    """Yield ``(function name, node)`` for every node, innermost def."""

    def walk(node, owner):
        for child in ast.iter_child_nodes(node):
            here = owner
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                here = child.name
            yield here, child
            yield from walk(child, here)

    yield from walk(tree, "<module>")


def _flag_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        v = node.value
        return isinstance(v, bool) or (
            isinstance(v, str) and v.lower() in ("true", "false")
        )
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return any(_flag_literal(e) for e in node.elts)
    return False


def test_no_flag_is_read_by_comparison_outside_truthy():
    """``x is True``, ``x == "true"``, ``x in (True, "true")``,
    ``str(x).lower() in ("true", "1")``: each is a second definition of a
    flag, and each read some spelling differently from the others."""
    found = []
    for rel, tree in _sources():
        for owner, node in _enclosing(tree):
            if not isinstance(node, ast.Compare):
                continue
            if not any(
                _flag_literal(n) for n in (node.left, *node.comparators)
            ):
                continue
            if (rel, owner) in _DEFINITION | _NOT_A_FLAG:
                continue
            found.append(f"{rel}:{node.lineno} {ast.unparse(node)}")
    assert not found, (
        "a flag read by comparison; read it through `_keys.truthy` (and "
        "name its key in `_keys.FLAG_KEYS`):\n  " + "\n  ".join(found)
    )


def test_exemptions_still_exist():
    """An exemption whose function is gone exempts nothing; drop it."""
    seen = {
        (rel, owner)
        for rel, tree in _sources()
        for owner, node in _enclosing(tree)
        if isinstance(node, ast.Compare)
        and any(_flag_literal(n) for n in (node.left, *node.comparators))
    }
    assert _DEFINITION | _NOT_A_FLAG <= seen, (
        _DEFINITION | _NOT_A_FLAG
    ) - seen


#: Functions that test a FLAG_KEYS-named key for truth on a dict jm built
#: itself, not on a manifest row, each with why. Keyed by (file, function).
_NOT_A_MANIFEST_ROW = {
    # `f` is a CLI flag record from `_value_flag` / the string-enum branch
    # of `_app_flags`, whose `required` is always a Python bool (slot 8 of
    # `_config.init_params` is read through `_truthy` at its source).
    ("_app.py", "_argparse_block"),
    ("_app.py", "_c_argv_parser"),
}


def _flag_key(node: ast.AST, consts: "dict[str, str]") -> "str | None":
    """The ``FLAG_KEYS`` key *node* reads -- ``x.get(k[, d])`` or
    ``x[k]``, *k* a literal or a module-level string constant -- or None."""
    k: object = None
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
    ):
        k = node.args[0]
    elif isinstance(node, ast.Subscript):
        k = node.slice
    if isinstance(k, ast.Constant) and isinstance(k.value, str):
        key = k.value
    elif isinstance(k, ast.Name):
        key = consts.get(k.id)
    else:
        return None
    return key if key in _keys.FLAG_KEYS else None


def _truth_tested(tree: ast.AST):
    """Yield ``(function name, node)`` for every expression Python tests
    for truth: an ``if`` / ``while`` / ternary / ``assert`` test, an
    ``and`` / ``or`` operand, a ``not`` operand, a comprehension filter,
    and the argument of ``bool()`` (the element of ``any()`` / ``all()``
    over a comprehension)."""
    for owner, node in _enclosing(tree):
        if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
            yield owner, node.test
        elif isinstance(node, ast.BoolOp):
            for v in node.values:
                yield owner, v
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            yield owner, node.operand
        elif isinstance(node, ast.comprehension):
            for cond in node.ifs:
                yield owner, cond
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("bool", "any", "all")
            and node.args
        ):
            arg = node.args[0]
            if isinstance(arg, (ast.GeneratorExp, ast.ListComp)):
                yield owner, arg.elt
            else:
                yield owner, arg


def _bare_flag_reads(tree: ast.Module) -> "list[tuple[str, int, str]]":
    """``(function, line, source)`` of every bare truth test of a
    ``FLAG_KEYS`` key in *tree*."""
    consts = _module_strings(tree)
    return [
        (owner, node.lineno, ast.unparse(node))
        for owner, node in _truth_tested(tree)
        if _flag_key(node, consts)
    ]


def test_no_flag_is_read_bare_outside_truthy():
    """``if p.get("writable"):`` reads ``writable = "false"`` -- a spelling
    load accepts -- as on, and a capsule property grew a setter it was
    declared without. Every truth test of a flag goes through ``truthy``;
    a ``truthy(x.get(k))`` call is not itself a truth test, so it passes."""
    found = [
        f"{rel}:{line} {src}"
        for rel, tree in _sources()
        for owner, line, src in _bare_flag_reads(tree)
        if (rel, owner) not in _NOT_A_MANIFEST_ROW
    ]
    assert not found, (
        'a flag tested for truth bare, which reads the string "false" as '
        "on; read it through `_keys.truthy`:\n  " + "\n  ".join(found)
    )


def test_bare_read_exemptions_still_exist():
    """An exemption whose function no longer reads a flag bare is stale."""
    seen = {
        (rel, owner)
        for rel, tree in _sources()
        for owner, _line, _src in _bare_flag_reads(tree)
    }
    assert _NOT_A_MANIFEST_ROW <= seen, _NOT_A_MANIFEST_ROW - seen


@pytest.mark.parametrize(
    "src",
    [
        "def f(p):\n    if p.get('writable'):\n        pass\n",
        "def f(p):\n    return bool(p.get('required'))\n",
        "def f(p):\n    return [a for a in p if a.get('writable')]\n",
        "def f(p):\n    return any(a.get('writable') for a in p)\n",
        "def f(p):\n    return 1 if p['why'] else 0\n",
        "def f(p):\n    return not p.get('mutable', False)\n",
        "K = 'status_out'\ndef f(p):\n    return p.get(K) and 1\n",
    ],
)
def test_the_bare_scan_is_armed(src):
    """Each truth-test shape is found."""
    assert len(_bare_flag_reads(ast.parse(src))) == 1


@pytest.mark.parametrize(
    "src",
    [
        "def f(p):\n    if truthy(p.get('writable')):\n        pass\n",
        "def f(p):\n    return [a for a in p if truthy(a.get('writable'))]\n",
        "def f(p):\n    return bool(truthy(p.get('required')))\n",
        # Not a flag key, and not a truth test.
        "def f(p):\n    if p.get('doc'):\n        pass\n",
        "def f(p):\n    return p.get('writable', False)\n",
    ],
)
def test_the_bare_scan_passes_a_read_through_truthy(src):
    assert _bare_flag_reads(ast.parse(src)) == []


def _module_strings(tree: ast.Module) -> "dict[str, str]":
    """Module-level ``NAME = "literal"`` assignments."""
    out = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            out[node.targets[0].id] = node.value.value
    return out


def _truthy_keys() -> "tuple[set[str], list[str]]":
    """Every key the source reads through ``truthy`` / ``_truthy``, and
    every such read whose key cannot be named."""
    keys: "set[str]" = set()
    unnamed: "list[str]" = []
    for rel, tree in _sources():
        consts = _module_strings(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            if name not in ("truthy", "_truthy") or not node.args:
                continue
            arg = node.args[0]
            key = None
            if (
                isinstance(arg, ast.Call)
                and isinstance(arg.func, ast.Attribute)
                and arg.func.attr == "get"
                and arg.args
            ):
                k = arg.args[0]
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    key = k.value
                elif isinstance(k, ast.Name):
                    key = consts.get(k.id)
            if key is None:
                unnamed.append(f"{rel}:{node.lineno} {ast.unparse(node)}")
            else:
                keys.add(key)
    return keys, unnamed


def test_flag_keys_are_the_keys_truthy_reads():
    """Both directions: a key read through ``truthy`` that ``FLAG_KEYS``
    lacks is one the load refusal and the respell never look at, and a
    ``FLAG_KEYS`` entry nothing reads refuses values for no reader."""
    keys, unnamed = _truthy_keys()
    assert not unnamed, (
        "read a flag as `truthy(<table>.get(<key>))`, so its key is "
        "known:\n  " + "\n  ".join(unnamed)
    )
    assert keys - _keys.FLAG_KEYS == set(), "add to _keys.FLAG_KEYS"
    assert _keys.FLAG_KEYS - keys == set(), "no reader; drop from FLAG_KEYS"


def test_a_bool_only_flag_is_still_refused_in_the_old_spelling(tmp_path):
    """`flag_errors` leaves ``BOOL_ONLY_FLAGS`` to their own checks; the
    old spelling must still stop, not read as false."""
    assert _keys.BOOL_ONLY_FLAGS <= _keys.FLAG_KEYS
    root = _project(tmp_path)
    _spell(root, "streamable", 'true\ncreate_why = "True"')
    r = run_cli("apply", cwd=root)
    assert r.returncode != 0, r.stdout
    assert "create_why = 'True' must be true or false" in r.stderr, r.stderr


def test_the_scans_are_armed():
    """Each scan finds what it is for, on a source that has one."""
    tree = ast.parse("def f(m):\n    return m.get('x') is True\n")
    hits = [
        n
        for _o, n in _enclosing(tree)
        if isinstance(n, ast.Compare)
        and any(_flag_literal(x) for x in (n.left, *n.comparators))
    ]
    assert len(hits) == 1
    assert {"streamable", "optional", "retain"} <= _truthy_keys()[0]
