"""gh-2379: an enum-backed parameter is documented by its choices.

A ``[[enum]]``-backed parameter is a C ``int`` and, in Python, one of a set of
strings. The header's ``@param`` prose describes the C ``int`` -- ``0 little,
1 big.`` -- so every generated docstring that repeated it told the caller to
pass a number the binding refuses, beside a signature that says ``str``.

`_docstring.EnumAnn` is the annotation ``str`` carrying the choices, and
`render_numpy_method_doc`, the one place a method-shaped face picks a
parameter's description, documents them instead of the header's prose. An
init parameter, which has no such picker, takes the same sentence from
`_docstring.enum_param_desc`.

The gate is over the FACES, because the first draft of a fix is one face:

- an object's method, standalone and in a module (the runtime ``__doc__`` and
  the ``.pyi`` each);
- a module function (each);
- an object's ``string_enum`` init parameter (each).

Each is rendered from the same header prose, and none may keep it.
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

import pytest
from _jmrun import run_cli

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _docstring as D  # noqa: E402

#: The C prose the header carries for the int, which no face may repeat.
C_PROSE = "0 little, 1 big."
CHOICES = ["le", "be"]
ONE_OF = 'One of ``"le"``, ``"be"``.'


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


def _prose(path: Path, param: str) -> None:
    """Make header *path*'s ``@param <param>`` the C int's description."""
    text = path.read_text(encoding="utf-8")
    new, hits = re.subn(rf"(@param {param})[^\n]*", rf"\1 {C_PROSE}", text)
    assert hits, (path, param)
    path.write_text(new, encoding="utf-8")


@pytest.fixture(scope="module")
def proj(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("enum_docs")
    _ok("new", "p", "--no-c-prefix", cwd=root)
    p = root / "p"
    # A standalone object, with the enum as a method param and an init param.
    _ok(
        "object", "w", "--state", "k:int:0",
        "--init-param", "mode:string_enum:le,be:le",
        cwd=p,
    )  # fmt: skip
    _ok(
        "method", "w", "fin", "--arg-type", "void", "--return-type", "int",
        "--param", "endian:int",
        cwd=p,
    )  # fmt: skip
    # The same method on a module object (the module-aggregated writer).
    _ok("module", "dsp", cwd=p)
    _ok("object", "mx", "--module", "dsp", "--state", "k:int:0", cwd=p)
    _ok(
        "method", "mx", "fin", "--arg-type", "void", "--return-type", "int",
        "--param", "endian:int",
        cwd=p,
    )  # fmt: skip
    # A module function.
    _ok("module", "fns", cwd=p)
    frag = p / "modules" / "fns.toml"
    frag.write_text(
        frag.read_text(encoding="utf-8")
        + '\n[[module.fns.functions]]\nname = "fin"\nreturn_type = "int"\n'
        'params = [{ name = "endian", type = "int" }]\n',
        encoding="utf-8",
    )
    _ok("apply", cwd=p)
    for hdr in (
        p / "native/inc/p/w/w_core.h",
        p / "native/inc/p/mx/mx_core.h",
    ):
        _prose(hdr, "endian")
    _prose(p / "native/inc/p/w/w_core.h", "mode")
    fns_h = p / "native/inc/p/fns/fns_core.h"
    text = fns_h.read_text(encoding="utf-8")
    assert text.count("int fin(int endian);") == 1, text
    fns_h.write_text(
        text.replace(
            "int fin(int endian);",
            "/**\n * @brief Finish.\n * @param endian " + C_PROSE + "\n"
            " * @return Status.\n */\nint fin(int endian);",
        ),
        encoding="utf-8",
    )
    cfg = C.load(p)
    cfg["enum"] = [{"name": "endianE", "values": CHOICES}]
    for obj in ("w", "mx"):
        for m in cfg[obj]["methods"]:
            for prm in m["params"]:
                if prm["name"] == "endian":
                    prm["enum"] = "endianE"
    for fn in cfg["module"]["fns"]["functions"]:
        for prm in fn["params"]:
            prm["enum"] = "endianE"
    C.save(p, cfg)
    # A view of the module object (a second class over the one C core): one
    # that inherits `fin`, and one whose own `methods` entry overrides it
    # (gh-1011), which is a different producer path.
    _ok("view", "mx", "MxView", "--module", "dsp",
        "--create-fn", "mx_create_v", cwd=p)  # fmt: skip
    _ok("view", "mx", "MxOwn", "--module", "dsp",
        "--create-fn", "mx_create_o", cwd=p)  # fmt: skip
    cfg = C.load(p)
    own = [m for m in cfg["mx"]["methods"] if m["name"] == "fin"]
    assert own, cfg["mx"]
    cfg["mx"]["views"][1]["methods"] = copy.deepcopy(own)
    C.save(p, cfg)
    _ok("apply", cwd=p)
    return p


def _read(p: Path, rel: str) -> str:
    return (p / rel).read_text(encoding="utf-8")


def _param_section(text: str, param: str) -> str:
    """The ``<param> : str`` entry of a numpy ``Parameters`` section."""
    m = re.search(
        rf"{param} : str[^\n]*\n((?:[ \t]+[^\n]*\n|[ \t]*\n)+?)(?=\S|\Z)",
        text.replace('\\n"\n     "', "\n").replace('\\n"', "\n"),
    )
    return m.group(0) if m else ""


FACES = {
    # label: (file, parameter)
    "object method runtime": ("native/src/w/w_ext.c", "endian"),
    "object method .pyi": ("src/p/w.pyi", "endian"),
    "module object method .pyi": ("src/p/dsp/dsp.pyi", "endian"),
    "module function .pyi": ("src/p/fns/fns.pyi", "endian"),
}


@pytest.mark.parametrize("face", sorted(FACES))
def test_a_face_documents_the_choices_not_the_c_int(proj, face):
    rel, param = FACES[face]
    text = _read(proj, rel)
    assert C_PROSE not in text.split("fin(")[-1].split("destroy")[0], (
        f"{face} repeats the header's description of the C int:\n{text[:2000]}"
    )
    assert ONE_OF.replace("\\", "") in text.replace("\\", ""), (
        face,
        text[:2000],
    )


def _ml_doc(text: str, name: str) -> str:
    """The ``PyMethodDef`` doc literal of *name*, as C source.

    From the method's own table row to the next row or the table's end, so
    only the docstring is read -- the header's prose also lives in comments
    and examples elsewhere in the file.
    """
    start = text.index(f'{{"{name}",')
    ends = [
        k
        for k in (text.find('\n    {"', start), text.find("\n};", start))
        if k != -1
    ]
    return text[start : min(ends)]


@pytest.mark.parametrize(
    "rel",
    [
        "native/src/w/w_ext.c",
        "native/src/dsp/dsp_ext_mx.c",
        "native/src/fns/fns_ext.c",
    ],
)
def test_a_runtime_doc_documents_the_choices(proj, rel):
    """The runtime ``PyMethodDef`` doc of each face, read from its literal."""
    doc = _ml_doc(_read(proj, rel), "fin").replace('\\"', '"')
    assert "endian : str" in doc, (rel, doc)
    assert C_PROSE not in doc, (rel, doc)
    assert ONE_OF in doc, (rel, doc)


def _class_section(pyi: str, cls: str) -> str:
    """The text of ``class <cls>`` in *pyi*, up to the next class."""
    start = pyi.index(f"class {cls}")
    nxt = re.search(r"\nclass \w+", pyi[start + 1 :])
    return pyi[start : start + 1 + nxt.start()] if nxt else pyi[start:]


@pytest.mark.parametrize("cls", ["Mx", "MxView", "MxOwn"])
def test_a_view_class_documents_the_choices(proj, cls):
    """A view is a second class over one C core. `MxView` inherits `fin`;
    `MxOwn` declares its own entry, which takes a different producer path.
    Both stubs, read from their own class, not from the file."""
    section = _class_section(_read(proj, "src/p/dsp/dsp.pyi"), cls)
    assert "def fin(" in section, section
    fin = section[section.index("def fin(") :].split("\n    def ")[0]
    assert "endian : str" in fin, (cls, fin)
    assert ONE_OF in fin, (cls, fin)
    assert C_PROSE not in fin, (cls, fin)


@pytest.mark.parametrize(
    "rel",
    ["native/src/dsp/dsp_ext_mxview.c", "native/src/dsp/dsp_ext_mxown.c"],
)
def test_a_view_runtime_doc_documents_the_choices(proj, rel):
    doc = _ml_doc(_read(proj, rel), "fin").replace('\\"', '"')
    assert ONE_OF in doc and C_PROSE not in doc, (rel, doc)


def test_a_composer_source_delegating_a_method_documents_the_choices(tmp_path):
    """A composer source's `generates.methods` binds the GENERATOR's method
    through the generator's emitter (gh-2259), so an enum-backed param there
    rides the object-method producer. `mode` is that shape in the gh-2259
    scaffold: an `int` param bound to an `[[enum]]`."""
    import test_gh2259_source_delegates_methods as base

    proj = base._scaffold(tmp_path, shapes=["mode"])
    _prose(proj / "native/inc/dlg/gen/gen_core.h", "k")
    base._ok(run_cli("apply", cwd=proj))
    pyi = (proj / "src/dlg/mix/mix.pyi").read_text(encoding="utf-8")
    seen = 0
    for cls in ("Src", "Gen"):
        try:
            section = _class_section(pyi, cls)
        except ValueError:
            continue
        if "def mode(" not in section:
            continue
        mode = section[section.index("def mode(") :].split("\n    def ")[0]
        assert "One of" in mode and C_PROSE not in mode, (cls, mode)
        seen += 1
    assert seen, pyi[:2000]


@pytest.mark.parametrize("where", ["standalone", "module"])
def test_an_init_parameter_spelled_enum_name_documents_the_choices(
    tmp_path, where
):
    """doppler spells its constructor enums `type = "enum:<name>"` (not
    `string_enum:a,b`; no `enum =` key), e.g. `wfm_writer`'s `endian`.
    `enum:<name>` flattens to `string_enum:` where the init params are read,
    so the doc writers see the same type -- this pins that the spelling
    reaches them, for an object and for a module object."""
    _ok("new", "q", "--no-c-prefix", cwd=tmp_path)
    q = tmp_path / "q"
    cfg = C.load(q)
    cfg["enum"] = [{"name": "endianE", "values": CHOICES}]
    C.save(q, cfg)
    where_args = []
    if where == "module":
        _ok("module", "dsp", cwd=q)
        where_args = ["--module", "dsp"]
    _ok("object", "w3", *where_args, "--init-param", "endian:enum:endianE:le",
        cwd=q)  # fmt: skip
    _prose(q / "native/inc/q/w3/w3_core.h", "endian")
    _ok("apply", cwd=q)
    pyi = _read(
        q, "src/q/dsp/dsp.pyi" if where == "module" else "src/q/w3.pyi"
    )
    m = re.search(
        r'endian : Literal\["le", "be"\], default "le"\n +([^\n]*)\n', pyi
    )
    assert m, pyi[:1500]
    assert m.group(1) == ONE_OF, m.group(1)
    assert C_PROSE not in pyi.split("endian :")[1].split("Examples")[0], pyi


def test_a_capsule_cannot_declare_an_enum_backed_param():
    """A capsule's scalars are one of the C scalars `scalar_meta` knows; an
    enum is not, so there is no capsule face to document. If one is ever
    allowed, this fails and the producer has to be written."""
    from just_makeit import _capsule
    from just_makeit._report import Refusal

    for ptype in ("string_enum:a,b", "enum:level"):
        with pytest.raises(Refusal):
            _capsule.scalar_meta(
                ptype, "capsule module 'cap' init_params row 'm'"
            )


def test_a_composer_extra_method_has_no_enum_key():
    """An `extra_methods` row is the author's C and the author's `args` /
    `doc` text; jm renders no parameter from it, so there is no annotation to
    carry choices. The same trip-wire as the capsule's."""
    from just_makeit import _keys

    assert "enum" not in _keys.COMPOSER_EXTRA_METHOD_KEYS
    assert "params" not in _keys.COMPOSER_EXTRA_METHOD_KEYS


def test_an_init_parameter_stub_documents_the_choices(proj):
    """The class docstring's own entry for ``mode``, not any line in the file
    (the method's ``endian`` carries the same sentence in the same stub)."""
    stub = _read(proj, "src/p/w.pyi")
    m = re.search(
        r'mode : Literal\["le", "be"\], default "le"\n +([^\n]*)\n', stub
    )
    assert m, stub[:1500]
    assert m.group(1) == ONE_OF, m.group(1)
    assert C_PROSE not in m.group(1)


def test_the_standalone_init_parameter_section_says_the_same_sentence():
    """The context slot that seeds the standalone ``.pyi`` (`make_state_ctx`)
    is the init parameter's other writer; it said ``One of: le, be.`` -- a
    third spelling of the sentence -- while the stub said another."""
    from just_makeit._context._state import make_state_ctx

    ctx = make_state_ctx(
        "plan",
        "Plan",
        [],
        no_state=True,
        init_params=[("mode", "string_enum:le,be", "le")],
        csym="plan",
    )
    docs = ctx["pyi_param_docs"]
    assert ONE_OF in docs, docs
    assert "One of:" not in docs, docs


def test_the_header_prose_is_still_used_for_a_plain_parameter():
    """The rule is for an enum, not for every parameter."""
    block = D.DoxyBlock(brief="Run.", params=[("gain", "Scale.")])
    _, _, descs, _, _ = D.render_numpy_method_doc(block, [("gain", "float")])
    assert descs["gain"] == "Scale."


def test_an_enum_annotation_replaces_the_header_prose():
    block = D.DoxyBlock(brief="Run.", params=[("endian", C_PROSE)])
    _, _, descs, _, _ = D.render_numpy_method_doc(
        block, [("endian", D.EnumAnn(CHOICES))]
    )
    assert descs["endian"] == ONE_OF


def test_an_authored_doc_comes_first_and_the_choices_follow():
    """The manifest `doc` is the author's, and outranks the header (gh-1493);
    the choices are not theirs to restate, so they follow it."""
    block = D.DoxyBlock(brief="Run.", params=[("endian", C_PROSE)])
    lines = D.render_numpy_doc(
        block,
        "fin",
        [("endian", D.EnumAnn(CHOICES))],
        "None",
        param_docs={"endian": "Byte order of the file."},
    )
    text = "\n".join(lines)
    assert "Byte order of the file." in text, text
    assert text.index("Byte order") < text.index(ONE_OF), text
    assert C_PROSE not in text, text


def test_an_enum_annotation_is_still_str():
    ann = D.EnumAnn(CHOICES)
    assert ann == "str" and f"x: {ann}" == "x: str"


class TestTheHandleFaces:
    """A handle module builds its own method and class docs
    (`_handle.py_face`, `_pyi_class_docstring`); a full handle project needs a
    backing library, so these ask the two writers directly."""

    ENUMS = {"endianE": CHOICES}
    BLOCK = (
        "/**\n * @brief Set the order.\n * @param endian " + C_PROSE + "\n"
        " */\nvoid sink_set_order(sink_t *h, int endian);\n"
    )
    METHOD = {
        "name": "set_order",
        "fn": "sink_set_order",
        "args": [{"name": "endian", "type": "int", "enum": "endianE"}],
        "returns": None,
    }

    def test_a_method_documents_the_choices(self):
        from just_makeit import _handle as H

        text = "\n".join(
            H.method_doc_lines(
                self.METHOD, {"sink_set_order": self.BLOCK}, self.ENUMS
            )
        )
        assert "endian : str" in text, text
        assert ONE_OF in text, text
        assert C_PROSE not in text, text

    def test_a_create_arg_documents_the_choices(self):
        from just_makeit import _handle as H

        block = D.parse_doxygen_block(
            "/**\n * @brief Open.\n * @param endian " + C_PROSE + "\n */\n"
        )
        text = "\n".join(
            H._pyi_class_docstring(
                "Sink",
                [{"name": "endian", "type": "int", "enum": "endianE"}],
                self.ENUMS,
                block,
            )
        )
        assert ONE_OF in text, text
        assert C_PROSE not in text, text
