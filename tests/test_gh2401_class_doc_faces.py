"""gh-2401: every class doc the ``.pyi`` shows, ``help(Obj)`` shows too.

The runtime ``tp_doc`` rendered the class block only on a hand-written
list of reasons -- a summary, a ``Raises``, a ``Warns``, an enum's
choices -- while the ``.pyi`` beside it renders every authored input. So a
documented constructor parameter (``[[<obj>.init_params]] doc``) or state
field (``[[<obj>.state]] doc``) reached the stub and not the runtime: the
two-faces disagreement gh-642 exists to prevent, on every kind of object.

Now one predicate, `_stubs.class_doc_authored`, decides for every runtime
class docstring jm writes (a standalone object's, a module object's, a
view's, `jm bind`'s), and `_docsync` asks the same predicate, of the
manifest's own prose, whether a sacred fragment's ``tp_doc`` is stale. It
asks the builder itself: the block with the authored inputs differs from
the block without them.

GATE, compiled: for every authored input of the class docstring -- the
predicate's own `_stubs.CLASS_DOC_INPUTS`: its keywords and an init-param's
``doc``, held to that list and to the class-doc keys `_keys` accepts --
an object authoring only that input, standalone and in a module, carries
its marker in the ``.pyi`` AND in the built class's ``__doc__``. And a view
that inherits its parent's constructor carries the parent's parameter doc.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, NamedTuple

import pytest

from _cmake import PYTHON3_ARG
from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _keys
from just_makeit import _stubs as S


class Case(NamedTuple):
    """One authored class-doc input, on an object of its own.

    ``argv`` is the `jm object` tail that gives the input something to
    document; ``author`` writes the input, given the manifest, the object
    and its marker; ``header`` edits the object's sacred header text.
    """

    argv: tuple
    author: Callable[[dict, str, str], None] = lambda c, o, m: None
    header: Callable[[str, str, str], str] = lambda t, o, m: t


def _brief(text: str, obj: str, marker: str) -> str:
    """Put an authored ``@brief`` on the object's ``create()``."""
    decl = f"{obj}_state_t *{obj}_create("
    assert text.count(decl) == 1, text
    return text.replace(decl, f"/**\n * @brief {marker}\n */\n{decl}", 1)


def _enum_header(text: str, obj: str, marker: str) -> str:
    """Declare the C enum whose enumerator docs the choices map to."""
    guard = '#ifdef __cplusplus\nextern "C" {\n#endif\n'
    assert text.count(guard) >= 1, text
    enum = (
        f"typedef enum {{\n    {obj.upper()}_LOW = 0, ///< {marker}\n"
        f"    {obj.upper()}_HIGH = 1 ///< High.\n}} {obj}_kind_t;\n"
    )
    return text.replace(guard, guard + enum, 1)


def _enum(cfg: dict, obj: str, marker: str) -> None:
    cfg.setdefault("enum", []).append(
        {
            "name": f"{obj}_kind",
            "values": [f"{obj}_lo", f"{obj}_hi"],
            "enumerators": [f"{obj.upper()}_LOW", f"{obj.upper()}_HIGH"],
        }
    )
    cfg[obj]["init_params"][0]["type"] = f"string_enum:{obj}_lo,{obj}_hi"
    cfg[obj]["init_params"][0]["default"] = f"{obj}_lo"


_PARAM = ("--init-param", "mode:int:0")
_FLAG = ("--state", "flag:bool:false")

#: Each authored input of the class docstring: the predicate's own list
#: (`_stubs.CLASS_DOC_INPUTS`).
CASES: dict[str, Case] = {
    "manifest_doc": Case(_FLAG + _PARAM, lambda c, o, m: c[o].update(doc=m)),
    "doc_blocks": Case(_FLAG + _PARAM, header=_brief),
    "state_docs": Case(
        ("--state", "gain:double:1.0"),
        lambda c, o, m: c[o]["state"][0].update(doc=m),
    ),
    "raises": Case(
        _FLAG + _PARAM,
        lambda c, o, m: c[o].update(
            create_error="ValueError", create_error_message=m
        ),
    ),
    "warns": Case(
        _FLAG + _PARAM,
        lambda c, o, m: c[o].update(
            warnings=[
                {"category": "UserWarning", "message": m, "condition": "flag"}
            ]
        ),
    ),
    "enum_choices": Case(
        _FLAG + ("--init-param", "kind:int:0"), _enum, header=_enum_header
    ),
    "init_params.doc": Case(
        _FLAG + _PARAM,
        lambda c, o, m: c[o]["init_params"][0].update(doc=m),
    ),
}


def test_every_authored_input_has_a_case() -> None:
    """The cases are the predicate's inputs, and cover `_keys`' doc keys."""
    assert set(CASES) == S.CLASS_DOC_INPUTS
    assert S.MANIFEST_CLASS_DOC_INPUTS <= S.CLASS_DOC_INPUTS
    # The class docstring reads the `doc` of these three vocabularies.
    for vocab in (_keys.OBJECT_KEYS, _keys.STATE_KEYS, _keys.INIT_PARAM_KEYS):
        assert "doc" in vocab


def _obj(kind: str, case: str) -> str:
    return f"{kind}_{case.replace('.', '_')}"


def _marker(obj: str) -> str:
    # One token: a docstring is reflowed, and a marker must survive it.
    return f"MARKER_{obj.upper()}_DOC"


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout + r.stderr)
    return r.stdout


_PROBE = """
import importlib, json, sys
sys.path.insert(0, "src")
out = {}
for mod, cls in json.loads(sys.argv[1]):
    out[cls] = getattr(importlib.import_module(mod), cls).__doc__
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """One project: an object per case, standalone and in module ``dsp``,
    and a view inheriting the param-documented module object's constructor.
    Returns ``{class: (stub text, runtime __doc__)}``."""
    if not shutil.which("cmake"):
        pytest.fail("cmake not found: this gate builds the project")
    root = tmp_path_factory.mktemp("gh2401faces")
    _ok("new", "p", "--no-c-prefix", cwd=root)
    proj = root / "p"
    _ok("module", "dsp", cwd=proj)
    objs: list[tuple[str, str, Case]] = []
    for case, spec in CASES.items():
        for kind, mod in (("s", ()), ("m", ("--module", "dsp"))):
            obj = _obj(kind, case)
            _ok("object", obj, *spec.argv, *mod, cwd=proj)
            objs.append((obj, "p.dsp" if mod else "p", spec))
    parent = _obj("m", "init_params.doc")
    _ok(
        "view", parent, "Inherits", "--module", "dsp",
        "--create-fn", f"{parent}_create_inherits", cwd=proj,
    )  # fmt: skip
    cfg = C.load(proj)
    for obj, _, spec in objs:
        spec.author(cfg, obj, _marker(obj))
    C.save(proj, cfg)
    for obj, _, spec in objs:
        (h,) = (proj / "native" / "inc").rglob(f"{obj}_core.h")
        h.write_text(
            spec.header(h.read_text("utf-8"), obj, _marker(obj)), "utf-8"
        )
    _ok("apply", cwd=proj)
    for cmd in (
        ["cmake", "-S", ".", "-B", "build", PYTHON3_ARG],
        ["cmake", "--build", "build"],
    ):
        r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True)
        assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-4000:]
    cfg = C.load(proj)
    classes = [(mod, C.resolved_class_name(cfg, obj)) for obj, mod, _ in objs]
    classes.append(("p.dsp", "Inherits"))
    r = subprocess.run(
        [sys.executable, "-c", _PROBE, json.dumps(classes)],
        cwd=proj,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    docs = json.loads(r.stdout)
    stubs = {
        "p": {obj: (proj / "src/p" / f"{obj}.pyi") for obj, m, _ in objs},
    }
    out = {}
    for obj, mod, _ in objs:
        cls = C.resolved_class_name(cfg, obj)
        stub = (
            stubs["p"][obj] if mod == "p" else proj / "src/p/dsp/dsp.pyi"
        ).read_text("utf-8")
        out[obj] = (stub, docs[cls])
    out["Inherits"] = (
        (proj / "src/p/dsp/dsp.pyi").read_text("utf-8"),
        docs["Inherits"],
    )
    return out


@pytest.mark.parametrize("kind", ["s", "m"])
@pytest.mark.parametrize("case", sorted(CASES))
def test_a_doc_the_stub_shows_reaches_the_runtime(
    built: dict, case: str, kind: str
) -> None:
    obj = _obj(kind, case)
    stub, runtime = built[obj]
    marker = _marker(obj)
    assert marker in stub, f"{obj}: the authored doc never reached the .pyi"
    assert marker in (runtime or ""), (
        f"{obj}: `{case}` reaches the .pyi and not `help()`:\n{runtime}"
    )


def test_a_view_inheriting_the_constructor_shows_its_param_docs(
    built: dict,
) -> None:
    """gh-1177's inheritance, on the runtime face, for a MANIFEST param doc."""
    marker = _marker(_obj("m", "init_params.doc"))
    stub, runtime = built["Inherits"]
    assert marker in (runtime or ""), runtime


def test_an_edited_param_doc_refreshes_a_sacred_fragment(
    tmp_path: Path,
) -> None:
    """`_docsync` replaces a sacred fragment's ``tp_doc`` on the render's own
    predicate. Asked of a second, narrower reading of the manifest (the
    object's ``doc`` alone), a fragment holding jm's block for an OLD param
    doc matched neither the new render nor its fallback, read as
    hand-written, and kept the old text while the ``.pyi`` moved on."""
    _ok("new", "p", "--no-c-prefix", cwd=tmp_path)
    proj = tmp_path / "p"
    _ok("module", "dsp", cwd=proj)
    _ok("object", "m", "--module", "dsp", *_PARAM, cwd=proj)
    frag = proj / "native/src/dsp/dsp_ext_m.c"
    for marker in ("MARKER_OLD_DOC", "MARKER_NEW_DOC"):
        cfg = C.load(proj)
        cfg["m"]["init_params"][0]["doc"] = marker
        C.save(proj, cfg)
        _ok("apply", cwd=proj)
        assert marker in frag.read_text("utf-8"), marker
    assert "MARKER_OLD_DOC" not in frag.read_text("utf-8")


def test_bind_renders_a_documented_param_without_a_brief(
    tmp_path: Path,
) -> None:
    """`jm bind`'s runtime ``tp_doc`` asks the same predicate: a header
    documenting a constructor parameter, and no ``@brief``, is authored.
    Gated on the brief alone, the `.pyi` bind wrote showed the parameter's
    text and ``help()`` did not."""
    _ok("new", "p", "--no-c-prefix", cwd=tmp_path)
    proj = tmp_path / "p"
    _ok("object", "o", *_FLAG, *_PARAM, cwd=proj)
    cfg = C.load(proj)
    del cfg["o"]  # bind is for a header the manifest does not declare
    C.save(proj, cfg)
    (h,) = (proj / "native" / "inc").rglob("o_core.h")
    decl = "o_state_t *o_create("
    text = h.read_text("utf-8")
    assert text.count(decl) == 1, text
    doc = "/**\n * @param mode  MARKER_BIND_PARAM_DOC\n */\n"
    h.write_text(text.replace(decl, doc + decl, 1), "utf-8")
    ext = proj / "native/src/o/o_ext.c"
    ext.unlink()
    _ok("bind", "o", cwd=proj)
    assert "MARKER_BIND_PARAM_DOC" in (proj / "src/p/o.pyi").read_text("utf-8")
    assert "MARKER_BIND_PARAM_DOC" in ext.read_text("utf-8")
