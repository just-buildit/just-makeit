"""gh-2399: where an enum parameter's choices sit, and how wide they run.

gh-2379's gate asserted the choice sentence PRESENT on every face. It never
asserted its LAYOUT, so a method parameter with a manifest ``doc`` shipped the
author's text, a blank line, and the sentence unwrapped at 131 columns, while
the same parameter without a ``doc`` wrapped at 79 and an init parameter with
one read correctly.

This renders every enum-parameter shape the object kind and the handle kind
have (a standalone object's method, a module object's, both views, a module
function, a handle method) with and without a ``doc``, with a short and a long
choice list, and reads BOTH faces of each -- every generated ``.pyi`` and every
string literal in every generated ``*_ext*.c`` -- for the two things a layout
is: no choice line past the stub width, and no blank line between a
parameter's text and its choices. It is registration-free over the generated
files, so a shape added later is read the day it is generated.
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

import pytest
from _jmrun import run_cli

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, SRC.as_posix())

import test_handle_build as TH  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit import _docstring as D  # noqa: E402

WIDTH = D.STUB_TARGET_WIDTH
DOC = "The dataset wire type, recorded as SigMF `core:datatype`."
SHORT = ["le", "be"]
LONG = [
    "cf32", "cf64", "ci32", "ci16", "ci8", "f32", "f64", "i32", "i16", "i8",
]  # fmt: skip

#: (choices, with a doc): the four layouts the sentence is joined under.
VARIANTS = {
    "short-nodoc": (SHORT, False),
    "short-doc": (SHORT, True),
    "long-nodoc": (LONG, False),
    "long-doc": (LONG, True),
}


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


def _build(root: Path, choices: "list[str]", with_doc: bool) -> Path:
    _ok("new", "p", "--no-c-prefix", cwd=root)
    p = root / "p"
    _ok("object", "w", "--state", "k:int:0", cwd=p)
    _ok(
        "method", "w", "fin", "--arg-type", "void", "--return-type", "int",
        "--param", "endian:int", cwd=p,
    )  # fmt: skip
    _ok("module", "dsp", cwd=p)
    _ok("object", "mx", "--module", "dsp", "--state", "k:int:0", cwd=p)
    _ok(
        "method", "mx", "fin", "--arg-type", "void", "--return-type", "int",
        "--param", "endian:int", cwd=p,
    )  # fmt: skip
    _ok("module", "fns", cwd=p)
    frag = p / "modules" / "fns.toml"
    frag.write_text(
        frag.read_text(encoding="utf-8")
        + '\n[[module.fns.functions]]\nname = "fin"\nreturn_type = "int"\n'
        'params = [{ name = "endian", type = "int" }]\n',
        encoding="utf-8",
    )
    _ok("apply", cwd=p)
    # The header's `@param` describes the C int: with no manifest `doc` it is
    # the prose the choices replace, so the no-doc layouts need it to exist.
    for hdr in (
        p / "native/inc/p/w/w_core.h",
        p / "native/inc/p/mx/mx_core.h",
    ):
        text = hdr.read_text(encoding="utf-8")
        text, hits = re.subn(
            r"(@param endian)[^\n]*", r"\1 0 little, 1 big.", text
        )
        assert hits, hdr
        hdr.write_text(text, encoding="utf-8")
    fns_h = p / "native/inc/p/fns/fns_core.h"
    text = fns_h.read_text(encoding="utf-8")
    assert text.count("int fin(int endian);") == 1, text
    fns_h.write_text(
        text.replace(
            "int fin(int endian);",
            "/**\n * @brief Finish.\n * @param endian 0 little, 1 big.\n"
            " * @return Status.\n */\nint fin(int endian);",
        ),
        encoding="utf-8",
    )
    cfg = C.load(p)
    cfg["enum"] = [{"name": "endianE", "values": choices}]

    def _mark(prm: dict) -> None:
        prm["enum"] = "endianE"
        if with_doc:
            prm["doc"] = DOC

    for obj in ("w", "mx"):
        for m in cfg[obj]["methods"]:
            for prm in m["params"]:
                if prm["name"] == "endian":
                    _mark(prm)
    for fn in cfg["module"]["fns"]["functions"]:
        for prm in fn["params"]:
            _mark(prm)
    mods = cfg.setdefault("module", {})
    arg = {"name": "m", "type": "int", "enum": "endianE"}
    if with_doc:
        arg["doc"] = DOC
    mods["hnd"] = TH._placed(
        {
            "kind": "handle",
            "backing": "hnd",
            "header": "hnd/hnd.h",
            "type_name": "Hnd",
            "create_fn": "hnd_open",
            "close_fn": "hnd_close",
            "create_args": [{"name": "k", "type": "int"}],
            # a method-level `doc` is what earns a handle method its numpy
            # section (and so its parameter descriptions)
            "methods": [{"name": "setmode", "fn": "hnd_setmode",
                         "doc": "Set the mode.", "args": [arg]}],
        },
        p,
    )  # fmt: skip
    C.save(p, cfg)
    hnd_h = p / "native" / "inc" / "p" / "hnd" / "hnd.h"
    hnd_h.parent.mkdir(parents=True, exist_ok=True)
    hnd_h.write_text(
        "/**\n * @brief Set the mode.\n * @param m 0 little, 1 big.\n */\n"
        "void hnd_setmode(int m);\n",
        encoding="utf-8",
    )
    # Views of the module object: one inheriting `fin`, one overriding it.
    _ok("view", "mx", "MxView", "--module", "dsp",
        "--create-fn", "mx_create_v", cwd=p)  # fmt: skip
    _ok("view", "mx", "MxOwn", "--module", "dsp",
        "--create-fn", "mx_create_o", cwd=p)  # fmt: skip
    cfg = C.load(p)
    own = [m for m in cfg["mx"]["methods"] if m["name"] == "fin"]
    cfg["mx"]["views"][1]["methods"] = copy.deepcopy(own)
    C.save(p, cfg)
    _ok("apply", cwd=p)
    return p


@pytest.fixture(scope="module", params=sorted(VARIANTS))
def project(request, tmp_path_factory) -> "tuple[str, Path]":
    choices, with_doc = VARIANTS[request.param]
    root = tmp_path_factory.mktemp(f"enum_layout_{request.param}")
    return request.param, _build(root, choices, with_doc)


# -- reading a face -------------------------------------------------------

_LITERAL_RUN = re.compile(r'(?:[ \t]*"(?:\\.|[^"\\])*"[ \t]*\n?)+')
_LITERAL = re.compile(r'"((?:\\.|[^"\\])*)"')


def _decoded_docs(c_text: str) -> "list[str]":
    """Every run of adjacent C string literals, as the text Python reads."""
    out = []
    for m in _LITERAL_RUN.finditer(c_text):
        joined = "".join(_LITERAL.findall(m.group(0)))
        out.append(joined.encode().decode("unicode_escape"))
    return out


def _choice_lines(doc: str) -> "list[tuple[int, str]]":
    """``(index, line)`` of each line of *doc* that is the choice sentence:
    one starting ``One of ``\"`` or a wrapped continuation of one."""
    lines = doc.split("\n")
    found: "list[tuple[int, str]]" = []
    inside = False
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith('One of ``"'):
            inside = True
            found.append((i, ln))
        elif inside and s.startswith('``"'):
            found.append((i, ln))
        else:
            inside = False
    return found


def _layout_faults(label: str, doc: str) -> "list[str]":
    faults = []
    lines = doc.split("\n")
    for i, ln in _choice_lines(doc):
        if len(ln) > WIDTH:
            faults.append(f"{label}: {len(ln)} columns: {ln.strip()[:60]}...")
        first = ln.strip().startswith("One of")
        if first and i > 0 and not lines[i - 1].strip():
            faults.append(f"{label}: blank line before the choices: {ln!r}")
    return faults


def _faces(root: Path) -> "list[tuple[str, str]]":
    """``(label, docstring text)`` for both faces of every generated file."""
    out = []
    for pyi in sorted((root / "src").rglob("*.pyi")):
        out.append((pyi.relative_to(root).as_posix(), pyi.read_text("utf-8")))
    for c in sorted((root / "native" / "src").rglob("*_ext*.c")):
        for doc in _decoded_docs(c.read_text(encoding="utf-8")):
            out.append((f"{c.name} (runtime)", doc))
    return out


# -- the gate -------------------------------------------------------------


def test_no_choice_line_is_too_wide_or_set_off_by_a_blank(project):
    name, root = project
    faults = []
    seen = 0
    for label, doc in _faces(root):
        seen += len(_choice_lines(doc))
        faults += _layout_faults(label, doc)
    assert seen >= 12, (name, "the scan found too few choice lines", seen)
    if faults:
        pytest.fail(f"{name}: {len(faults)} fault(s)\n" + "\n".join(faults))


@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_every_shape_is_read_on_both_faces(variant, tmp_path):
    """Armed: each shape's choice sentence is found on its .pyi AND its
    runtime doc, so the layout check above is not passing on an empty read."""
    choices, with_doc = VARIANTS[variant]
    root = _build(tmp_path, choices, with_doc)
    pyi_hits = {
        p.name
        for p in (root / "src").rglob("*.pyi")
        if _choice_lines(p.read_text("utf-8"))
    }
    run_hits = {
        c.name
        for c in (root / "native" / "src").rglob("*_ext*.c")
        if any(_choice_lines(d) for d in _decoded_docs(c.read_text("utf-8")))
    }
    assert {"w.pyi", "dsp.pyi", "fns.pyi", "hnd.pyi"} <= pyi_hits, pyi_hits
    assert {"w_ext.c", "fns_ext.c", "hnd_ext.c"} <= run_hits, run_hits
    assert any(n.startswith("dsp_ext") for n in run_hits), run_hits


@pytest.mark.parametrize(
    "doc",
    [
        'p : str\n    Text.\n\n    One of ``"a"``, ``"b"``.',
        "p : str\n    Text.\n    One of " + '``"abcdefgh"``, ' * 8 + "x.",
    ],
    ids=["blank-line", "too-wide"],
)
def test_the_checker_flags_each_fault(doc):
    """The checker is held to the two faults it exists for."""
    assert _layout_faults("x", doc), doc


def test_the_checker_passes_the_layout_it_wants():
    ok = 'p : str\n    Text.\n    One of ``"a"``, ``"b"``.'
    assert not _layout_faults("x", ok)
