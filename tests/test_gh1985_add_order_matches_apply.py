"""gh-1985: every adder writes the root CMakeLists and the umbrella header in
the order `apply` writes them.

`jm new a && cd a && jm object q && jm object o` left `status --check` red on
both files. `apply` listed the components in manifest order -- the replay
materializes them in that order, each block going directly under its
sentinel and each include going last -- while the adders inserted the same
way in the order their COMMANDS ran. The two agree only when those orders
do, and they differ:

- in a split layout, whose fragments load sorted by path (the issue);
- when a standalone object follows a module object -- the replay builds
  every standalone object first, in either layout;
- when the new block lands above a ``c_dep`` that `apply` keeps on top;
- after ``migrate-to-fragments`` / ``split-objects``, which change the
  manifest's order without touching either file;
- on `apply --only`, whose own copy of the umbrella insert also dropped the
  blank line a full `apply` writes.

There is now one order (`_config.object_order`, `_libwiring.section_order`)
and one sort per file (`_libwiring.order_sections`,
`_init.order_umbrella_includes`). The writers sort what they write by the
manifest ON DISK, so every adder saves its manifest entry first; `apply`
sorts its result by the same functions.

GATE: every function that calls one of those writers (derived from the
source -- `test_every_adder_has_a_case`) has a scenario here that adds in
the order `apply` does not list, and the scenario must reach it and leave
`status --check` at exit 0. The writers are themselves derived: the
functions outside `_apply` that read the sort. An adder reached only from
inside `apply`'s replay is exempt, also by derivation -- `apply` re-sorts
everything the replay leaves (`test_apply_orders_whatever_the_replay_left`).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import just_makeit
from _jmrun import run_cli
from just_makeit import _apply
from just_makeit import _textio

SRC = Path(just_makeit.__file__).parent

#: The one sort per file. Everything else here is derived from these.
ORDER_FNS = {"order_sections", "order_umbrella_includes"}


# ── Deriving the adders from the source ──────────────────────────────────────


def _modules() -> "dict[str, ast.Module]":
    out = {}
    for path in sorted(SRC.rglob("*.py")):
        if "templates" in path.parts or "examples" in path.parts:
            continue
        out[path.stem] = ast.parse(path.read_text(encoding="utf-8"))
    return out


def _functions(tree: ast.Module):
    """Every function in *tree*, by its own name."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _aliases(tree: ast.Module, names: "set[str]") -> "dict[str, str]":
    """``local name -> imported name`` for every ``from ... import`` of one of
    *names* in *tree*, at any depth (jm imports inside functions)."""
    out = {n: n for n in names}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name in names:
                    out[a.asname or a.name] = a.name
    return out


def _references(fn: ast.AST, local: "dict[str, str]") -> "set[str]":
    """Which of *local*'s targets *fn* names -- called or passed along."""
    out = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id in local:
            out.add(local[node.id])
        elif isinstance(node, ast.Attribute) and node.attr in local.values():
            out.add(node.attr)
    return out


def _callers(
    mods: "dict[str, ast.Module]", names: "set[str]", skip=()
) -> "set[str]":
    """``module.function`` for every function naming one of *names*."""
    out = set()
    for stem, tree in mods.items():
        local = _aliases(tree, names)
        for fn in _functions(tree):
            if fn.name in names or stem in skip:
                continue
            if _references(fn, local):
                out.add(f"{stem}.{fn.name}")
    return out


def _sites(mods: "dict[str, ast.Module]", target: str) -> "set[str]":
    """Every function that refers to *target* (``module.function``)."""
    tmod, tfn = target.split(".")
    out = set()
    for stem, tree in mods.items():
        modnames = {tmod} if stem != tmod else set()
        fnnames = {tfn} if stem == tmod else set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name == tmod:
                        modnames.add(a.asname or a.name)
                    if node.module and node.module.endswith(tmod):
                        if a.name == tfn:
                            fnnames.add(a.asname or a.name)
        for fn in _functions(tree):
            for node in ast.walk(fn):
                hit = (
                    isinstance(node, ast.Attribute)
                    and node.attr == tfn
                    and isinstance(node.value, ast.Name)
                    and node.value.id in modnames
                ) or (isinstance(node, ast.Name) and node.id in fnnames)
                if hit and f"{stem}.{fn.name}" != target:
                    out.add(f"{stem}.{fn.name}")
    return out


def derived():
    """``(writers, adders, replay_only)``, all read from the source.

    - writers: the functions outside `_apply` that read the sort -- the ones
      that put an entry into either file in order;
    - adders: every function that calls a writer;
    - replay_only: the adders reached from nowhere but `_apply._replay`.
    """
    mods = _modules()
    writers = {
        c.split(".")[1] for c in _callers(mods, ORDER_FNS, skip={"_apply"})
    }
    adders = _callers(mods, writers)
    replay_only = {a for a in adders if _sites(mods, a) == {"_apply._replay"}}
    return writers, adders, replay_only


# ── The scenarios ────────────────────────────────────────────────────────────


def _root_cmake(root: Path) -> Path:
    return root / "CMakeLists.txt"


def _drop_block(name: str):
    """Delete ``add_subdirectory(native/src/<name>)`` and its wiring, by hand
    -- an interrupted edit `jm` must then put back where `apply` would."""

    def edit(root: Path) -> None:
        path = _root_cmake(root)
        text = path.read_text(encoding="utf-8")
        new = re.sub(
            rf"^add_subdirectory\(native/src/{name}\)\n"
            r"(?:target_sources\(.*\n)*",
            "",
            text,
            count=1,
            flags=re.M,
        )
        assert new != text, f"no block for {name} to drop"
        _textio.write_text(path, new)

    edit.__name__ = f"drop-block-{name}"
    return edit


def _drop_include(name: str):
    """Delete ``<name>``'s include line (and the blank line after it) from
    the umbrella header, by hand."""

    def edit(root: Path) -> None:
        (umbrella,) = (root / "native" / "inc").rglob(f"{root.name}.h")
        text = umbrella.read_text(encoding="utf-8")
        new = re.sub(
            rf'^#include "[\w/]*{name}/{name}_core\.h"\n\n',
            "",
            text,
            count=1,
            flags=re.M,
        )
        assert new != text, f"no include for {name} to drop"
        _textio.write_text(umbrella, new)

    edit.__name__ = f"drop-include-{name}"
    return edit


def _hand_written(module: str):
    """Make *module* ``no_generate``, as an author taking it over does: the
    manifest says so, and its CMakeLists is the author's (one with no core
    of its own, so nothing is left for `status` to call unwired)."""

    def edit(root: Path) -> None:
        frag = root / "modules" / f"{module}.toml"
        head = f"[module.{module}]\n"
        text = frag.read_text(encoding="utf-8")
        assert head in text, text
        _textio.write_text(
            frag,
            text.replace(
                head,
                f'{head}no_generate = true\nno_generate_reason = "mine"\n',
                1,
            ),
        )
        _textio.write_text(
            root / "native" / "src" / module / "CMakeLists.txt",
            "# hand-written\n",
        )

    edit.__name__ = f"hand-written-{module}"
    return edit


_FN = ("--param", "x:double", "--return-type", "double")

#: adder (``module.function``) -> flavor -> (`jm new` flags, steps).
#:
#: Every flavor but the one marked adds in an order the manifest does not
#: list -- so on the code before gh-1985 it ends with `status --check` red.
#: A step is argv for `jm`, or a hand edit of the tree. Keyed by what
#: `test_every_adder_has_a_case` derives from the source.
CASES = {
    "_init.run": {
        # The issue: a split layout loads objects/*.toml sorted.
        "reverse-sorted": ((), [("object", "q"), ("object", "o")]),
        "new-reverse-sorted": (("--object", "q", "--object", "o"), []),
        # The replay builds standalone objects before module objects --
        # in a central manifest too.
        "after-a-module-object": (
            ("--no-fragments",),
            [("module", "m"), ("object", "x", "--module", "m"),
             ("object", "s")],
        ),
        # `apply` keeps the c_deps on top; the adder put its block above.
        "below-a-c-dep": (
            ("--c-dep", "vend"),
            [("object", "x"), ("apply",), ("object", "y")],
        ),
    },
    "_object.run": {
        # A module object's place follows its MODULE's, and modules/*.toml
        # load sorted too.
        "reverse-sorted-modules": (
            (),
            [("module", "q"), ("module", "o"),
             ("object", "x", "--module", "q"),
             ("object", "y", "--module", "o")],
        ),
    },
    "_module.run": {
        "reverse-sorted": ((), [("module", "q"), ("module", "o")]),
        # Green before gh-1985 too: it pins the other half of the order,
        # where `apply` keeps a `no_generate` module's bare block last.
        "beside-a-no-generate-module": (
            (),
            [("module", "a"), ("module", "b"), _hand_written("b"),
             ("apply",), ("module", "c")],
        ),
    },
    "_object._regenerate_module_now": {
        # Its splice re-adds a module's wiring that is missing -- at the
        # top, before gh-1985, wherever the module belonged.
        "self-heal": (
            (),
            [("module", "o"), ("module", "q"), _drop_block("o"),
             ("function", "f", "--module", "o", *_FN)],
        ),
    },
    "_apply._add_umbrella_include": {
        # `apply --only` adds one component's lines; a full `apply` must
        # then have nothing to move.
        "only": (
            (),
            [("object", "o"), ("object", "q"), _drop_block("o"),
             _drop_include("o"), ("apply", "--only=o")],
        ),
    },
    "_migrate.run": {
        "central-to-fragments": (
            ("--no-fragments",),
            [("object", "q"), ("object", "o"), ("migrate-to-fragments",)],
        ),
    },
    "_split_objects.run": {
        "central-to-fragments": (
            ("--no-fragments",),
            [("object", "q"), ("object", "o"), ("split-objects",)],
        ),
    },
}  # fmt: skip


def test_every_adder_has_a_case():
    """The adders are the source's, not this file's: a new call to a writer
    fails here until it has a scenario -- or is reachable only from inside
    `apply`'s replay, whose result `apply` re-sorts."""
    writers, adders, replay_only = derived()
    # Not vacuous: the derivation finds the writers it exists to find.
    assert {"splice_cmake_component", "insert_umbrella_include"} <= writers
    assert set(CASES) == adders - replay_only, (
        f"adders without a case: {sorted(adders - replay_only - set(CASES))};"
        f" cases for no adder: {sorted(set(CASES) - adders)}"
    )


def _record_writers(monkeypatch) -> "set[str]":
    """Wrap every writer wherever jm binds it; return the set the wrappers
    fill with each caller's ``module.function``."""
    import importlib
    import pkgutil
    import sys

    for info in pkgutil.walk_packages(
        just_makeit.__path__, prefix="just_makeit."
    ):
        if not info.name.startswith(
            ("just_makeit.templates", "just_makeit.examples")
        ):
            importlib.import_module(info.name)
    writers, _, _ = derived()
    seen: "set[str]" = set()
    originals = {}
    for name in writers:
        for mod in list(sys.modules.values()):
            fn = getattr(mod, name, None) if mod else None
            if callable(fn) and getattr(fn, "__module__", "").startswith(
                "just_makeit."
            ):
                originals[name] = fn
                break

    def wrap(fn):
        def recording(*args, **kwargs):
            code = sys._getframe(1).f_code
            seen.add(f"{Path(code.co_filename).stem}.{code.co_name}")
            return fn(*args, **kwargs)

        return recording

    for name, fn in originals.items():
        wrapper = wrap(fn)
        for mod in list(sys.modules.values()):
            if not (mod and mod.__name__.startswith("just_makeit")):
                continue
            for attr, value in list(vars(mod).items()):
                if value is fn:
                    monkeypatch.setattr(mod, attr, wrapper)
    return seen


def _ok(root: Path, *argv: str) -> None:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (argv, (r.stdout + r.stderr)[-3000:])


def _run(tmp_path: Path, new_flags, steps) -> Path:
    _ok(tmp_path, "new", "p", *new_flags)
    root = tmp_path / "p"
    for step in steps:
        if callable(step):
            step(root)
        else:
            _ok(root, *step)
    return root


@pytest.mark.parametrize(
    "adder, flavor",
    [(a, f) for a, flavors in CASES.items() for f in flavors],
    ids=[f"{a}-{f}" for a, flavors in CASES.items() for f in flavors],
)
def test_the_add_leaves_what_apply_writes(
    tmp_path, monkeypatch, adder, flavor
):
    new_flags, steps = CASES[adder][flavor]
    seen = _record_writers(monkeypatch)
    root = _run(tmp_path, new_flags, steps)
    s = run_cli("status", "--check", cwd=root)
    assert s.returncode == 0, s.stdout
    # Not vacuous: the scenario reached the adder it is filed under.
    assert adder in seen, f"{adder} was never reached; reached: {sorted(seen)}"


def test_apply_orders_whatever_the_replay_left(tmp_path, monkeypatch):
    """`apply`'s order is the sort's, not the replay's.

    The replay's writers sort too, so on today's replay `apply`'s own sort
    changes nothing -- which is exactly why it needs this: a replay that
    materialized in another order (a reordered loop, a writer that does not
    sort, the replay-only adders exempted above) would otherwise reach the
    project as that order, and every adder would disagree with it.
    """
    root = _run(
        tmp_path,
        (),
        [("module", "m"), ("module", "n"), ("object", "o"), ("object", "q"),
         ("object", "x", "--module", "m"), ("object", "y", "--module", "n")],
    )  # fmt: skip
    before = {
        p: p.read_bytes()
        for p in (_root_cmake(root), *(root / "native" / "inc").rglob("p.h"))
    }
    real_replay = _apply.replay_project

    def shuffled(cfg, temp_root, project_root, **kw):
        real_replay(cfg, temp_root, project_root, **kw)
        # Reverse every run of jm's blocks, and the umbrella's includes.
        cmake = temp_root / "CMakeLists.txt"
        text = cmake.read_text(encoding="utf-8")
        for sentinel in ("# ── Components", "# ── Modules"):
            start = text.index("\n", text.index(sentinel)) + 1
            run, end = [], start
            while m := _apply._SUBDIR_BLOCK.match(text, end):
                run.append(m.group(0))
                end = m.end()
            assert len(run) >= 2, f"nothing to shuffle under {sentinel}"
            text = text[:start] + "".join(reversed(run)) + text[end:]
        _textio.write_text(cmake, text)
        (umbrella,) = (temp_root / "native" / "inc").rglob("p.h")
        lines = umbrella.read_text(encoding="utf-8").splitlines(True)
        slots = [i for i, ln in enumerate(lines) if ln.startswith("#include")]
        assert len(slots) >= 2, "nothing to shuffle in the umbrella"
        for i, ln in zip(slots, reversed([lines[i] for i in slots])):
            lines[i] = ln
        _textio.write_text(umbrella, "".join(lines))

    monkeypatch.setattr(_apply, "replay_project", shuffled)
    _ok(root, "apply")
    after = {p: p.read_bytes() for p in before}
    assert after == before, "apply wrote the replay's order, not the sort's"
