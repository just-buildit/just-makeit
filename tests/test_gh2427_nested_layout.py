"""gh-2427: ``[project] layout = "nested"`` -- a component's header, and
every native file that mirrors it, follow its module.

doppler asked for the C header tree to be the layout of record:
``native/inc/<pkg>/<area>/<sub...>/<obj>/<obj>_core.h``, the Python package
being that path minus the object. gh-2423 made ``_incpath.core_dir`` the one
answer to "where does this component live in the C tree"; under ``nested``
it answers ``<module package>/<comp>``, the package read from
``_config.module_package_resolved``, and the header, its tests and benches,
every ``#include`` of it, the bridge and process-global headers beside it
and the CMake include directories all move with it. gh-2459 moves its C
source directory too (``tests/test_gh2459_nested_src.py``).

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
6. `remove` leaves no empty module directory behind, and never takes a
   component whose directory sits below the removed one's (a module object
   or a standalone object above another module's members), author edits
   intact.
7. A composer's bridge header lands beside its core under nested, fresh and
   switched, and builds clean -- and no function holding the manifest asks
   the disk where a component's file goes (apply's replay records no
   composer module).
8. Changing a module's `package` under nested moves its headers, tests and
   benches, both ways, as a layout switch does.
9. `jm module --help` says the headers follow `--package` under nested.
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


_PCT = re.compile(r"%[-#0 +]*\d*(?:\.\d+)?[sdrai]")
_FMT = re.compile(r"\{[^{}]*\}")


def _known(node, known: "dict[str, str]") -> "str | None":
    """The value of *node* when it names one of *known*'s `_incpath`
    constants (``INC.SRC_DIR``, a bare ``SRC_DIR``), or is ``os.sep``
    (read as ``/``), else None."""
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
        and node.attr == "sep"
    ):
        return "/"
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in ("INC", "_incpath")
    ):
        return known.get(node.attr)
    if isinstance(node, ast.Name):
        return known.get(node.id)
    return None


def _shape(node, known: "dict[str, str] | None" = None) -> "str | None":
    """A string BUILT from names, as text with each name as NUL: an
    f-string, a ``+`` concatenation, a ``%`` format, a ``.format`` call or
    a ``sep.join([...])`` on a literal; a plain literal as is. ``None`` for
    anything else.

    gh-2459: *known* maps `_incpath` constants to their values, which are
    read as the literal they stand for, so ``f"{INC.SRC_DIR}/{x}"`` is
    the same spelling as ``f"native/src/{x}"``.
    """
    known = known or {}

    def part(p) -> str:
        if isinstance(p, ast.Constant) and isinstance(p.value, str):
            return p.value
        if isinstance(p, ast.FormattedValue):
            p = p.value
        got = _known(p, known)
        if got is not None:
            return got
        inner = _shape(p, known)
        return inner if inner is not None else "\x00"

    if isinstance(node, ast.JoinedStr):
        return "".join(part(p) for p in node.values)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if _known(node, known) is not None:
        return _known(node, known)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
        and (
            isinstance(node.func.value, ast.Constant)
            and isinstance(node.func.value.value, str)
            or _known(node.func.value, known) is not None
        )
        and len(node.args) == 1
        and isinstance(node.args[0], (ast.List, ast.Tuple))
    ):
        sep = (
            node.func.value.value
            if isinstance(node.func.value, ast.Constant)
            else _known(node.func.value, known)
        )
        return sep.join(part(e) for e in node.args[0].elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _shape(node.left, known), _shape(node.right, known)
        if left is None and right is None:
            return None
        return (left if left is not None else "\x00") + (
            right if right is not None else "\x00"
        )
    if (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Mod)
        and isinstance(node.left, ast.Constant)
        and isinstance(node.left.value, str)
    ):
        return _PCT.sub("\x00", node.left.value)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
        and isinstance(node.func.value, ast.Constant)
        and isinstance(node.func.value.value, str)
    ):
        return _FMT.sub("\x00", node.func.value.value)
    return None


#: The kinds `_incpath.NAMED_HEADERS` names, read off the table.
_KINDS = "|".join(
    re.escape(f.replace("{}", "")) for f in INC.NAMED_HEADERS.values()
)
#: A path to a named header built from a name: ``{x}/{x}_core.h``,
#: ``{d}/{x}_bridge.h``. A bare ``{x}_core.h`` is a label in a message.
_HAND_DIR = re.compile(rf"/[^\s]*\x00({_KINDS})$|\x00/\x00({_KINDS})")
#: ``<<inc_prefix>>`` glued to a name: the CMake include directory by hand.
_HAND_SLOT = re.compile(r"<<inc_prefix>>(\x00|<<(component|module)>>)")

#: The `_incpath` calls whose result is a header directory: joining a name
#: onto one spells a component's directory by hand (`INC.resolve` is the
#: one join for an include spelling).
_ROOTS = {"header_root", "inc_dir"}
#: The `_incpath` calls taking a header-relative name second.
_NAMERS = {"path"}


def _incpath_names(tree) -> "dict[str, str]":
    """``{local name: _incpath attribute}`` for a ``from ._incpath import``."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(
            "_incpath"
        ):
            for alias in node.names:
                out[alias.asname or alias.name] = alias.name
    return out


def _incpath_call(node, names: "dict[str, str]") -> "str | None":
    """The `_incpath` function *node* calls, by any spelling, else None."""
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        if f.value.id in ("INC", "_incpath"):
            return f.attr
    if isinstance(f, ast.Name) and f.id in names:
        return names[f.id]
    return None


def _div_chain(node) -> "list":
    """``a / b / c`` as ``[a, b, c]``."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _div_chain(node.left) + [node.right]
    return [node]


def _is_literal(node) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _findings(tree, rel) -> "list[str]":
    prose = _prose(tree)
    names = _incpath_names(tree)
    # Names bound to a header directory, per enclosing function (or the
    # module): a name means what its own scope assigned it.
    scope_of: "dict[int, int]" = {}

    def mark(node, scope):
        for child in ast.iter_child_nodes(node):
            inner = (
                child
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                else scope
            )
            scope_of[id(child)] = id(inner)
            mark(child, inner)

    mark(tree, tree)
    bound: "dict[int, set[str]]" = {}
    for n in ast.walk(tree):
        if (
            isinstance(n, (ast.Assign, ast.AnnAssign))
            and n.value is not None
            and _incpath_call(n.value, names) in _ROOTS
        ):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    bound.setdefault(scope_of.get(id(n), id(tree)), set()).add(
                        t.id
                    )
    hits = []
    seen_div: "set[int]" = set()
    for node in ast.walk(tree):
        if id(node) in prose:
            continue
        text = _shape(node)
        if text is not None and (
            _HAND_DIR.search(text) or _HAND_SLOT.search(text)
        ):
            hits.append(f"{rel}:{node.lineno}  {text[:70]!r}")
        # `<header dir> / name ...`: a component's directory by hand, the
        # header directory called or bound to a name first.
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Div)
            and id(node) not in seen_div
        ):
            chain = _div_chain(node)
            for sub in ast.walk(node):
                seen_div.add(id(sub))
            base, rest = chain[0], chain[1:]
            from_root = _incpath_call(base, names) in _ROOTS or (
                isinstance(base, ast.Name)
                and base.id
                in bound.get(scope_of.get(id(node), id(tree)), set())
            )
            if from_root and not all(_is_literal(r) for r in rest):
                hits.append(f"{rel}:{node.lineno}  <header dir> / name")
            literals = [r.value for r in rest if _is_literal(r)]
            if any(
                a == "native" and b == "inc"
                for a, b in zip(literals, literals[1:])
            ) and not all(_is_literal(r) for r in rest):
                hits.append(f"{rel}:{node.lineno}  ... / 'native' / 'inc' / x")
        # `<header dir>.joinpath(name, ...)`: the same join, spelled as a
        # method.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "joinpath"
        ):
            base = node.func.value
            if (
                _incpath_call(base, names) in _ROOTS
                or (
                    isinstance(base, ast.Name)
                    and base.id
                    in bound.get(scope_of.get(id(node), id(tree)), set())
                )
            ) and not all(_is_literal(a) for a in node.args):
                hits.append(
                    f"{rel}:{node.lineno}  <header dir>.joinpath(name)"
                )
        # `INC.path(root, name)`: the same, one call deep. A name that is
        # itself a call (`INC.core_dir(...)`, `bridge_h(...)`) or a file
        # (`f"{pkg}.h"`) is not a hand-spelled directory.
        if _incpath_call(node, names) in _NAMERS and len(node.args) >= 2:
            arg = node.args[1]
            shape = _shape(arg)
            if isinstance(arg, (ast.Name, ast.Attribute, ast.Subscript)) or (
                shape is not None and "\x00" in shape and ".h" not in shape
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


#: Every shape the gate refuses, one per line: each must be a finding.
_PROBE = """\
a = f'{c}/{c}_core.h'
b = f'{m}/{m}_bridge.h'
c = f'{inc}/<<inc_prefix>>{component})'
d = INC.header_root(proj) / comp
e = INC.path(root, obj)
f = f'{x}/{x}_procglobal.h'
g = INC.path(root, f'{comp}')
h = INC.path(root, self.comp)
i = INC.inc_dir(root) / comp
j = comp + '/' + comp + '_core.h'
k = '%s/%s_core.h' % (comp, comp)
l = '{}/{}_core.h'.format(comp, comp)
m = root / 'native' / 'inc' / pkg / comp
def stray(root, cfg, comp):
    hroot = INC.header_root(root, cfg)
    return hroot / comp / f"{comp}_core.h"
def annotated(root, comp):
    hr: Path = INC.header_root(root)
    return hr / comp
o = INC.header_root(root).joinpath(comp)
"""

#: ...and what it must let through: a literal file, an include spelling
#: resolved by `INC.resolve`, a label naming a file, a directory from a call.
_ALLOWED = """\
a = INC.header_root(root) / "jm_perf.h"
b = INC.resolve(root, h)
c = f"remove the body from {cname}_core.h"
d = INC.path(root, f"{pkg}.h")
e = INC.path(root, INC.core_dir(obj, cfg))
"""


def test_the_gate_sees_what_it_guards():
    """Feed the gate each shape it refuses and require a finding for each,
    and each shape it allows and require none."""
    lines = [ln for ln in _PROBE.splitlines() if not ln.startswith(" ")]
    src = _PROBE + "from ._incpath import path\nn = path(root, comp)\n"
    found = _findings(ast.parse(src), "probe")
    # One finding per top-level statement (the `def` counts once), and one
    # for the bare `path` imported from `_incpath`.
    assert len(found) == len(lines) + 1, found
    assert {f.split()[0] for f in found} == {
        f"probe:{n}" for n in (*range(1, 14), 16, 19, 20, 22)
    }, found
    assert _findings(ast.parse(_ALLOWED), "probe") == []
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
        for lead in (
            "native/inc/p/",
            "native/tests/",
            "native/benchmarks/",
            # gh-2459: and the C source directory.
            "native/src/",
        ):
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
        core = INC.src_path(root, comp) / f"{comp}_core.c"
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
        core = (INC.src_path(root, comp) / f"{comp}_core.c").read_text(
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
    r = _jm(root, "apply")
    # A move is reported as one: renamed, then `update` where its includes
    # were respelled -- never `create` (the file existed before the run).
    assert "Materialized 0 new file(s)" in r.stdout, r.stdout
    assert not [
        ln for ln in r.stdout.splitlines() if ln.startswith("  create")
    ]
    assert f"renamed {old_h} -> " in r.stdout
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
    core = (INC.src_path(root, "fir") / "fir_core.c").read_text(
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


@pytest.mark.parametrize(
    "build",
    [
        # [module.radio] package "front" puts fir at front/fir/; a module
        # packaged "front/fir" puts tap at front/fir/tap/, below it.
        (
            (
                ("module", "radio", "--package", "front"),
                ("object", "fir", "--module", "radio"),
                ("module", "taps", "--package", "front/fir"),
                ("object", "tap", "--module", "taps"),
            ),
            "fir",
            "tap",
        ),
        # A standalone `rx` sits above every member of [module."rx.ss"].
        (
            (
                ("object", "rx"),
                ("module", "rx.ss"),
                ("object", "tap", "--module", "rx.ss"),
            ),
            "rx",
            "tap",
        ),
    ],
    ids=["module-object", "standalone"],
)
def test_remove_keeps_a_component_nested_below_the_removed_one(
    tmp_path, build
):
    """B1: under nested one component's directory may be an ancestor of
    another's, and removing the outer one deleted the inner header whole --
    the author's edits with it. Only the removed component's own part goes."""
    steps, gone, kept = build
    _jm(tmp_path, "new", "p")
    root = tmp_path / "p"
    _declare(root, "nested")
    for step in steps:
        _jm(root, *step)
    _jm(root, "apply")
    inner = INC.core_h(root, kept)
    assert INC.core_h(root, gone).parent in inner.parents
    mine = "/* AUTHOR */\n"
    inner.write_text(
        inner.read_text(encoding="utf-8") + mine, encoding="utf-8"
    )
    survivors = {
        p: p.read_bytes()
        for p in INC.header_root(root).rglob("*_core.h")
        if INC.core_h(root, gone).parent != p.parent and p.parent.name != gone
    }
    _jm(root, "remove", "object", gone, "--force")
    assert not INC.core_h(root, gone).exists()
    assert inner.read_text(encoding="utf-8").endswith(mine)
    for path, data in survivors.items():
        assert path.read_bytes() == data, path
    _jm(root, "status", "--check")


# ── B2: a composer's bridge header under nested ─────────────────────────────

_SEAMS = PKG / "examples" / "composer_seams" / ".steps"


def _composer_project(where: Path, layout: str) -> Path:
    """The composer_seams example's project, in *layout*: the backing header
    and every include of a header jm places spelled through `_incpath`."""
    _jm(where, "new", "studio")
    root = where / "studio"
    _declare(root, layout)
    _jm(
        root,
        "object",
        "clip",
        "--state",
        "level:double:0.0",
        "--arg-type",
        "void",
        "--return-type",
        "float _Complex",
    )
    backing = root / "native" / "src" / "backing"
    backing.mkdir(parents=True)
    playlist_h = INC.core_h(root, "playlist", _with_composer(root))
    playlist_h.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_SEAMS / "02_playlist_core.h", playlist_h)
    old = '"studio/playlist/playlist_core.h"'
    (backing / "playlist_core.c").write_text(
        (_SEAMS / "02_playlist_core.c")
        .read_text(encoding="utf-8")
        .replace(
            old,
            f'"{INC.core_include("playlist", _with_composer(root))}"',
        ),
        encoding="utf-8",
    )
    shutil.copy(_SEAMS / "02_CMakeLists.txt", backing / "CMakeLists.txt")
    r = subprocess.run(
        [sys.executable, str(_SEAMS / "03_manifest.py")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    _jm(root, "apply")
    bridge = INC.include(_composer.bridge_h("playlist", root), root)
    (backing / "playlist_bridge.c").write_text(
        (_SEAMS / "05_playlist_bridge.c")
        .read_text(encoding="utf-8")
        .replace('"studio/playlist/playlist_bridge.h"', f'"{bridge}"'),
        encoding="utf-8",
    )
    # The hand-written method the example's manifest registers.
    shutil.copy(
        _SEAMS / "05b_playlist_ext_extra.c",
        INC.src_path(root, "playlist") / "playlist_ext_extra.c",
    )
    # The example's c_deps core is folded into no library (`status` says
    # UNWIRED, as the example's own tree does); wire it as status advises.
    top = root / "CMakeLists.txt"
    top.write_text(
        top.read_text(encoding="utf-8")
        + "\ntarget_sources(studio_lib PRIVATE $<TARGET_OBJECTS:backing_core>)"
        + "\ntarget_sources(studio_lib_static PRIVATE"
        + " $<TARGET_OBJECTS:backing_core>)\n",
        encoding="utf-8",
    )
    return root


def _with_composer(root: Path) -> dict:
    """*root*'s manifest as it reads once the composer module is declared:
    the backing header is written before the manifest names the module."""
    cfg = C.load(root)
    return {**cfg, "module": {**cfg.get("module", {}), "playlist": {}}}


def _builds(root: Path, tmp_path: Path) -> None:
    if not shutil.which("cmake"):
        return
    build = tmp_path / "build"
    for cmd in (
        [
            "cmake",
            "-S",
            str(root),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        ["cmake", "--build", str(build), "-j4"],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        assert r.returncode == 0, (cmd, r.stdout[-3000:], r.stderr[-3000:])
    shutil.rmtree(build)


@pytest.mark.parametrize("start", ["nested", ""], ids=["fresh", "switched"])
def test_a_composer_bridge_lands_beside_its_core_under_nested(tmp_path, start):
    """B2: apply's replay tree records no composer module, so a bridge
    placed by the replay's manifest went flat while the binding and the
    reconcile spelled it nested: a fresh project did not build, and a switch
    was refused as a C-symbol collision. Placed by the manifest, both work."""
    root = _composer_project(tmp_path, start)
    if not start:
        _jm(root, "status", "--check")
        _declare(root, "nested")
        _jm(root, "apply")
    bridge = INC.path(root, _composer.bridge_h("playlist", root))
    assert _composer.bridge_h("playlist", root) == (
        "playlist/playlist/playlist_bridge.h"
    )
    assert bridge.is_file()
    assert not INC.path(root, "playlist/playlist_bridge.h").exists()
    for f in _c_files(root):
        for target in _INCLUDE.findall(f.read_text(encoding="utf-8")):
            if target.startswith("studio/"):
                assert INC.resolve(root, target).is_file(), (f, target)
    _jm(root, "status", "--check")
    _builds(root, tmp_path)


# ── S2: a module's `package` moves its headers too ─────────────────────────


def test_changing_a_modules_package_moves_its_headers(tmp_path):
    """S2: under nested the module's `package` decides where its headers
    go, and a manifest edit of it left them where the old package put them:
    `apply` refused forever on the stranded header's guard. It is the same
    move as a layout switch, read from jm's own records of where it put
    them (the component CMakeLists, the core's first include), both ways."""
    _jm(tmp_path, "new", "p")
    root = tmp_path / "p"
    _declare(root, "nested")
    _jm(root, "module", "radio")
    _jm(root, "object", "fir", "--module", "radio")
    _jm(
        root,
        "function",
        "f",
        "--module",
        "radio",
        "--param",
        "x:double",
        "--return-type",
        "double",
    )
    _jm(root, "apply")
    test = INC.native_path(root, "test", "fir")
    test.write_text(
        test.read_text(encoding="utf-8") + _MINE_TEST, encoding="utf-8"
    )
    # The author's own first include: fir's former place is then on record
    # only in its CMakeLists.
    core_c = INC.src_path(root, "fir") / "fir_core.c"
    core_c.write_text(
        f'#include "{INC.include("clib_common.h", root)}"\n'
        + core_c.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    frag = root / "modules" / "radio.toml"
    plain = frag.read_text(encoding="utf-8")
    for package, want in (("front/end", "front/end"), ("", "radio")):
        text = (
            plain.replace(
                "[module.radio]\n", f'[module.radio]\npackage = "{package}"\n'
            )
            if package
            else plain
        )
        frag.write_text(text, encoding="utf-8")
        r = run_cli("status", cwd=root)
        assert "SUPERSEDED" in r.stdout, r.stdout
        _jm(root, "apply")
        for name in ("fir", "radio"):
            assert INC.core_dir(name, root) == f"{want}/{name}"
            assert INC.core_h(root, name).is_file()
        core = (INC.src_path(root, "fir") / "fir_core.c").read_text(
            encoding="utf-8"
        )
        assert f'#include "{INC.core_include("fir", root)}"' in core
        assert (
            INC.native_path(root, "test", "fir")
            .read_text(encoding="utf-8")
            .endswith(_MINE_TEST)
        )
        stale = [
            p.relative_to(root).as_posix()
            for p in (root / "native").rglob("*")
            if (p.is_dir() and not any(p.iterdir()))
            or p.name in ("fir_core.h", "test_fir_core.c")
            and p.parent.parent.name not in want.split("/")[-1:]
        ]
        assert stale == [], stale
        _jm(root, "status", "--check")


def test_module_help_says_the_headers_follow_package_under_nested():
    """N2: `--package` said the C side keeps the module's own name, which
    the nested layout's headers no longer do."""
    r = run_cli("module", "--help")
    text = " ".join((r.stdout + r.stderr).split())
    start = text.index("--package DIR")
    end = text.find(" --", start + len("--package DIR"))
    entry = text[start : end if end >= 0 else len(text)]
    assert "The C side keeps the module's own name." not in entry
    assert 'layout = "nested" the headers' in entry, entry


#: Every call that places a component's file by its `core_dir`.
_PLACERS = {
    "core_h",
    "core_include",
    "core_rel",
    "core_dir",
    "named_header",
    "native_path",
    "native_rel",
    "native_dir",
    "native_slots",
    "bridge_h",
    "header_name",
    "symbols_file",
    # gh-2459: and its C source directory, which follows the header's.
    "src_dir",
    "src_path",
    "src_rel",
    "src_below",
    "subdir_line",
    "component_core_libs",
    "_module_extra_files",
}


def _placements_off_the_manifest(tree, rel) -> "list[str]":
    hits = []
    for f in ast.walk(tree):
        if not isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        has_cfg = any(
            isinstance(n, ast.Name) and n.id == "cfg" for n in ast.walk(f)
        ) or "cfg" in {a.arg for a in f.args.args + f.args.kwonlyargs}
        if not has_cfg:
            continue
        for n in ast.walk(f):
            if not isinstance(n, ast.Call):
                continue
            name = (
                n.func.attr
                if isinstance(n.func, ast.Attribute)
                else getattr(n.func, "id", "")
            )
            text = ast.unparse(n)
            if name in _PLACERS and not re.search(r"cfg|owner|tree", text):
                hits.append(f"{rel}:{n.lineno}  {text[:70]}")
    return sorted(set(hits))


def test_a_header_path_is_asked_of_the_manifest_in_hand():
    """B2's class: where a component's file goes depends on its module under
    nested, and the tree a writer stands in may not say so -- apply's replay
    records no composer module, an object's membership is saved after its
    files. A function holding the manifest asks IT, never the disk."""
    hits = []
    for rel, tree in _modules():
        hits += _placements_off_the_manifest(tree, rel)
    assert hits == [], "\n".join(hits)
    probe = "def f(root, cfg, c):\n    return INC.core_h(root, c)\n"
    assert _placements_off_the_manifest(ast.parse(probe), "probe")


def test_leaving_nested_and_changing_package_in_one_edit(tmp_path):
    """The former package is in no manifest once both change: jm's own
    records of where it put the files say, so they still come home to the
    flat layout."""
    _jm(tmp_path, "new", "p")
    root = tmp_path / "p"
    _declare(root, "nested")
    _jm(root, "module", "radio")
    _jm(root, "object", "fir", "--module", "radio")
    # A function core: its test target is the CMake record of the module
    # core's directory. A module with no functions has none but its sacred
    # _core.c, which is read under nested only (gh-2463 is that case).
    _jm(
        root,
        "function",
        "f",
        "--module",
        "radio",
        "--param",
        "x:double",
        "--return-type",
        "double",
    )
    _jm(root, "apply")
    frag = root / "modules" / "radio.toml"
    frag.write_text(
        frag.read_text(encoding="utf-8").replace(
            "[module.radio]\n", '[module.radio]\npackage = "front"\n'
        ),
        encoding="utf-8",
    )
    _declare(root, "")
    _jm(root, "apply")
    assert (
        INC.core_h(root, "fir") == INC.header_root(root) / "fir" / "fir_core.h"
    )
    assert INC.core_h(root, "fir").is_file()
    hroot = INC.header_root(root)
    assert (hroot / "radio" / "radio_core.h").is_file()  # the flat cname dir
    for left in ("radio/fir", "radio/radio", "front"):
        assert not (hroot / left).exists(), left
    _jm(root, "status", "--check")


@pytest.mark.parametrize("layout", ["", "mirrored", "nested"])
def test_a_foreign_file_in_jms_shape_is_never_a_former_place(tmp_path, layout):
    """A vendored copy of another jm project, and an author's legacy test,
    have exactly the shapes jm writes (``third_party/fir/fir_core.h``,
    ``legacy/fir/test_fir_core.c``). A former place is read from jm's own
    records, never matched in the tree: `status --check` stays clean, and
    `apply` moves nothing of theirs."""
    _jm(tmp_path, "new", "p")
    root = tmp_path / "p"
    _declare(root, layout)
    _jm(root, "module", "dsp")
    _jm(root, "object", "fir", "--module", "dsp")
    _jm(root, "apply")
    vendored = INC.header_root(root) / "third_party" / "fir"
    vendored.mkdir(parents=True)
    foreign = {
        vendored / "fir_core.h": "/* vendored */\n",
        vendored / "helper.h": "/* vendored helper */\n",
        root / INC.TESTS_DIR / "legacy" / "fir" / "test_fir_core.c": (
            "int main(void) { return 0; }\n"
        ),
    }
    for path, text in foreign.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert not [o for o, _n in INC.relocations(root) if (root / o).exists()], (
        INC.relocations(root)
    )
    _jm(root, "status", "--check")
    before = _digest(root)
    _jm(root, "apply")
    for path, text in foreign.items():
        assert path.read_text(encoding="utf-8") == text, path
    assert _digest(root) == before


@pytest.mark.parametrize("layout", ["", "mirrored", "nested"])
def test_the_sacred_cores_first_include_is_not_a_record_outside_nested(
    tmp_path, layout
):
    """The core's first include is the author's: here it names a vendored
    ``third_party/fir/fir_core.h`` (no fir guard) beside a ``helper.h``.
    Flat and mirrored never read it, so it is no place at all; under nested
    a place known only from it moves jm's named headers, and only behind
    the component's own guard -- which a foreign header lacks."""
    _jm(tmp_path, "new", "p")
    root = tmp_path / "p"
    _declare(root, layout)
    _jm(root, "module", "dsp")
    _jm(root, "object", "fir", "--module", "dsp")
    _jm(root, "object", "gain", "--module", "dsp")
    _jm(root, "apply")
    vendored = INC.header_root(root) / "third_party" / "fir"
    vendored.mkdir(parents=True)
    foreign = {
        vendored / "fir_core.h": "/* vendored, its own guard */\n",
        vendored / "helper.h": "/* vendored helper */\n",
    }
    for path, text in foreign.items():
        path.write_text(text, encoding="utf-8")
    core_c = INC.src_path(root, "fir") / "fir_core.c"
    core_c.write_text(
        f'#include "{INC.include("third_party/fir/fir_core.h", root)}"\n'
        + core_c.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    cfg = C.load(root)
    weak = INC._recorded_places(root, cfg, INC.native_names(cfg))[2]
    if layout == "nested":
        assert weak["fir"] == {"third_party/fir"}
    else:
        assert weak == {}
    assert not [o for o, _n in INC.relocations(root) if (root / o).exists()]
    _jm(root, "status", "--check")
    before = _digest(root)
    _jm(root, "apply")
    for path, text in foreign.items():
        assert path.read_text(encoding="utf-8") == text, path
    assert _digest(root) == before


def test_a_place_known_only_from_the_core_moves_jms_headers_only(tmp_path):
    """A module with no functions records its core's directory nowhere but
    the sacred ``_core.c``'s first include -- the author's content. Under
    nested a package change moves that core's named headers on it, behind
    the module's own guard, and leaves every other file there alone."""
    _jm(tmp_path, "new", "p")
    root = tmp_path / "p"
    _declare(root, "nested")
    _jm(root, "module", "radio")
    _jm(root, "object", "fir", "--module", "radio")
    _jm(root, "apply")
    old = INC.core_h(root, "radio")
    helper = old.parent / "helper.h"
    helper.write_text(_MINE_H, encoding="utf-8")
    frag = root / "modules" / "radio.toml"
    frag.write_text(
        frag.read_text(encoding="utf-8").replace(
            "[module.radio]\n", '[module.radio]\npackage = "front"\n'
        ),
        encoding="utf-8",
    )
    _jm(root, "apply")
    assert INC.core_dir("radio", root) == "front/radio"
    assert INC.core_h(root, "radio").is_file() and not old.exists()
    assert helper.read_text(encoding="utf-8") == _MINE_H
    _jm(root, "status", "--check")
