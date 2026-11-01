"""gh-2423: ``[project] layout = "mirrored"`` -- one primitive places every
generated native test and benchmark.

doppler asked for the C tree to read one way: headers already sit at
``native/inc/<pkg>/<comp>/`` (`_incpath`), and jm wrote every C test and
benchmark flat beside each other, found only by file-name prefix. The
mirrored layout puts ``test_<c>_core.c`` and ``test_<c>_symbols.c`` under
``native/tests/<dir>/`` and ``bench_<c>_core.c`` under
``native/benchmarks/<dir>/``, ``<dir>`` being the directory the component's
header has below ``native/inc/<pkg>/`` (`_incpath.core_dir`).

The paths were spelled by hand at about forty sites -- writers, the remover,
the hollow-target scan, the ownership rules, the upgrade migrations, three
build templates and two Python CMake emitters -- so a layout switch would
have been an edit at each, with every missed one a file written in one
place and built, scanned or removed in another. They now all ask
`_incpath.native_rel` (or its slots), reading `_incpath.NATIVE_FILES`.

GATES:

1. Registration-free: no jm module but `_incpath` spells a generated
   native test/bench path -- the trees, the file names or the harness
   headers -- and no build template spells a tree (read as source: the AST,
   prose excluded).
2. Behaviour: in a mirrored project every kind of file, for a standalone
   component, an object of a dotted module, its collocated object and a
   function module, lands where the primitive says and nowhere flat; the
   harnesses stay at the tree roots; `status --check` is clean; the project
   configures, builds and passes `ctest` with no Python.
3. Byte-identity: with the key absent, the regions this change templated
   render exactly what jm rendered before it (pinned from origin/main
   85ab6e69).
4. Switching: setting the key on an existing project and running `apply`
   moves the author's files with their edits, creates no flat duplicate,
   leaves no CMake naming an old path (a hand-registered one is respelled),
   and removing the key moves them back.
5. An unknown value is refused before anything is written.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _createonly
from just_makeit import _hollow
from just_makeit import _incpath as INC

PKG = Path(__file__).resolve().parent.parent / "src" / "just_makeit"

#: The owner, and a GENERATED record of what released jms rendered.
_EXEMPT = {"_incpath.py", "_installhistory.py"}


def _modules():
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG)
        if {"templates", "examples"} & set(rel.parts) or rel.name in _EXEMPT:
            continue
        yield rel, ast.parse(path.read_text(encoding="utf-8"), str(path))


def _prose(tree) -> "set[int]":
    """ids of string constants that are docstrings or bare string lines."""
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }


def _is_str(node, value: str) -> bool:
    return isinstance(node, ast.Constant) and node.value == value


def _ends_with_native(node) -> bool:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _is_str(node.right, "native")
    if isinstance(node, ast.Call) and node.args:
        return _is_str(node.args[-1], "native")
    return _is_str(node, "native")


#: Derived from the primitive's own table, so a kind added there is gated.
_TREES = sorted(INC.HARNESS)
_TREE_LEAVES = {t.split("/", 1)[1] for t in _TREES}
_NAMES = {name.replace("{}", "\x00") for _t, name in INC.NATIVE_FILES.values()}
_HARNESSES = set(INC.HARNESS.values())


def _fstring_shape(node) -> "str | None":
    """An f-string as text with each placeholder as NUL; a constant as is."""
    if isinstance(node, ast.JoinedStr):
        return "".join(
            p.value if isinstance(p, ast.Constant) else "\x00"
            for p in node.values
        )
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _findings(tree, rel) -> "list[str]":
    prose = _prose(tree)
    hits = []
    for node in ast.walk(tree):
        if id(node) in prose:
            continue
        text = _fstring_shape(node)
        if text is not None and not isinstance(
            getattr(node, "_parent", None), ast.JoinedStr
        ):
            if any(t in text for t in _TREES):
                hits.append(f"{rel}:{node.lineno}  {text[:60]!r}")
            elif text in _NAMES or text in _HARNESSES:
                hits.append(f"{rel}:{node.lineno}  {text!r}")
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Div)
            and any(_is_str(node.right, leaf) for leaf in _TREE_LEAVES)
            and _ends_with_native(node.left)
        ):
            hits.append(f'{rel}:{node.lineno}  ... / "native" / "<tree>"')
        if isinstance(node, ast.Call) and any(
            _is_str(a, "native")
            and isinstance(b, ast.Constant)
            and b.value in _TREE_LEAVES
            for a, b in zip(node.args, node.args[1:])
        ):
            hits.append(f'{rel}:{node.lineno}  Path(..., "native", "<tree>")')
    return hits


def _mark_parents(tree):
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._parent = node
    return tree


def _template_findings() -> "list[str]":
    hits = []
    for sub in ("cmake", "make"):
        for path in sorted((PKG / "templates" / sub).rglob("*")):
            if not path.is_file():
                continue
            for n, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if line.lstrip().startswith("#"):
                    continue
                if any(t in line for t in _TREES):
                    hits.append(f"templates/{sub}/{path.name}:{n}  {line}")
    return hits


def test_only_incpath_spells_a_native_test_path():
    hits = []
    for rel, tree in _modules():
        hits += _findings(_mark_parents(tree), rel)
    hits += _template_findings()
    assert hits == [], (
        "spell a generated native test/bench path through `_incpath` "
        "(INC.native_path, INC.native_rel, INC.harness_rel, "
        "INC.native_slots ...):\n" + "\n".join(hits)
    )


def test_the_gate_sees_what_it_guards():
    """A gate that finds nothing to read passes while measuring nothing:
    feed it each shape it refuses and require a finding for each."""
    src = (
        "root / 'native' / 'tests' / f'test_{c}_core.c'\n"
        "x = 'native/benchmarks'\n"
        "y = f'bench_{c}_core.c'\n"
        "z = 'jm_test.h'\n"
        "w = Path(r, 'native', 'benchmarks')\n"
    )
    found = _findings(_mark_parents(ast.parse(src)), "probe")
    assert len(found) == 6, found
    assert len(list(_modules())) > 50
    uses = sum(
        p.read_text(encoding="utf-8").count("INC.native_")
        for p in PKG.glob("*.py")
    )
    assert uses >= 15, uses


# ── behaviour: a mirrored project, end to end ──────────────────────────────


def _jm(cwd, *args):
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r


def _declare(root: Path, value: str) -> None:
    toml = root / "just-makeit.toml"
    text = toml.read_text(encoding="utf-8")
    text = re.sub(r'^layout = "[^"]*"\n', "", text, flags=re.M)
    if value:
        text = text.replace("[project]\n", f'[project]\nlayout = "{value}"\n')
    toml.write_text(text, encoding="utf-8")


def _scaffold(where: Path, layout: str) -> Path:
    _jm(where, "new", "p")
    root = where / "p"
    _declare(root, layout)
    _jm(root, "object", "gain")
    _jm(root, "module", "dsp.filters")
    _jm(root, "object", "fir", "--module", "dsp.filters")
    _jm(root, "object", "dsp_filters", "--module", "dsp.filters")
    _jm(root, "module", "util")
    _jm(
        root,
        "function",
        "clamp",
        "--module",
        "util",
        "--param",
        "x:double",
        "--return-type",
        "double",
    )
    _jm(root, "apply")
    return root


@pytest.fixture(scope="module")
def mirrored(tmp_path_factory) -> Path:
    return _scaffold(tmp_path_factory.mktemp("mirrored"), "mirrored")


#: Each name, and the kinds jm generates for it: a function module gets no
#: link table (it has no binding to check).
_EXPECTED = {
    "gain": ("test", "symbols", "bench"),
    "fir": ("test", "symbols", "bench"),
    "dsp_filters": ("test", "symbols", "bench"),
    "util": ("test", "bench"),
}


def _native_files(root: Path) -> "set[str]":
    return {
        p.relative_to(root).as_posix()
        for t in INC.HARNESS
        for p in (root / t).rglob("*")
        if p.is_file()
    }


def test_every_kind_lands_in_its_mirrored_dir(mirrored):
    want = {INC.harness_rel(t) for t in INC.HARNESS}
    for name, kinds in _EXPECTED.items():
        for kind in kinds:
            rel = INC.native_rel(kind, name, mirrored)
            tree, file = INC.NATIVE_FILES[kind]
            # Independently of the primitive: the header's directory.
            header_dir = INC.core_h(mirrored, name).parent.name
            assert rel == f"{tree}/{header_dir}/{file.format(name)}"
            want.add(rel)
    assert _native_files(mirrored) == want


def test_the_build_files_name_the_mirrored_paths(mirrored):
    texts = "".join(
        p.read_text(encoding="utf-8")
        for p in (mirrored / "native" / "src").glob("*/CMakeLists.txt")
    )
    for name, kinds in _EXPECTED.items():
        for kind in kinds:
            assert INC.native_rel(kind, name, mirrored) in texts
            tree, file = INC.NATIVE_FILES[kind]
            assert f"{tree}/{file.format(name)}" not in texts
    assert texts.count(f"${{CMAKE_SOURCE_DIR}}/{INC.TESTS_DIR})") == 4


def test_status_is_clean_and_scans_the_mirrored_tree(mirrored):
    _jm(mirrored, "status", "--check")
    assert _hollow.built_stems(mirrored, "bench") == set(_EXPECTED)
    stray = mirrored / INC.TESTS_DIR / "gain" / "test_stray_core.c"
    stray.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    try:
        orphans = _hollow.orphans(mirrored, {"project": {"name": "p"}})
        assert [o.rel for o in orphans] == [
            stray.relative_to(mirrored).as_posix()
        ]
    finally:
        stray.unlink()


def test_the_ownership_rules_classify_the_mirrored_files(mirrored):
    for name, kinds in _EXPECTED.items():
        for kind in kinds:
            rule = _createonly.classify(
                INC.native_rel(kind, name, mirrored), mirrored
            )
            assert rule is not None, (kind, name)
            assert rule.kind == (
                _createonly.DERIVED
                if kind == "symbols"
                else _createonly.AUTHOR
            )


@pytest.mark.skipif(not shutil.which("cmake"), reason="cmake not available")
def test_the_mirrored_project_builds_and_its_c_tests_pass(mirrored, tmp_path):
    build = tmp_path / "build"
    for cmd in (
        ["cmake", "-S", str(mirrored), "-B", str(build), "-DBUILD_PYTHON=OFF"],
        ["cmake", "--build", str(build), "-j4"],
        ["ctest", "--test-dir", str(build), "--output-on-failure"],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True)
        assert r.returncode == 0, (cmd, r.stdout[-3000:], r.stderr[-3000:])
    # ctest's summary wording varies by release: 3.x prints "100% tests
    # passed, 0 tests failed out of 4", 4.x "100% tests passed out of 4".
    # What matters is all passed AND all four ran -- the count is what
    # proves the nested tests were found at all.
    summary = re.search(r"(\d+)% tests passed.*? out of (\d+)", r.stdout)
    assert summary and summary.groups() == ("100", "4"), r.stdout[-3000:]
    for name in _EXPECTED:
        assert (build / f"bench_{name}_core").exists() or any(
            build.rglob(f"bench_{name}_core")
        )


def test_remove_deletes_from_the_mirrored_place(tmp_path):
    root = _scaffold(tmp_path, "mirrored")
    _jm(root, "remove", "object", "gain", "--force")
    left = _native_files(root)
    assert not any("gain" in rel for rel in left), left
    assert not (root / INC.TESTS_DIR / "gain").exists()
    _jm(root, "status", "--check")


# ── byte-identity: the flat layout renders what it always has ─────────────

#: Pinned from origin/main 85ab6e69, a project made by `jm new p --object
#: gain`, then `jm module util` + `jm function clamp` and an object of
#: `[module."dsp.filters"]`. The regions gh-2423 moved into slots.
_FLAT_COMPONENT = """add_executable(test_gain_core
    ${CMAKE_SOURCE_DIR}/native/tests/test_gain_core.c
    ${CMAKE_SOURCE_DIR}/native/tests/test_gain_symbols.c)
target_link_libraries(test_gain_core PRIVATE
    gain_core
    ${JM_MATH_LIBRARY})
target_include_directories(test_gain_core
    PRIVATE ${CMAKE_SOURCE_DIR}/native/inc)
add_test(NAME test_gain_core COMMAND test_gain_core)

add_executable(bench_gain_core
    ${CMAKE_SOURCE_DIR}/native/benchmarks/bench_gain_core.c)
target_link_libraries(bench_gain_core PRIVATE
    gain_core
    ${JM_MATH_LIBRARY})
target_include_directories(bench_gain_core
    PRIVATE ${CMAKE_SOURCE_DIR}/native/inc
            ${CMAKE_SOURCE_DIR}/native/benchmarks)
"""
_FLAT_OBJECT_CORE = """add_executable(test_fir_core
               ${CMAKE_SOURCE_DIR}/native/tests/test_fir_core.c
               ${CMAKE_SOURCE_DIR}/native/tests/test_fir_symbols.c)
target_link_libraries(test_fir_core
                      PRIVATE fir_core ${JM_MATH_LIBRARY})
target_include_directories(test_fir_core
                           PRIVATE ${CMAKE_SOURCE_DIR}/native/inc)
add_test(NAME test_fir_core COMMAND test_fir_core)

add_executable(
  bench_fir_core
  ${CMAKE_SOURCE_DIR}/native/benchmarks/bench_fir_core.c)
target_link_libraries(bench_fir_core
                      PRIVATE fir_core ${JM_MATH_LIBRARY})
target_include_directories(
  bench_fir_core PRIVATE ${CMAKE_SOURCE_DIR}/native/inc
                                   ${CMAKE_SOURCE_DIR}/native/benchmarks)
"""
_FLAT_MODULE = """add_executable(test_util_core
    ${CMAKE_SOURCE_DIR}/native/tests/test_util_core.c)
target_link_libraries(test_util_core PRIVATE util_core ${JM_MATH_LIBRARY})
target_include_directories(test_util_core
    PRIVATE ${CMAKE_SOURCE_DIR}/native/inc)
add_test(NAME test_util_core COMMAND test_util_core)

add_executable(bench_util_core
    ${CMAKE_SOURCE_DIR}/native/benchmarks/bench_util_core.c)
target_link_libraries(bench_util_core PRIVATE util_core ${JM_MATH_LIBRARY})
target_include_directories(bench_util_core
    PRIVATE ${CMAKE_SOURCE_DIR}/native/inc
            ${CMAKE_SOURCE_DIR}/native/benchmarks)
"""
_FLAT_MAKE = (
    "test_gain_core: native/tests/test_gain_core.c"
    " native/tests/test_gain_symbols.c native/src/gain/gain_core.c\n"
    "\t$(CC) -O2 -std=c99 -Inative/inc $^ -o $@\n",
    "bench_gain_core: native/benchmarks/bench_gain_core.c"
    " native/src/gain/gain_core.c\n"
    "\t$(CC) -O2 -std=gnu99 -Inative/inc -Inative/benchmarks $^ -o $@ -lm\n",
)


@pytest.fixture(scope="module")
def flat(tmp_path_factory) -> Path:
    return _scaffold(tmp_path_factory.mktemp("flat"), "")


def test_flat_renders_what_it_always_has(flat):
    src = flat / "native" / "src"
    assert _FLAT_COMPONENT in (src / "gain" / "CMakeLists.txt").read_text(
        encoding="utf-8"
    )
    assert _FLAT_OBJECT_CORE in (src / "fir" / "CMakeLists.txt").read_text(
        encoding="utf-8"
    )
    assert _FLAT_MODULE in (src / "util" / "CMakeLists.txt").read_text(
        encoding="utf-8"
    )
    want = {INC.harness_rel(t) for t in INC.HARNESS} | {
        f"{tree}/{file.format(name)}"
        for name, kinds in _EXPECTED.items()
        for kind in kinds
        for tree, file in [INC.NATIVE_FILES[kind]]
    }
    assert _native_files(flat) == want
    assert "layout" not in (flat / "just-makeit.toml").read_text(
        encoding="utf-8"
    )


def test_flat_make_rules_render_what_they_always_have(tmp_path):
    _jm(tmp_path, "new", "p", "--build-system", "make")
    _jm(tmp_path / "p", "object", "gain")
    text = (tmp_path / "p" / "Makefile").read_text(encoding="utf-8")
    for rule in _FLAT_MAKE:
        assert rule in text


# ── switching an existing project ──────────────────────────────────────────


def test_switching_moves_the_files_and_back(tmp_path):
    root = _scaffold(tmp_path, "")
    mine = "/* the author's own check */\n"
    flat_test = root / INC.native_rel("test", "gain", root)
    flat_test.write_text(
        flat_test.read_text(encoding="utf-8") + mine, encoding="utf-8"
    )
    top = root / "CMakeLists.txt"
    hand = (
        "\nadd_executable(extra_gain\n"
        f"    ${{CMAKE_SOURCE_DIR}}/{INC.native_rel('test', 'gain', root)})\n"
    )
    top.write_text(top.read_text(encoding="utf-8") + hand, encoding="utf-8")

    _declare(root, "mirrored")
    # `status` names every file waiting to move before `apply` moves it.
    r = run_cli("status", cwd=root)
    assert "SUPERSEDED (11)" in r.stdout, r.stdout
    _jm(root, "apply")

    moved = root / INC.native_rel("test", "gain", root)
    assert moved != flat_test and moved.read_text(encoding="utf-8").endswith(
        mine
    )
    want = {INC.harness_rel(t) for t in INC.HARNESS} | {
        INC.native_rel(kind, name, root)
        for name, kinds in _EXPECTED.items()
        for kind in kinds
    }
    assert _native_files(root) == want  # no flat duplicate left or made
    old = flat_test.relative_to(root).as_posix()
    for cml in [top, *(root / "native" / "src").glob("*/CMakeLists.txt")]:
        text = cml.read_text(encoding="utf-8")
        assert f"/{old})" not in text and f"/{old}\n" not in text, cml
    assert f"${{CMAKE_SOURCE_DIR}}/{moved.relative_to(root).as_posix()})" in (
        top.read_text(encoding="utf-8")
    )
    top.write_text(
        top.read_text(encoding="utf-8").split("\nadd_executable(extra_gain")[0]
        + "\n",
        encoding="utf-8",
    )
    _jm(root, "status", "--check")

    _declare(root, "")
    _jm(root, "apply")
    assert flat_test.read_text(encoding="utf-8").endswith(mine)
    flat_want = {INC.harness_rel(t) for t in INC.HARNESS} | {
        INC.native_rel(kind, name, root)
        for name, kinds in _EXPECTED.items()
        for kind in kinds
    }
    assert _native_files(root) == flat_want
    assert (
        sorted(p.name for p in (root / INC.TESTS_DIR).iterdir() if p.is_dir())
        == []
    )
    _jm(root, "status", "--check")


def test_files_moved_by_hand_first_are_adopted(tmp_path):
    root = _scaffold(tmp_path, "")
    _declare(root, "mirrored")
    for old, new in INC.relocations(root):
        if (root / old).is_file():
            (root / new).parent.mkdir(parents=True, exist_ok=True)
            (root / old).rename(root / new)
    r = _jm(root, "apply")
    assert "renamed" not in r.stdout
    assert "create" not in r.stdout.replace("created", "")
    _jm(root, "status", "--check")


def test_upgrade_makes_the_same_moves(tmp_path):
    root = _scaffold(tmp_path, "")
    _declare(root, "mirrored")
    _jm(root, "upgrade")
    for old, new in INC.relocations(root):
        assert not (root / old).exists(), old
    _jm(root, "apply")
    _jm(root, "status", "--check")


def test_an_unknown_layout_is_refused_before_anything_is_written(tmp_path):
    root = tmp_path / "p"
    _jm(tmp_path, "new", "p")
    _declare(root, "nested")
    r = run_cli("object", "gain", cwd=root)
    assert r.returncode != 0
    assert "[project] layout 'nested'" in r.stderr, r.stderr
    assert not (root / INC.TESTS_DIR).exists()
    r = run_cli("apply", cwd=root)
    assert r.returncode != 0
    assert "[project] layout 'nested'" in r.stderr, r.stderr
