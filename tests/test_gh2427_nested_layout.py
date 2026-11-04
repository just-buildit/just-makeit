"""gh-2427: ``[project] layout = "nested"`` -- a component's header, and
every native file that mirrors it, follow its module.

doppler asked for the C header tree to be the layout of record:
``native/inc/<pkg>/<area>/<sub...>/<obj>/<obj>_core.h``, the Python package
being that path minus the object. gh-2423 made ``_incpath.core_dir`` the one
answer to "where does this component live in the C tree"; under ``nested``
it answers ``<module package>/<comp>``, the package read from
``_config.module_package_resolved``, and the header, its tests and benches,
every ``#include`` of it, the bridge and process-global headers beside it
and the CMake include directories all move with it. ``native/src`` does not
move here: that is gh-2459.

GATES:

1. Registration-free: no jm module but `_incpath` spells a header jm names
   after a component (``<x>/<x>_core.h``, a ``<<inc_prefix>>`` slot glued
   to a name, ``header_root(..) / name``, ``INC.path(root, name)``), and no
   template spells ``<<component>>/<<component>>_core.h`` (the AST, prose
   excluded).
2. Behaviour: a 3-level nested module with two objects, one ``depends_on``
   the other, plus a module function and a standalone component: every
   header lands under the module's path, every ``#include`` in the tree --
   the dependent object's include of its dependency among them -- resolves
   to a file through ``-Inative/inc``, ``status --check`` is clean, and the
   project builds (Python extension included) and passes ``ctest``.
3. An independent oracle: the nested tree IS the mirrored tree with each
   module member's directory moved under its module's package, by path and
   by every spelling of it, and nothing else.
4. Byte-identity: with the key absent, every core-header include and every
   per-component include directory is what jm rendered before this change
   (pinned from origin/main f1e0d8f8).
5. Switching: flat -> nested -> mirrored -> flat moves the author's files
   with their edits, an author header beside the core moves too and the
   includes naming it are respelled, `status --check` is clean at each
   step, and the round trip is byte-identical. `upgrade` makes the same
   moves. A module path under an object's own name moves no directory into
   itself.
6. `remove` leaves no empty module directory behind.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _composer
from just_makeit import _config as C
from just_makeit import _incpath as INC
from just_makeit import _procglobal

PKG = Path(__file__).resolve().parent.parent / "src" / "just_makeit"

#: The owner, and a GENERATED record of what released jms rendered.
_EXEMPT = {"_incpath.py", "_installhistory.py"}

_MODULE = "rx.ss.burst"
_MEMBERS = ("fir", "gain", "rx_ss_burst")  # two objects and the module core

# ── 1. registration-free: one spelling of a component's header ────────────


def _modules():
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG)
        if {"templates", "examples"} & set(rel.parts) or rel.name in _EXEMPT:
            continue
        yield rel, ast.parse(path.read_text(encoding="utf-8"), str(path))


def _prose(tree) -> "set[int]":
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }


def _shape(node) -> "str | None":
    """An f-string as text with each placeholder as NUL; a str as is."""
    if isinstance(node, ast.JoinedStr):
        return "".join(
            p.value if isinstance(p, ast.Constant) else "\x00"
            for p in node.values
        )
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


#: The kinds `_incpath.NAMED_HEADERS` names, read off the table.
_KINDS = "|".join(
    re.escape(f.replace("{}", "")) for f in INC.NAMED_HEADERS.values()
)
#: ``{x}/{x}_core.h`` and kin: a directory spelled as the bare name.
_HAND_DIR = re.compile(rf"\x00/\x00({_KINDS})")
#: ``<<inc_prefix>>`` glued to a name: the CMake include directory by hand.
_HAND_SLOT = re.compile(r"<<inc_prefix>>(\x00|<<(component|module)>>)")


def _is_incpath_call(node, name: str) -> bool:
    f = getattr(node, "func", None)
    return (
        isinstance(node, ast.Call)
        and isinstance(f, ast.Attribute)
        and f.attr == name
        and isinstance(f.value, ast.Name)
        and f.value.id == "INC"
    )


def _findings(tree, rel) -> "list[str]":
    prose = _prose(tree)
    hits = []
    for node in ast.walk(tree):
        if id(node) in prose:
            continue
        text = _shape(node)
        if text is not None and (
            _HAND_DIR.search(text) or _HAND_SLOT.search(text)
        ):
            hits.append(f"{rel}:{node.lineno}  {text[:70]!r}")
        # `header_root(...) / name`: a component's directory by hand.
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Div)
            and _is_incpath_call(node.left, "header_root")
            and isinstance(node.right, ast.Name)
        ):
            hits.append(f"{rel}:{node.lineno}  header_root(..) / name")
        # `INC.path(root, name)`: the same, one call deep.
        if (
            _is_incpath_call(node, "path")
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Name)
        ):
            hits.append(f"{rel}:{node.lineno}  INC.path(root, name)")
    return hits


#: A template spelling a component's header by its name.
_TEMPLATE_HAND = re.compile(
    r"<<(component|module)>>[*/]*<<\1>>[*/]*_core\.h"
    r"|<<inc_prefix>>[*/]*<<(component|module)>>"
)


def _template_findings() -> "list[str]":
    hits = []
    for path in sorted((PKG / "templates").rglob("*")):
        if not path.is_file():
            continue
        for n, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if "#include" in line and _TEMPLATE_HAND.search(line):
                hits.append(f"{path.relative_to(PKG)}:{n}  {line.strip()}")
    return hits


def test_only_incpath_spells_a_component_header_path():
    hits = []
    for rel, tree in _modules():
        hits += _findings(tree, rel)
    hits += _template_findings()
    assert hits == [], (
        "spell a component's header, or its directory, through `_incpath` "
        "(INC.core_include, INC.core_h, INC.core_dir, INC.named_header, "
        "the <<core_include>> / <<core_dir>> slots):\n" + "\n".join(hits)
    )


def test_the_gate_sees_what_it_guards():
    """Feed the gate each shape it refuses and require a finding for each."""
    src = (
        "a = f'{c}/{c}_core.h'\n"
        "b = f'{m}/{m}_bridge.h'\n"
        "c = f'{inc}/<<inc_prefix>>{component})'\n"
        "d = INC.header_root(proj) / comp\n"
        "e = INC.path(root, obj)\n"
        "f = f'{x}/{x}_procglobal.h'\n"
    )
    assert len(_findings(ast.parse(src), "probe")) == 6
    assert _TEMPLATE_HAND.search(
        '#include "/*<<inc_prefix>>*//*<<component>>*///*<<component>>*/'
        '_core.h"'
    )
    assert len(list(_modules())) > 50
    uses = sum(
        p.read_text(encoding="utf-8").count("INC.core_")
        for p in PKG.glob("*.py")
    )
    assert uses >= 30, uses


def test_core_dir_reads_the_resolved_package():
    """The module's path comes from `module_package_resolved`, the one answer
    to where a module's Python lives -- a declared ``package`` wins."""
    cfg = {
        "project": {"name": "p", "layout": "nested"},
        "module": {
            _MODULE: {"objects": ["fir"]},
            "pkgd": {"objects": ["amp"], "package": "radio/front"},
        },
        "fir": {},
        "amp": {},
        "solo": {},
    }
    for name, module in (("fir", _MODULE), ("amp", "pkgd")):
        assert INC.core_dir(name, cfg) == (
            f"{C.module_package_resolved(cfg, module)}/{name}"
        )
    assert INC.core_dir("solo", cfg) == "solo"
    assert INC.core_dir("rx_ss_burst", cfg) == "rx/ss/burst/rx_ss_burst"
    # The headers named beside a core follow it.
    assert _composer.bridge_h(_MODULE, cfg) == (
        "rx/ss/burst/rx_ss_burst/rx_ss_burst_bridge.h"
    )
    assert _procglobal.header_name("fir", cfg) == (
        "rx/ss/burst/fir/fir_procglobal.h"
    )
    for value in ("flat", "mirrored"):
        flat = {**cfg, "project": {"name": "p", "layout": value}}
        assert INC.core_dir("fir", flat) == "fir"


# ── 2. behaviour: a nested project, end to end ──────────────────────────────


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
    _jm(root, "object", "amp")
    _jm(root, "module", _MODULE)
    _jm(root, "object", "fir", "--module", _MODULE)
    _jm(root, "object", "gain", "--module", _MODULE)
    _jm(
        root,
        "function",
        "clip",
        "--module",
        _MODULE,
        "--param",
        "x:double",
        "--return-type",
        "double",
    )
    frag = root / "objects" / "gain.toml"
    frag.write_text(
        frag.read_text(encoding="utf-8").replace(
            "[gain]\n", '[gain]\ndepends_on = ["fir"]\n', 1
        ),
        encoding="utf-8",
    )
    _jm(root, "apply")
    return root


@pytest.fixture(scope="module")
def nested(tmp_path_factory) -> Path:
    return _scaffold(tmp_path_factory.mktemp("nested"), "nested")


_INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', re.M)


def _c_files(root: Path):
    return sorted(
        p
        for p in (root / "native").rglob("*")
        if p.is_file() and p.suffix in (".c", ".h")
    )


def test_every_header_lands_under_its_module(nested):
    cfg = C.load(nested)
    package = C.module_package_resolved(cfg, _MODULE)
    assert package == "rx/ss/burst"
    hroot = INC.header_root(nested)
    for name in _MEMBERS:
        # Independently of the primitive: the package path, then the name.
        h = hroot / package / name / f"{name}_core.h"
        assert h.is_file(), h
        assert INC.core_h(nested, name) == h
        assert not (hroot / name).exists(), name
        for tree in INC.HARNESS:
            assert (nested / tree / package / name).is_dir(), (tree, name)
    # A standalone component has no module to follow.
    assert (hroot / "amp" / "amp_core.h").is_file()


def test_every_include_in_the_tree_resolves(nested):
    """Registration-free over the tree: an include of a project header that
    no longer resolves is a file left behind or a spelling left unmoved."""
    inc = INC.inc_dir(nested)
    pkg = C.project_name(C.load(nested))
    unresolved = []
    seen = 0
    for f in _c_files(nested):
        for target in _INCLUDE.findall(f.read_text(encoding="utf-8")):
            if not target.startswith(f"{pkg}/"):
                continue
            seen += 1
            if (
                not (inc / target).is_file()
                and not (f.parent / target).is_file()
            ):
                unresolved.append(f"{f.relative_to(nested)}: {target}")
    assert unresolved == []
    assert seen >= 30, seen


def test_the_dependent_object_includes_its_dependency_through_core_dir(nested):
    gain_h = INC.core_h(nested, "gain").read_text(encoding="utf-8")
    want = f'#include "{INC.core_include("fir", nested)}"'
    assert want == '#include "p/rx/ss/burst/fir/fir_core.h"'
    assert want in gain_h


def test_status_is_clean(nested):
    _jm(nested, "status", "--check")


@pytest.mark.skipif(not shutil.which("cmake"), reason="cmake not available")
def test_the_nested_project_builds_and_imports(nested, tmp_path):
    # A copy: the build writes the extensions into `src/`, and the oracle
    # below compares the shared fixture's tree file by file.
    nested = Path(shutil.copytree(nested, tmp_path / "p"))
    build = tmp_path / "build"
    for cmd in (
        [
            "cmake",
            "-S",
            str(nested),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        ["cmake", "--build", str(build), "-j4"],
        ["ctest", "--test-dir", str(build), "--output-on-failure"],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        assert r.returncode == 0, (cmd, r.stdout[-3000:], r.stderr[-3000:])
    # amp, fir, gain and the module's own function core.
    summary = re.search(r"(\d+)% tests passed.*? out of (\d+)", r.stdout)
    assert summary and summary.groups() == ("100", "4"), r.stdout[-3000:]
    script = (
        "from p.rx.ss.burst import Fir, Gain, clip\n"
        "from p import Amp\n"
        "Fir(); Gain(); Amp(); clip(1.0)\n"
        "print('OK')\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(nested / "src")},
        cwd=str(tmp_path),
        timeout=120,
    )
    assert r.returncode == 0 and "OK" in r.stdout, (r.stdout, r.stderr)


# ── 3. the oracle: nested is mirrored with the module's directories moved ───


@pytest.fixture(scope="module")
def mirrored(tmp_path_factory) -> Path:
    return _scaffold(tmp_path_factory.mktemp("mirrored"), "mirrored")


def _nest(text: str, package: str) -> str:
    """*text* with every spelling of a member's directory -- the header's,
    each mirror's, the include's -- moved under *package*."""
    for name in _MEMBERS:
        for lead in ("native/inc/p/", "native/tests/", "native/benchmarks/"):
            text = re.sub(
                rf"(?<!\w){re.escape(lead + name)}(?=[/)])",
                f"{lead}{package}/{name}",
                text,
            )
        text = text.replace(f'"p/{name}/', f'"p/{package}/{name}/')
    return text


def _tree(root: Path) -> "dict[str, bytes]":
    skip = {"build", ".git", "dist", "__pycache__"}
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not skip & set(p.relative_to(root).parts)
    }


def test_nested_is_mirrored_with_the_module_directories_moved(
    mirrored, nested
):
    package = C.module_package_resolved(C.load(nested), _MODULE)
    want = {
        _nest(rel, package): _nest(
            data.decode("utf-8", "surrogateescape"), package
        ).replace('layout = "mirrored"', 'layout = "nested"')
        for rel, data in _tree(mirrored).items()
    }
    got = {
        rel: data.decode("utf-8", "surrogateescape")
        for rel, data in _tree(nested).items()
    }
    assert sorted(got) == sorted(want)
    differ = [rel for rel in got if got[rel] != want[rel]]
    assert differ == [], [
        (rel, [ln for ln in got[rel].splitlines() if ln not in want[rel]])
        for rel in differ
    ]


# ── 4. byte-identity: the flat layout renders what it always has ───────────

#: Pinned from origin/main f1e0d8f8, the project `_scaffold` makes with no
#: ``layout``: every include of a component's (or the module's) header.
_FLAT_CORE_INCLUDES = """\
native/benchmarks/bench_amp_core.c #include "p/amp/amp_core.h"
native/benchmarks/bench_fir_core.c #include "p/fir/fir_core.h"
native/benchmarks/bench_gain_core.c #include "p/gain/gain_core.h"
native/benchmarks/bench_rx_ss_burst_core.c #include "p/rx_ss_burst/rx_ss_burst_core.h"
native/inc/p/gain/gain_core.h #include "p/fir/fir_core.h"
native/inc/p/p.h #include "p/amp/amp_core.h"
native/inc/p/p.h #include "p/fir/fir_core.h"
native/inc/p/p.h #include "p/gain/gain_core.h"
native/src/amp/amp_core.c #include "p/amp/amp_core.h"
native/src/amp/amp_ext.c #include "p/amp/amp_core.h"
native/src/fir/fir_core.c #include "p/fir/fir_core.h"
native/src/gain/gain_core.c #include "p/gain/gain_core.h"
native/src/rx_ss_burst/clip.c #include "p/rx_ss_burst/rx_ss_burst_core.h"
native/src/rx_ss_burst/rx_ss_burst_core.c #include "p/rx_ss_burst/rx_ss_burst_core.h"
native/src/rx_ss_burst/rx_ss_burst_ext.c #include "p/rx_ss_burst/rx_ss_burst_core.h"
native/src/rx_ss_burst/rx_ss_burst_ext_fir.c #include "p/fir/fir_core.h"
native/src/rx_ss_burst/rx_ss_burst_ext_gain.c #include "p/gain/gain_core.h"
native/tests/test_amp_core.c #include "p/amp/amp_core.h"
native/tests/test_amp_symbols.c #include "p/amp/amp_core.h"
native/tests/test_fir_core.c #include "p/fir/fir_core.h"
native/tests/test_fir_symbols.c #include "p/fir/fir_core.h"
native/tests/test_gain_core.c #include "p/gain/gain_core.h"
native/tests/test_gain_symbols.c #include "p/gain/gain_core.h"
native/tests/test_rx_ss_burst_core.c #include "p/rx_ss_burst/rx_ss_burst_core.h"
"""

#: ...and the per-component include directories, the other region gh-2427
#: moved into a slot.
_FLAT_INC_DIRS = {
    "${CMAKE_SOURCE_DIR}/native/inc/p/amp)",
    "${CMAKE_SOURCE_DIR}/native/inc/p/fir)",
    "${CMAKE_SOURCE_DIR}/native/inc/p/gain)",
}


@pytest.fixture(scope="module")
def flat(tmp_path_factory) -> Path:
    return _scaffold(tmp_path_factory.mktemp("flat"), "")


def test_flat_renders_what_it_always_has(flat):
    got = sorted(
        f'{f.relative_to(flat).as_posix()} #include "{t}"'
        for f in _c_files(flat)
        for t in _INCLUDE.findall(f.read_text(encoding="utf-8"))
        if t.endswith("_core.h")
    )
    assert "\n".join(got) + "\n" == _FLAT_CORE_INCLUDES
    dirs = {
        line.strip().split()[-1]
        for p in (flat / "native" / "src").glob("*/CMakeLists.txt")
        for line in p.read_text(encoding="utf-8").splitlines()
        if "native/inc/p/" in line
    }
    assert dirs == _FLAT_INC_DIRS
    assert "layout" not in (flat / "just-makeit.toml").read_text(
        encoding="utf-8"
    )


# ── 5. switching an existing project ───────────────────────────────────────

_MINE_H = "#define FIR_TAPS 4\n"
_MINE_TEST = "/* the author's own check */\n"


def _digest(root: Path) -> "dict[str, str]":
    return {
        rel: hashlib.sha256(data).hexdigest()
        for rel, data in _tree(root).items()
    }


def _author_edits(root: Path) -> None:
    """An author header beside fir's core, included by the sacred core.c
    and by gain's, and an edit to fir's C test."""
    (INC.core_h(root, "fir").parent / "fir_taps.h").write_text(
        _MINE_H, encoding="utf-8"
    )
    spelled = INC.include(f"{INC.core_dir('fir', root)}/fir_taps.h", root)
    for comp in ("fir", "gain"):
        core = root / "native" / "src" / comp / f"{comp}_core.c"
        core.write_text(
            f'#include "{spelled}"\n' + core.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    test = INC.native_path(root, "test", "fir")
    test.write_text(
        test.read_text(encoding="utf-8") + _MINE_TEST, encoding="utf-8"
    )


def _assert_settled(root: Path) -> None:
    taps = INC.core_h(root, "fir").parent / "fir_taps.h"
    assert taps.read_text(encoding="utf-8") == _MINE_H
    spelled = INC.include(f"{INC.core_dir('fir', root)}/fir_taps.h", root)
    for comp in ("fir", "gain"):
        core = (root / "native" / "src" / comp / f"{comp}_core.c").read_text(
            encoding="utf-8"
        )
        assert core.startswith(f'#include "{spelled}"\n'), comp
        assert f'#include "{INC.core_include(comp, root)}"' in core
    assert (
        INC.native_path(root, "test", "fir")
        .read_text(encoding="utf-8")
        .endswith(_MINE_TEST)
    )
    # No file left in another layout's place, and no directory emptied.
    for other in INC.relocations(root):
        assert not (root / other[0]).exists(), other
    empty = [
        p.relative_to(root).as_posix()
        for p in (root / "native").rglob("*")
        if p.is_dir() and not any(p.iterdir())
    ]
    assert empty == []
    _jm(root, "status", "--check")


def test_switching_moves_headers_and_mirrors_every_way(tmp_path):
    root = _scaffold(tmp_path, "")
    _author_edits(root)
    before = _digest(root)

    _declare(root, "nested")
    r = run_cli("status", cwd=root)
    old_h = INC.rel("fir/fir_core.h", root)
    assert old_h in r.stdout.split("SUPERSEDED", 1)[-1], r.stdout
    _jm(root, "apply")
    assert INC.core_dir("fir", root) == "rx/ss/burst/fir"
    _assert_settled(root)
    if shutil.which("cmake"):
        build = tmp_path / "build"
        for cmd in (
            ["cmake", "-S", str(root), "-B", str(build), "-DBUILD_PYTHON=OFF"],
            ["cmake", "--build", str(build), "-j4"],
            ["ctest", "--test-dir", str(build)],
        ):
            r = subprocess.run(cmd, capture_output=True, text=True)
            assert r.returncode == 0, (cmd, r.stdout[-3000:], r.stderr[-3000:])

    _declare(root, "mirrored")
    _jm(root, "apply")
    assert INC.core_dir("fir", root) == "fir"
    _assert_settled(root)

    _declare(root, "")
    _jm(root, "apply")
    _assert_settled(root)
    after = _digest(root)
    assert {k for k in after if after.get(k) != before.get(k)} | {
        k for k in before if k not in after
    } == set()


def test_upgrade_makes_the_same_moves(tmp_path):
    root = _scaffold(tmp_path, "")
    _author_edits(root)
    _declare(root, "nested")
    _jm(root, "upgrade")
    # Moved, and the includes naming them respelled, by `upgrade` alone.
    assert INC.core_dir("fir", root) == "rx/ss/burst/fir"
    for old, _new in INC.relocations(root):
        assert not (root / old).exists(), old
    core = (root / "native" / "src" / "fir" / "fir_core.c").read_text(
        encoding="utf-8"
    )
    assert f'#include "{INC.core_include("fir", root)}"' in core
    _jm(root, "apply")
    _assert_settled(root)


def test_a_module_under_an_objects_own_name_moves_no_dir_into_itself(
    tmp_path,
):
    """``fir`` of ``[module."fir.sub"]``: its flat directory is an ancestor
    of its nested one. Only the headers jm names move; the author's own
    header stays where its include already finds it."""
    _jm(tmp_path, "new", "p")
    root = tmp_path / "p"
    _jm(root, "module", "fir.sub")
    _jm(root, "object", "fir", "--module", "fir.sub")
    mine = INC.core_h(root, "fir").parent / "mine.h"
    mine.write_text(_MINE_H, encoding="utf-8")
    before = _digest(root)
    _declare(root, "nested")
    _jm(root, "apply")
    assert INC.core_h(root, "fir").is_file()
    assert INC.core_dir("fir", root) == "fir/sub/fir"
    assert mine.read_text(encoding="utf-8") == _MINE_H
    _jm(root, "status", "--check")
    _declare(root, "")
    _jm(root, "apply")
    after = _digest(root)
    assert {k for k in after if after.get(k) != before.get(k)} == set()
    assert set(before) == set(after)


# ── 6. remove ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("layout", ["nested", ""])
def test_remove_leaves_nothing_of_the_module_behind(tmp_path, layout):
    """In every layout: and a function-bearing module's own C test and
    bench go with it, which no layout removed before (they included the
    header the removal had just deleted)."""
    root = _scaffold(tmp_path, layout)
    _jm(root, "remove", "object", "gain", "--force")
    assert not INC.core_h(root, "gain").exists()
    assert not INC.native_path(root, "test", "gain").exists()
    _jm(root, "status", "--check")
    _jm(root, "remove", "module", _MODULE, "--force")
    left = [
        p.relative_to(root).as_posix()
        for tree in (*INC.HARNESS, INC.header_root(root).relative_to(root))
        for p in (root / tree).rglob("*")
        if "rx" in p.relative_to(root / tree).parts[0]
    ]
    assert left == []
    empty = [
        p.relative_to(root).as_posix()
        for p in (root / "native").rglob("*")
        if p.is_dir() and not any(p.iterdir())
    ]
    assert empty == []
    _jm(root, "status", "--check")
