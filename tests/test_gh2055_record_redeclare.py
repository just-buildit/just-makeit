"""gh-2055: `jm record` re-declaring an element its members speak.

`jm record o sample --type double`, on an object whose `write` takes
``sample[]`` and whose `wait` lends ``sample`` back, re-rendered none of
them. `status --check` went red (the binding, the stub, the gh-1404
contract and the header's prototypes all STALE on a standalone object), and
`jm apply`, which turned it green, left a tree that did not build: it
re-declares the prototypes in the header (gh-632) and never touches the
author's definitions, so ``o_core.c`` defined ``q_o_write`` with the old
type. On an object in a module `apply` kept the old wrapper bodies instead
(gh-770), so the binding converted the old type while the contract
asserted the new one, and `jm test` failed.

Two cases, decided on the issue:

- **The C type the element stands for changes** -- a scalar's ``--type``,
  or a switch between scalar and struct. Refused while any member speaks
  it, the way `jm method` refuses to re-declare a member, with the route
  that makes the change instead: take the members out, re-declare, add them
  back. The route is printed as commands, and the compiled test below RUNS
  them as printed.
- **The type stays** -- a struct's columns. Re-rendered through
  `_glue.regenerate`, the re-render `jm property` uses. For an object in a
  module that needed jm's own dtype builders regenerated rather than kept
  by gh-770 (`_object.jm_owned_functions`), which fixes `apply` too.

Running the struct-to-scalar route found the same mistake the other way
round: `jm remove method` of a module object's record reader carried its
builder into the fragment as hand-written, without the cache static it
reads, and the module stopped compiling on a tree `status --check` called
clean. `_docsync.transplant_hand_written` now leaves builders behind.

GATE: for every transition between the two kinds of element, re-declaring
      one that a writer and a reader speak, standalone and in a module,
      either is refused with the tree byte-identical, or leaves
      `status --check` at 0 with the new columns in the binding. The
      refusal's printed route, followed, and a column change both build
      and pass `jm test`. Only `jm record` writes an element declaration,
      so it is the one verb this has to cover.
"""

from __future__ import annotations

import ast
import itertools
import re
import shlex
import shutil
from pathlib import Path

import pytest

from _compilers import default_cc
from _jm_stub import drop_jm_stubs
from _jminc import INC_ROOT
from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _record
from just_makeit import _textio

_M = ("--module", "mod")

#: The two kinds of element: the two keys `_recorddecl.run` refuses
#: together. Each with the element it declares and the pair that speaks it.
KINDS = ("scalar", "struct")

ELEMENT = {"scalar": "sample", "struct": "iq_t"}

DECLARE = {
    "scalar": ("--type", "float _Complex"),
    "struct": ("--field", "i:int16_t", "--field", "q:int16_t"),
}


def _pair(kind: str, *module: str) -> "list[tuple[str, ...]]":
    """The element, a WRITER of it and a READER: the gh-1404 pair."""
    el = ELEMENT[kind]
    reader = (
        ("method", "o", "wait", "--arg-type", "void", "--return-type", el,
         "--borrow", "--param", "n:size_t", *module)
        if kind == "scalar"
        else ("method", "o", "read", "--borrow", "--param", "n:size_t",
              "--return-type", "float _Complex", "--record-dtype", el,
              *module)
    )  # fmt: skip
    return [
        ("record", "o", el, *DECLARE[kind]),
        ("method", "o", "write", "--arg-type", f"{el}[]",
         "--return-type", "bool", *module),
        reader,
    ]  # fmt: skip


SPEAKERS = {"scalar": ("write", "wait"), "struct": ("write", "read")}

#: (old kind, new kind) -> (the re-declaration's flags, what it must do).
#: Held to every pair of `KINDS` by `test_every_transition_has_a_case`.
TRANSITIONS = {
    ("scalar", "scalar"): (("--type", "double"), "refused"),
    ("scalar", "struct"): (("--field", "a:float"), "refused"),
    ("struct", "scalar"): (("--type", "double"), "refused"),
    ("struct", "struct"): (
        ("--field", "i:int16_t", "--field", "q:int16_t",
         "--field", "t:uint32_t"),
        "re-rendered",
    ),
}  # fmt: skip


def _ok(root: Path, *argv: str, stdin: str = "") -> str:
    r = run_cli(*argv, cwd=root, stdin=stdin)
    assert r.returncode == 0, (argv, (r.stdout + r.stderr)[-3000:])
    return r.stdout


def _project(tmp_path: Path, kind: str, placement: str) -> Path:
    _ok(tmp_path, "new", "p")
    root = tmp_path / "p"
    module = _M if placement == "module" else ()
    if module:
        _ok(root, "module", "mod")
    _ok(root, "object", "o", *module)
    for argv in _pair(kind, *module):
        _ok(root, *argv)
    # Not vacuous: the declaration left a clean tree, so a red one below is
    # the re-declaration's.
    s = run_cli("status", "--check", cwd=root)
    assert s.returncode == 0, f"the SETUP left status red:\n{s.stdout}"
    return root


def _tree(root: Path) -> "dict[str, bytes]":
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _route(message: str) -> "list[str]":
    """The commands a refusal prints, one logical line each.

    A trailing backslash continues a command, as in a shell; a ``#`` line
    is the step the author does by hand.
    """
    _, _, tail = message.partition("add them back:\n")
    assert tail, message
    out: list[str] = []
    for line in tail.splitlines():
        line = line.strip()
        if out and out[-1].endswith("\\"):
            out[-1] = out[-1][:-1].rstrip() + " " + line
        elif line:
            out.append(line)
    return out


def _follow(root: Path, route: "list[str]") -> None:
    """Run a refusal's route as an author would.

    Every command as printed, through `run_cli`. A remove asks to confirm,
    so it gets the ``y`` the author types. Each ``#`` step is done the way
    its line says, and a step this cannot follow fails rather than being
    skipped:

    - delete the bodies "as each remove's note says": the note names the
      function and the file it remains in;
    - declare a struct "in the sacred header", with the columns the record
      command just declared;
    - add the pair back "as a <kind> element is spoken", with the two
      spellings the next line prints, one per direction.
    """
    notes: list[tuple[str, str]] = []
    declared: list[str] = []
    adding: list[str] = []
    for line in route:
        if "remove's note says" in line:
            assert notes, f"a hand step before any remove: {line}"
            for sym, fname in notes:
                (core,) = (root / "native" / "src").rglob(fname)
                member = sym.rsplit("_", 1)[-1]
                text = drop_jm_stubs(core.read_text(encoding="utf-8"), member)
                assert f"{sym}(" not in text, text
                _textio.write_text(core, text)
            notes.clear()
        elif "in the sacred header" in line:
            cols = [
                tuple(v.split(":"))
                for k, v in zip(declared, declared[1:])
                if k == "--field"
            ]
            _declare_struct(root, declared[3], cols)
        elif line.startswith("# and add "):
            m = re.match(r"# and add (.+) back as an? \w+ element", line)
            assert m, line
            adding = m.group(1).split(" and ")
        elif line.startswith("#   --arg-type "):
            m = re.match(
                r"#   --arg-type '(\w+\[\])' \(.*?\) or "
                r"--(record-dtype|return-type) (\w+) \(",
                line,
            )
            assert m and len(adding) == 2, (line, adding)
            rows_in, out_flag, el = m.groups()
            writer, reader = adding
            _ok(root, "method", "o", writer, "--arg-type", rows_in,
                "--return-type", "bool")  # fmt: skip
            _ok(root, "method", "o", reader, "--borrow", "--param",
                "n:size_t", f"--{out_flag}", el)  # fmt: skip
        elif line.startswith("#"):
            raise AssertionError(f"a hand step this cannot follow: {line}")
        else:
            argv = shlex.split(line)
            assert argv[0] == "just-makeit", line
            out = _ok(root, *argv[1:], stdin="y\n")
            if argv[1] == "remove":
                note = re.search(r"note: (\w+)\(\) remains in (\w+\.c)", out)
                assert note, out
                notes.append(note.groups())
            elif argv[1] == "record":
                declared = argv


def test_every_transition_has_a_case():
    assert set(TRANSITIONS) == set(itertools.product(KINDS, KINDS))


@pytest.mark.parametrize("placement", ["standalone", "module"])
@pytest.mark.parametrize(
    "old, new",
    sorted(TRANSITIONS),
    ids=[f"{o}-{n}" for o, n in sorted(TRANSITIONS)],
)
def test_a_redeclare_leaves_the_tree_in_sync(tmp_path, placement, old, new):
    root = _project(tmp_path, old, placement)
    flags, outcome = TRANSITIONS[old, new]
    before = _tree(root)

    r = run_cli("record", "o", ELEMENT[old], *flags, cwd=root)

    if outcome == "refused":
        assert r.returncode == 1, r.stdout + r.stderr
        assert r.stderr.startswith("error: "), r.stderr
        for member in SPEAKERS[old]:
            assert f"remove method {member} --object o" in r.stderr
        assert _tree(root) == before, "a refusal wrote to the tree"
        # ...and the route it prints is one jm accepts, end to end, for
        # every transition (`test_the_refusals_route_builds_and_passes`
        # also compiles one).
        _follow(root, _route(r.stderr))
        s = run_cli("status", "--check", cwd=root)
        assert s.returncode == 0, s.stdout
        _builders_have_caches(root)
        return
    assert r.returncode == 0, r.stdout + r.stderr
    s = run_cli("status", "--check", cwd=root)
    assert s.returncode == 0, s.stdout
    _builders_speak(root, "t", len(SPEAKERS[old]))


def _bindings(root: Path) -> "list[str]":
    return [
        p.read_text(encoding="utf-8")
        for p in (root / "native" / "src").rglob("*_ext*.c")
    ]


def _builders_speak(root: Path, column: str, n: int) -> None:
    """Every dtype builder the extension builds has *column* -- not one.

    `status` is about the files the manifest owns, and called a module
    binding with the old builders clean (gh-770 kept them); what the
    extension compiles is the claim.
    """
    olds = sum(b.count("offsetof(iq_t, q)") for b in _bindings(root))
    news = sum(b.count(f"offsetof(iq_t, {column})") for b in _bindings(root))
    assert olds == news == n, (olds, news)
    _builders_have_caches(root)


def _builders_have_caches(root: Path) -> None:
    """A dtype builder never stands without the cache it reads.

    A `jm remove` carried a removed reader's builder as hand-written and
    not its file-scope cache, so the module stopped compiling -- on a tree
    `status --check` called clean.
    """
    for text in _bindings(root):
        for fn in _record.dtype_builders(text):
            sid = fn[: -len("_get_dtype")]
            assert f"static PyArray_Descr *{sid}_dtype = NULL;" in text, fn


def test_apply_rewrites_a_builder_it_kept_by_name(tmp_path):
    """`apply`, the other writer of a module fragment, restored jm's old
    builders by name too. The column reaches the manifest without `jm
    record` here -- the manifest-first path, and the tree a re-declare left
    before this fix."""
    root = _project(tmp_path, "struct", "module")
    cfg = C.load(root)
    cfg["o"]["records"][0]["fields"].append({"name": "t", "type": "uint32_t"})
    C.save(root, cfg)

    out = _ok(root, "apply")

    assert "record dtype O_read_get_dtype" in out, out
    assert run_cli("status", "--check", cwd=root).returncode == 0
    _builders_speak(root, "t", len(SPEAKERS["struct"]))
    # ...and once is enough: the next apply has nothing to say.
    assert "record dtype" not in _ok(root, "apply")


def test_the_same_type_redeclared_rerenders(tmp_path):
    """A doc-only re-declare keeps the type: re-rendered, not refused."""
    root = _project(tmp_path, "scalar", "standalone")
    _ok(root, "record", "o", "sample", *DECLARE["scalar"], "--doc", "IQ.")
    assert run_cli("status", "--check", cwd=root).returncode == 0


def test_an_element_nothing_speaks_is_freely_redeclared(tmp_path):
    _ok(tmp_path, "new", "p")
    root = tmp_path / "p"
    _ok(root, "object", "o")
    _ok(root, "record", "o", "sample", *DECLARE["scalar"])
    _ok(root, "record", "o", "sample", "--type", "double")
    _ok(root, "record", "o", "sample", "--field", "a:float")
    assert run_cli("status", "--check", cwd=root).returncode == 0


def test_a_view_member_is_refused_without_a_route(tmp_path):
    """`jm remove` cannot take out a view's method, so no route is printed
    for one: a refusal never prints a command that cannot run."""
    _ok(tmp_path, "new", "p")
    root = tmp_path / "p"
    _ok(root, "module", "mod")
    _ok(root, "object", "o", *_M)
    _ok(root, "record", "o", "sample", *DECLARE["scalar"])
    _ok(root, "view", "o", "V", *_M, "--create-fn", "o_create_v")
    _ok(root, "method", "o", "w2", *_M, "--view", "V",
        "--arg-type", "sample[]", "--return-type", "bool")  # fmt: skip
    before = _tree(root)

    r = run_cli("record", "o", "sample", "--type", "float", cwd=root)

    assert r.returncode == 1, r.stdout + r.stderr
    assert "V.w2" in r.stderr, r.stderr
    assert "just-makeit" not in r.stderr, r.stderr
    assert _tree(root) == before


def test_only_jm_record_writes_an_element_declaration():
    """The verbs that change what members speak, derived from the source.

    An element is changed only where ``["records"]`` is written. `apply`
    copies the declarations into its scratch tree (gh-1411); every other
    writer is a verb this file has to cover, so a new one fails here until
    it has a case.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "just_makeit"
    writers = set()
    for path in sorted(src.rglob("*.py")):
        if {"templates", "examples"} & set(path.relative_to(src).parts):
            continue  # data jm renders from, as `test_doctests` excludes
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.Delete)):
                targets = getattr(node, "targets", None) or [node.target]
            elif isinstance(node, ast.Call) and isinstance(
                node.func, ast.Attribute
            ):
                targets = [node.func.value]
            for t in targets:
                if (
                    isinstance(t, ast.Subscript)
                    and isinstance(t.slice, ast.Constant)
                    and t.slice.value == "records"
                ):
                    writers.add(path.relative_to(src).as_posix())
    assert writers == {"_recorddecl.py", "_apply.py"}, writers


# -- compiled: the route as printed, and a column change ----------------------


def _no_toolchain() -> "str | None":
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    return None


def _jm_test(root: Path) -> None:
    r = run_cli("test", cwd=root)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-4000:]
    assert "100% tests passed" in out, out[-4000:]


def _declare_struct(
    root: Path, name: str, cols: "list[tuple[str, str]]"
) -> None:
    """The author's half of a struct element: the struct, in the header.

    jm declares the columns; the C struct is the author's, and the binding
    reads each column's offset from it. Marked, so a later call replaces
    the struct rather than declaring it twice.
    """
    header = root / INC_ROOT / "o" / "o_core.h"
    text = header.read_text(encoding="utf-8")
    body = "".join(f"    {t} {n};\n" for n, t in cols)
    block = f"/* {name} */\ntypedef struct {{\n{body}}} {name};\n/* end */\n"
    old = re.compile(rf"/\* {name} \*/\n.*?/\* end \*/\n", re.S)
    if old.search(text):
        text = old.sub(lambda _m: block, text)
    else:
        text = text.replace("typedef struct", block + "\ntypedef struct", 1)
    _textio.write_text(header, text)


_REFUSED = sorted(
    k for k, (_f, out) in TRANSITIONS.items() if out == "refused"
)


@pytest.mark.slow
@pytest.mark.skipif(bool(_no_toolchain()), reason=str(_no_toolchain()))
@pytest.mark.parametrize("placement", ["standalone", "module"])
@pytest.mark.parametrize(
    "old, new", _REFUSED, ids=[f"{o}-{n}" for o, n in _REFUSED]
)
def test_the_refusals_route_builds_and_passes(tmp_path, placement, old, new):
    """The route a type change is refused with, run as printed.

    The scalar change goes to the issue's own ``double``. It went to
    ``float`` until gh-2067: a ``double`` element's contract could not pass
    on a FIRST declaration, its foreign dtype (``int8``) casting safely
    into ``float64``.
    """
    root = _project(tmp_path, old, placement)
    if old == "struct":
        _declare_struct(root, "iq_t", [("i", "int16_t"), ("q", "int16_t")])
    flags, _ = TRANSITIONS[old, new]
    r = run_cli("record", "o", ELEMENT[old], *flags, cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr

    _follow(root, _route(r.stderr))

    s = run_cli("status", "--check", cwd=root)
    assert s.returncode == 0, s.stdout
    _jm_test(root)


@pytest.mark.slow
@pytest.mark.skipif(bool(_no_toolchain()), reason=str(_no_toolchain()))
@pytest.mark.parametrize("placement", ["standalone", "module"])
def test_a_column_change_builds_and_passes(tmp_path, placement):
    """Armed: the contract is run against the binding before and after, and
    the module binding kept the old columns until `jm_owned_functions`."""
    root = _project(tmp_path, "struct", placement)
    _declare_struct(root, "iq_t", [("i", "int16_t"), ("q", "int16_t")])
    _jm_test(root)

    flags, _ = TRANSITIONS["struct", "struct"]
    _ok(root, "record", "o", "iq_t", *flags)
    _declare_struct(
        root, "iq_t", [("i", "int16_t"), ("q", "int16_t"), ("t", "uint32_t")]
    )

    s = run_cli("status", "--check", cwd=root)
    assert s.returncode == 0, s.stdout
    # A clean build, so the change reaches the binary through jm's sources
    # alone. Incrementally, macOS CI twice kept the binding object from the
    # build above -- its log shows `o_core.c` recompiled and the binding not,
    # though `jm record` had just rewritten it -- and the contract failed on
    # the old dtype builder. The record and the edit land within about a
    # second of that build, where a timestamp read to the second calls the
    # object current; a clean build has no timestamps to misread.
    shutil.rmtree(root / "build")
    _jm_test(root)
