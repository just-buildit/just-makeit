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

    def check(after: str) -> None:
        ext = (root / "native/src/g/g_ext.c").read_text(encoding="utf-8")
        pyi = (root / "src/p/g.pyi").read_text(encoding="utf-8")
        assert ("G_setprop_level" in ext) is on, (after, ext)
        assert ("@level.setter" in pyi) is on, (after, pyi)
        init = "def __init__(self, gain: float) -> None"
        optional = "def __init__(self, gain: float = 1.0) -> None"
        assert (init if on else optional) in pyi, (after, pyi)

    check("apply")
    # A command that regenerates the object's glue without `apply`'s
    # replay, which rebuilds each property from the manifest first: the
    # properties context reads the manifest rows themselves.
    r = run_cli("property", "g", "other", "--type", "double", cwd=root)
    assert r.returncode == 0, r.stderr
    check("property")


def _nogil_project(tmp_path: Path) -> "tuple[Path, Path]":
    """An object ``g`` with a ``--nogil`` method ``w``, and its manifest."""
    r = run_cli("new", "p", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    root = tmp_path / "p"
    for argv in (
        ("object", "g", "--init-param", "gain:double:1.0"),
        ("method", "g", "w", "--return-type", "int", "--nogil"),
    ):
        r = run_cli(*argv, cwd=root)
        assert r.returncode == 0, r.stderr
    return root, _manifest_with(root, "nogil")


def _releases_gil(root: Path) -> bool:
    ext = (root / "native/src/g/g_ext.c").read_text(encoding="utf-8")
    return "Py_BEGIN_ALLOW_THREADS" in ext


def test_nogil_false_never_releases_the_gil(tmp_path):
    """``nogil`` had no ``truthy`` reader and was no flag: ``nogil =
    "False"`` loaded, was not respelled, and the method released the GIL.
    Now ``"False"`` is refused naming ``jm upgrade``, which respells it to
    ``false``; ``false`` and ``"false"`` release nothing."""
    root, path = _nogil_project(tmp_path)
    assert _releases_gil(root)
    _spell(root, "nogil", '"False"')
    r = run_cli("apply", cwd=root)
    assert r.returncode != 0, r.stdout
    assert 'g.methods[w]: nogil = "False"' in r.stderr, r.stderr
    assert "just-makeit upgrade" in r.stderr, r.stderr
    assert _releases_gil(root)  # refused, so nothing was rewritten
    r = run_cli("upgrade", cwd=root)
    assert r.returncode == 0, r.stderr
    assert "\nnogil = false\n" in path.read_text(encoding="utf-8")
    for spelling in ("false", '"false"'):
        _spell(root, "nogil", spelling)
        r = run_cli("apply", cwd=root)
        assert r.returncode == 0, r.stderr
        assert not _releases_gil(root), spelling
    _spell(root, "nogil", "true")
    assert run_cli("apply", cwd=root).returncode == 0
    assert _releases_gil(root)


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


#: Reads of a FLAG_KEYS-named key that are not ``truthy(...)``, each
#: legitimately raw, each with why. Keyed by (file, enclosing function,
#: the read's source) and valued by how many times that read occurs, so an
#: exemption covers those reads and not a second copy added beside them.
_RAW_READ_OK = {
    # jm's own CLI flag records (`_value_flag`, the string-enum branch of
    # `_app_flags`): `required` there is always a Python bool, built from
    # slot 8 of `_config.init_params`, which reads it through `_truthy`.
    # Three reads (the choice flag's spec and help, the value flag's spec)
    # and two (the choice and the number `must`).
    ("_app.py", "_argparse_block", "f.get('required')"): 3,
    ("_app.py", "_c_argv_parser", "f.get('required')"): 2,
    # The bool-only load validators: each reads the raw value to refuse
    # anything but a TOML bool, naming it -- `truthy` would hide the
    # value they exist to report.
    # Twice: the isinstance test, then the refusal naming the value.
    ("_config.py", "_why_key_errors", "mod['create_why']"): 2,
    ("_config.py", "_why_key_errors", "fn['why']"): 1,
    ("_config.py", "_create_why_errors", "table['create_why']"): 1,
    ("_config.py", "_why_key_errors", "f['parse_why']"): 1,
    ("_config.py", "_short_out_errors", "entry['short_out']"): 1,
    ("_config.py", "_no_count_errors", "entry['no_count']"): 1,
    (
        "_context/_diagnostics.py",
        "method_status_out_errors",
        "m['status_out']",
    ): 1,
    ("_context/_diagnostics.py", "method_why_errors", "m['why']"): 1,
    ("_context/_parse.py", "required_errors", "p[REQUIRED_KEY]"): 1,
    ("_retain.py", "errors", "p[KEY]"): 1,
    # `[project.bench]` is the benchmark settings TABLE, not a method's
    # `bench = false` opt-out (`_keys._FLAG_NAMED_TABLES`).
    ("_apply.py", "_replay", "cfg.get('project', {}).get('bench')"): 1,
    (
        "_config.py",
        "project_bench_block_sizes",
        "cfg.get('project', {}).get('bench', {})",
    ): 1,
    (
        "_config.py",
        "bench_timeout",
        "cfg.get('project', {}).get('bench', {})",
    ): 1,
    # `NATIVE_FILES` maps a generated native file's role to its place; its
    # `bench` row is a path, not a manifest value.
    ("_hollow.py", "unrun_benches", "INC.NATIVE_FILES['bench']"): 1,
    (
        "_object.py",
        "_write_module_test_and_bench",
        "INC.NATIVE_FILES[kind]",
    ): 1,
    # `jm method`'s own keyword arguments (`_args = dict(locals())`), read
    # by name over `_SIGNATURE_COERCIONS`, whose keys mix flag-named ones
    # (`why`, `status_out`) with the rest: Python values from the CLI or
    # the replay, never a manifest spelling.
    ("_method.py", "run", "_args[k]"): 2,
    # A kind module's getter names its result struct, `out = "<C type>"`;
    # `out` is a flag only on a param row (`_keys._FLAG_ROW_SCOPE`).
    ("_handle.py", "_scalar_out", "g.get('out', '')"): 1,
    ("_handle.py", "render_getsets", "g['out']"): 1,
    ("_handle.py", "render_type", "g['out']"): 1,
    # `INPUT_SIDE_KEYS` maps each key NAME to the input side it reads; the
    # loop's `k` indexes that table, not a manifest row.
    ("_method.py", "shape_keys_why_not", "_keys.INPUT_SIDE_KEYS[k]"): 1,
}


#: A value the resolver cannot spell from the source.
_UNKNOWN = object()


def _module_assigns(tree: ast.Module) -> "dict[str, ast.AST]":
    """Module-level ``NAME = <expr>`` (or ``NAME: T = <expr>``) values."""
    out: "dict[str, ast.AST]" = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            out[node.targets[0].id] = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and isinstance(node.target, ast.Name)
        ):
            out[node.target.id] = node.value
    return out


_GLOBALS: "dict[str, ast.AST]" = {}


def _globals() -> "dict[str, ast.AST]":
    """Every module-level constant in the source, by name (the first
    module to define a name wins), so ``_keys.SOME_KEYS`` resolves as
    well as a bare ``SOME_KEYS``."""
    if not _GLOBALS:
        for _rel, tree in _sources():
            for name, value in _module_assigns(tree).items():
                _GLOBALS.setdefault(name, value)
    return _GLOBALS


_ITERABLE_CALLS = ("tuple", "list", "sorted", "set", "frozenset", "reversed")


def _value(node: ast.AST, env: "dict[str, ast.AST]", seen=()):
    """The Python value *node* spells, as far as the source does, or
    ``_UNKNOWN``: a constant; a tuple / list / set / dict literal; a name
    (the function's own single assignment, then the module's, then any
    source module's) or an attribute (any source module's); one of
    :data:`_ITERABLE_CALLS` over one; ``.keys()`` / ``.items()`` /
    ``.values()`` of a dict; and ``+`` / ``|`` / ``&`` / ``-``."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(_value(e, env, seen) for e in node.elts)
    if isinstance(node, ast.Set):
        vals = [_value(e, env, seen) for e in node.elts]
        return _UNKNOWN if _UNKNOWN in vals else frozenset(vals)
    if isinstance(node, ast.Dict):
        out = {}
        for k, v in zip(node.keys, node.values):
            key = _UNKNOWN if k is None else _value(k, env, seen)
            if key is _UNKNOWN or not _hashable(key):
                return _UNKNOWN
            out[key] = _value(v, env, seen)
        return out
    if isinstance(node, (ast.Name, ast.Attribute)):
        name = node.id if isinstance(node, ast.Name) else node.attr
        if name in seen:
            return _UNKNOWN
        local = env if isinstance(node, ast.Name) else {}
        src = local.get(name, _globals().get(name))
        if src is None:
            return _UNKNOWN
        return _value(src, local, (*seen, name))
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Name) and f.id in _ITERABLE_CALLS:
            if len(node.args) != 1:
                return _UNKNOWN
            items = _elements(_value(node.args[0], env, seen))
            if items is _UNKNOWN:
                return _UNKNOWN
            if f.id in ("set", "frozenset"):
                if any(i is _UNKNOWN or not _hashable(i) for i in items):
                    return _UNKNOWN
                return frozenset(items)
            return tuple(items)
        if (
            isinstance(f, ast.Attribute)
            and f.attr in ("keys", "items", "values")
            and not node.args
        ):
            d = _value(f.value, env, seen)
            if not isinstance(d, dict):
                return _UNKNOWN
            return tuple(getattr(d, f.attr)())
        return _UNKNOWN
    if isinstance(node, ast.BinOp):
        a = _value(node.left, env, seen)
        b = _value(node.right, env, seen)
        if a is _UNKNOWN or b is _UNKNOWN:
            return _UNKNOWN
        if isinstance(node.op, ast.Add):
            if isinstance(a, tuple) and isinstance(b, tuple):
                return a + b
            return _UNKNOWN
        sa, sb = _elements(a), _elements(b)
        if sa is _UNKNOWN or sb is _UNKNOWN:
            return _UNKNOWN
        if not all(_hashable(i) for i in (*sa, *sb)):
            return _UNKNOWN
        sa, sb = frozenset(sa), frozenset(sb)
        if isinstance(node.op, ast.BitOr):
            return sa | sb
        if isinstance(node.op, ast.BitAnd):
            return sa & sb
        if isinstance(node.op, ast.Sub):
            return sa - sb
    return _UNKNOWN


def _hashable(value: object) -> bool:
    try:
        hash(value)
    except TypeError:
        return False
    return True


def _elements(value: object):
    """What iterating *value* yields (a dict's keys), or ``_UNKNOWN``."""
    if isinstance(value, dict):
        return list(value)
    if isinstance(value, (tuple, frozenset)):
        return list(value)
    return _UNKNOWN


def _bind(target: ast.AST, items) -> "dict[str, object]":
    """``{name: set of values | _UNKNOWN}`` for a loop *target* over
    *items*: a name takes every item, a tuple target takes each item's
    value at its position."""
    if isinstance(target, ast.Name):
        if items is _UNKNOWN or _UNKNOWN in items:
            return {target.id: _UNKNOWN}
        return {target.id: {i for i in items if _hashable(i)}}
    if isinstance(target, (ast.Tuple, ast.List)):
        out: "dict[str, object]" = {}
        width = len(target.elts)
        for pos, sub in enumerate(target.elts):
            col = _UNKNOWN
            if items is not _UNKNOWN and all(
                isinstance(i, tuple) and len(i) == width for i in items
            ):
                col = [i[pos] for i in items]
            out.update(_bind(sub, col))
        return out
    return {}


#: What `_enclosing` names an owner by: a function. A lambda or class body
#: inside one is read as part of it.
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _own_nodes(scope: ast.AST):
    """Every node in *scope*'s own body: not inside a nested function,
    which binds its own names."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def _bound_names(node: ast.AST) -> "list[str]":
    """The names *node* binds, in any form: ``=`` (unpacking too), ``+=``,
    an annotation, a ``for`` / comprehension target, ``:=``, ``with ...
    as``, ``except ... as``, an import, a nested def or class."""
    targets: "list[ast.AST]" = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
        targets = [node.target]
    elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
        targets = [node.target]
    elif isinstance(node, ast.withitem) and node.optional_vars is not None:
        targets = [node.optional_vars]
    elif isinstance(node, ast.ExceptHandler) and node.name:
        return [node.name]
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        return [(a.asname or a.name).split(".")[0] for a in node.names]
    elif isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    ):
        return [node.name]
    return [
        n.id
        for t in targets
        for n in ast.walk(t)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
    ]


def _scope_env(scope: ast.AST) -> "dict[str, ast.AST]":
    """*scope*'s own names: each bound exactly once by a plain ``name =
    <expr>`` maps to that expression; every other name it binds -- twice,
    or any other way, or as a parameter -- maps to ``_UNKNOWN``, so it
    shadows a module constant of the same name rather than resolving as
    one of its values."""
    counts: "dict[str, int]" = {}
    simple: "dict[str, ast.AST]" = {}
    params: "list[str]" = []
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        a = scope.args
        params = [
            p.arg
            for p in (
                *a.posonlyargs,
                *a.args,
                *a.kwonlyargs,
                a.vararg,
                a.kwarg,
            )
            if p is not None
        ]
    for node in _own_nodes(scope):
        for name in _bound_names(node):
            counts[name] = counts.get(name, 0) + 1
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            simple[node.targets[0].id] = node.value
    unknown = ast.Constant(value=_UNKNOWN)
    env: "dict[str, ast.AST]" = {name: unknown for name in (*params, *counts)}
    for name, value in simple.items():
        if counts.get(name) == 1 and name not in params:
            env[name] = value
    return env


def _scope_ids(tree: ast.Module) -> "dict[int, int]":
    """``id(node)`` -> ``id`` of the function (or module) whose own body
    holds it: the key a loop variable is bound under, so two functions of
    one name keep their own."""
    out: "dict[int, int]" = {}

    def walk(node: ast.AST, scope: int) -> None:
        for child in ast.iter_child_nodes(node):
            out[id(child)] = scope
            walk(child, id(child) if isinstance(child, _SCOPES) else scope)

    walk(tree, id(tree))
    return out


def _loop_keys(tree: ast.Module) -> "dict[tuple[int, str], set[str]]":
    """``(scope, loop variable)`` -> the FLAG_KEYS keys it takes, for
    every ``for`` loop and comprehension whose iterable resolves (see
    :func:`_value`) to keys that include one: a read ``table.get(<var>)``
    there reads those flags. Each function resolves names in its own
    environment (keyed by the function, so two of one name do not share),
    then the module's. An iterable that does not resolve yields nothing
    -- this scan is fail-open by decision (gh-2120: failing closed flagged
    378 reads keyed by C types, component names and symbols)."""
    module = _module_assigns(tree)
    out: "dict[tuple[int, str], set[str]]" = {}
    scopes = [tree] + [n for n in ast.walk(tree) if isinstance(n, _SCOPES)]
    for scope in scopes:
        owner = id(scope)
        env = {**module, **(_scope_env(scope) if scope is not tree else {})}
        for node in _own_nodes(scope):
            if not isinstance(node, (ast.For, ast.comprehension)):
                continue
            items = _elements(_value(node.iter, env))
            for name, vals in _bind(node.target, items).items():
                if vals is _UNKNOWN:
                    continue
                flags = {v for v in vals if isinstance(v, str)}
                flags &= _keys.FLAG_KEYS
                if flags:
                    out.setdefault((owner, name), set()).update(flags)
    return out


def _flag_key(
    node: ast.AST,
    consts: "dict[str, str]",
    scope: int = 0,
    loops: "dict[tuple[int, str], set[str]] | None" = None,
) -> "str | None":
    """The ``FLAG_KEYS`` key *node* reads -- ``x.get(k[, d])`` or a load
    of ``x[k]``, *k* a literal, a module-level string constant, or a loop
    variable over a module tuple that names a flag -- or None."""
    k: object = None
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
    ):
        k = node.args[0]
    elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
        k = node.slice
    if isinstance(k, ast.Constant) and isinstance(k.value, str):
        return k.value if k.value in _keys.FLAG_KEYS else None
    if isinstance(k, ast.Name):
        if k.id in consts:
            key = consts[k.id]
            return key if key in _keys.FLAG_KEYS else None
        hit = (loops or {}).get((scope, k.id))
        if hit:
            return sorted(hit)[0]
    return None


def _is_truthy_call(node: "ast.AST | None") -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
    return name in ("truthy", "_truthy")


def _raw_flag_reads(tree: ast.Module) -> "list[tuple[str, int, str]]":
    """``(function, line, source)`` of every read of a ``FLAG_KEYS`` key
    in *tree* that is not the first argument of ``truthy`` / ``_truthy``."""
    consts = _module_strings(tree)
    loops = _loop_keys(tree)
    scope = _scope_ids(tree)
    parent: "dict[int, ast.AST]" = {
        id(child): node
        for node in ast.walk(tree)
        for child in ast.iter_child_nodes(node)
    }
    out = []
    for owner, node in _enclosing(tree):
        if not _flag_key(node, consts, scope[id(node)], loops):
            continue
        up = parent.get(id(node))
        if _is_truthy_call(up) and up.args and up.args[0] is node:
            continue
        out.append((owner, node.lineno, ast.unparse(node)))
    return out


def _raw_reads_by_row():
    """``(file, function, source) -> [line, ...]`` for every raw read."""
    rows: "dict[tuple[str, str, str], list[int]]" = {}
    for rel, tree in _sources():
        for owner, line, src in _raw_flag_reads(tree):
            rows.setdefault((rel, owner, src), []).append(line)
    return rows


def test_no_flag_is_read_raw_outside_truthy():
    """``if p.get("writable"):`` reads ``writable = "false"`` -- a spelling
    load accepts -- as on, and so does ``w = p.get("writable", False)``
    tested three hundred lines later. Every read of a flag's value goes
    through ``truthy``; what legitimately reads it raw is named in
    ``_RAW_READ_OK`` with why, as many times as it occurs -- a second
    copy of an exempt read beside it is a new read, and refused."""
    found = []
    for (rel, owner, src), lines in sorted(_raw_reads_by_row().items()):
        if len(lines) > _RAW_READ_OK.get((rel, owner, src), 0):
            found += [f"{rel}:{ln} {src}" for ln in lines]
    assert not found, (
        'a flag read raw, which reads the string "false" as on; read it '
        "through `_keys.truthy` (`_keys.is_set` in a loop over keys):\n  "
        + "\n  ".join(found)
    )


def test_raw_read_exemptions_still_exist():
    """An exemption whose reads are gone, or fewer than it counts, is
    stale; drop it or lower its count."""
    rows = _raw_reads_by_row()
    stale = {
        row: (n, len(rows.get(row, [])))
        for row, n in _RAW_READ_OK.items()
        if len(rows.get(row, [])) < n
    }
    assert not stale, stale


@pytest.mark.parametrize(
    "src",
    [
        "def f(p):\n    if p.get('writable'):\n        pass\n",
        "def f(p):\n    return bool(p.get('required'))\n",
        "def f(p):\n    return [a for a in p if a.get('writable')]\n",
        "def f(p):\n    return 1 if p['why'] else 0\n",
        "def f(p):\n    w = p.get('writable', False)\n    return w\n",
        "def f(m):\n    so = m.get('status_out')\n    if so:\n        pass\n",
        "K = 'status_out'\ndef f(p):\n    return p.get(K)\n",
        "KS = ('out_size', 'why')\n"
        "def f(fn):\n    for k in KS:\n        if fn.get(k):\n"
        "            return k\n",
        # gh-2120 review: a comprehension over an inline literal, and a
        # loop over a wrapped module tuple.
        "def f(prop):\n    return any(prop.get(k) for k in ('writable',))\n",
        "KS = ('out_size', 'why')\n"
        "def f(fn):\n    for flag in tuple(KS):\n        if fn.get(flag):\n"
        "            return flag\n",
        "def f(fn):\n    for k in ['doc'] + ['why']:\n        fn[k]\n",
        "def f(fn):\n    return {k: fn.get(k) for k in sorted({'retain'})}\n",
        # gh-2120 review of ca897841: a tuple of pairs bound by position,
        # a dict literal, a tuple assigned in the function, and a set
        # expression over a module constant reached by attribute.
        "def f(m):\n    for key, flag in (('why', '--why'),):\n"
        "        if m.get(key):\n            return flag\n",
        "def f(p):\n    for key in {'writable': 'set'}:\n"
        "        if p.get(key):\n            return key\n",
        "def f(p):\n    setters = ('writable',)\n    for k in setters:\n"
        "        if p.get(k):\n            return k\n",
        "def f(m):\n    for key in _keys.BOOL_ONLY_FLAGS & {'why'}:\n"
        "        m.get(key)\n",
        # gh-2120 review of b4d4b313 (F2): two functions of one name keep
        # their own names -- the second's `ks` does not make the first's
        # unknown, nor its loop the second's.
        "def a(p):\n    def g():\n        ks = ('writable',)\n"
        "        for k in ks:\n            p.get(k)\n"
        "def b(p):\n    def g():\n        ks = ('doc',)\n"
        "        for k in ks:\n            p.get(k)\n",
        "D = {'why': 1, 'doc': 2}\n"
        "def f(m):\n    for key, _v in D.items():\n        m.get(key)\n",
    ],
)
def test_the_raw_scan_is_armed(src):
    """Each shape of a raw read is found."""
    assert len(_raw_flag_reads(ast.parse(src))) == 1


@pytest.mark.parametrize(
    "src",
    [
        "def f(p):\n    if truthy(p.get('writable')):\n        pass\n",
        "def f(p):\n    return C._truthy(p.get('required'))\n",
        "def f(p):\n    p['writable'] = True\n",
        "def f(p):\n    return 'why' in p\n",
        "def f(p):\n    if p.get('doc'):\n        pass\n",
        "KS = ('out_size', 'doc')\n"
        "def f(fn):\n    for k in KS:\n        if fn.get(k):\n"
        "            return k\n",
        "KS = ('out_size', 'why')\n"
        "def f(fn):\n    for k in KS:\n        if is_set(fn, k):\n"
        "            return k\n",
        "def f(m):\n    for key, flag in (('doc', '--doc'),):\n"
        "        if m.get(key):\n            return flag\n",
        # F2: a name bound twice, or another way than `=`, is unknown --
        # never one of its values (`ks += ...`, a `for` rebinding, `:=`,
        # unpacking, a parameter shadowing a module constant).
        "def f(p):\n    ks = ('writable',)\n    ks += ('doc',)\n"
        "    for k in ks:\n        p.get(k)\n",
        "def f(p):\n    ks = ('writable',)\n    for ks in [()]:\n        pass\n"
        "    for k in ks:\n        p.get(k)\n",
        "def f(p):\n    ks = ('writable',)\n    if (ks := ()):\n        pass\n"
        "    for k in ks:\n        p.get(k)\n",
        "def f(p):\n    ks = ('writable',)\n    ks, _ = (), 1\n"
        "    for k in ks:\n        p.get(k)\n",
        "KS = ('writable',)\n"
        "def f(p, KS=()):\n    for k in KS:\n        p.get(k)\n",
        "def f(m):\n    for key in _keys.BOOL_ONLY_FLAGS & {'name', 'type'}:"
        "\n        m.get(key)\n",
    ],
)
def test_the_raw_scan_passes_a_read_through_truthy(src):
    assert _raw_flag_reads(ast.parse(src)) == []


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


#: The reader that takes its key from its caller: ``_keys.is_set(table,
#: key)``, for a loop over a list of keys. Its own ``truthy`` call names no
#: key; each call of it names one (resolved below), and counts as a read.
_KEY_FROM_CALLER = {("_keys.py", "is_set")}

#: ``is_set`` calls over EVERY key of a row, which no key set names.
_IS_SET_EVERY_KEY = {
    # Which backing keys a property row sets: each key it carries, a flag
    # read as one (`field = "false"` is not a backing).
    ("_borrow.py", "_property_read"),
}


def _truthy_keys() -> "tuple[set[str], list[str]]":
    """Every key the source reads through ``truthy`` / ``_truthy`` (or
    ``is_set``, whose key a loop over a module tuple names), and every
    such read whose key cannot be named."""
    keys: "set[str]" = set()
    unnamed: "list[str]" = []
    for rel, tree in _sources():
        consts = _module_strings(tree)
        loops = _loop_keys(tree)
        scope = _scope_ids(tree)
        for owner, node in _enclosing(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            if name == "is_set" and len(node.args) == 2:
                k = node.args[1]
                hit = (scope[id(node)], getattr(k, "id", ""))
                if isinstance(k, ast.Name) and hit in loops:
                    keys |= loops[hit]
                    continue
                if (rel, owner) in _IS_SET_EVERY_KEY:
                    continue
                unnamed.append(f"{rel}:{node.lineno} {ast.unparse(node)}")
                continue
            if name not in ("truthy", "_truthy") or not node.args:
                continue
            if (rel, owner) in _KEY_FROM_CALLER:
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


#: A command's `run()` parameter that takes a manifest key under another
#: spelling (a trailing ``_`` dodges a module name; a short form).
_RUN_PARAM_KEY = {
    "pytest_": "pytest",
    "pytest_benchmark_": "pytest_benchmark",
    "step_delegates": "step_delegates_to_steps",
}


def _vocabulary() -> "set[str]":
    """Every key a manifest table may carry: `_keys`' key sets."""
    out: "set[str]" = set()
    for name in dir(_keys):
        if not name.endswith("KEYS"):
            continue
        value = getattr(_keys, name)
        tables = value.values() if isinstance(value, dict) else [value]
        for t in tables:
            if isinstance(t, frozenset):
                out |= {k for k in t if isinstance(k, str)}
    return out


def _registry_bools() -> "dict[str, set[str]]":
    """Every manifest key a TYPED registry declares a bool, with the
    registries that do -- independent of who reads it:

    - a flag coercion in ``_method._SIGNATURE_COERCIONS``;
    - a bool field of ``_keys.INIT_PARAM_FIELDS``;
    - a ``bool`` parameter of a command's ``run()`` named for a manifest
      key (``_RUN_PARAM_KEY`` maps the ones spelled otherwise)."""
    from just_makeit import _method

    out: "dict[str, set[str]]" = {}
    for key, (coerce, _default) in _method._SIGNATURE_COERCIONS.items():
        if coerce in (bool, _keys.truthy):
            out.setdefault(key, set()).add("_SIGNATURE_COERCIONS")
    for key, is_bool in _keys.INIT_PARAM_FIELDS:
        if is_bool:
            out.setdefault(key, set()).add("INIT_PARAM_FIELDS")
    vocab = _vocabulary()
    for rel, tree in _sources():
        for node in tree.body:
            if not (isinstance(node, ast.FunctionDef) and node.name == "run"):
                continue
            for a in node.args.args + node.args.kwonlyargs:
                ann = ast.unparse(a.annotation) if a.annotation else ""
                key = _RUN_PARAM_KEY.get(a.arg, a.arg)
                if ann.strip("'\"") == "bool" and key in vocab:
                    out.setdefault(key, set()).add(f"{rel} run()")
    return out


def test_flag_keys_are_the_registries_bools():
    """Both directions, against registries that do not know who reads a
    key: a bool there that is no flag is one ``flag_errors`` never refuses
    and every bare reader reads ``"False"`` as on -- `nogil = "False"`
    released the GIL; a flag no registry declares a bool must be named in
    ``UNTYPED_FLAG_KEYS``, which a key leaves when it gains a typed home."""
    reg = _registry_bools()
    typed = set(reg)
    missing = {k: sorted(reg[k]) for k in typed - _keys.FLAG_KEYS}
    assert not missing, f"a registry's bool that is no flag: {missing}"
    stray = _keys.FLAG_KEYS - typed - _keys.UNTYPED_FLAG_KEYS
    assert not stray, (
        "a flag no typed registry declares a bool; name it, with its "
        f"table, in _keys.UNTYPED_FLAG_KEYS: {sorted(stray)}"
    )
    rehomed = {k: sorted(reg[k]) for k in _keys.UNTYPED_FLAG_KEYS & typed}
    assert not rehomed, f"typed now; drop from UNTYPED_FLAG_KEYS: {rehomed}"
    assert _keys.UNTYPED_FLAG_KEYS <= _keys.FLAG_KEYS


def test_no_signature_flag_coerces_through_bool():
    """``bool("false")`` is True: a flag's coercion is ``truthy``."""
    from just_makeit import _method

    plain = [
        k for k, (c, _d) in _method._SIGNATURE_COERCIONS.items() if c is bool
    ]
    assert not plain, plain


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
