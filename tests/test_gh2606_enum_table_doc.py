"""gh-2606: a published ``[[enum]]`` table is documented in the ``.pyi``.

gh-2344 publishes each ``[[enum]]`` a binding indexes as a module attribute
(``ENG_STATE: tuple[str, ...]`` or ``LVL: dict[int, str]``), but the stub
declared it as a bare annotation. A documentation tool such as mkdocstrings
hides an attribute that has no docstring, so a downstream API reference
silently left every published table out (doppler: 14 of them).

GATE:

* an ``[[enum]]`` row's ``doc`` key is the attribute docstring written right
  after its annotation, on both ``.pyi`` generators (a standalone object's
  template and the module stub, ``_stubs``);
* without ``doc``, the docstring is a generated sentence for the reader of
  an API reference -- the enum's name, how a value is looked up, and for a
  dict the C constants it is keyed by, with no manifest syntax -- so no
  published table is ever undocumented. That is asserted registration-free:
  EVERY published annotation in every ``.pyi`` of the project must be
  followed by a docstring;
* text that would end the literal -- a triple quote, a backslash, a final
  quote -- in a ``doc`` or a property's header ``@brief`` still gives a
  ``.pyi`` that parses, and the docstring reads back as the text
  (``_docstring.summary_docstring``, shared by both). The peer layouts are
  held to the triple quote and an unsafe close: a manifest property
  ``doc`` (``authored_docstring``), the getter method's numpy docstring
  (``render_numpy_doc``) and the class docstring (``class_docstring``);
* the key is recognised by ``_keys`` (a misspelling is reported), and
  ``jm script``, which replays the ``[[enum]]`` rows verbatim, carries it.
  ``tests/test_gh2045_dump_keeps_every_key.py`` holds the manifest writer to
  the ``enum`` vocabulary for every key and layout.

The runtime value is a plain ``tuple`` / ``dict``, which cannot carry a
``__doc__`` of its own; the ``.pyi`` is the face documentation tools read.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _keys

DOC = "Engine state code -> name, as Eng.mode reports it."

#: A published annotation, as `_enumc.publish_pyi` writes one.
_ANNOT = re.compile(
    r"^([A-Z][A-Z0-9_]*): (tuple\[str, \.\.\.\]|dict\[int, str\])$"
)


def _enum_rows(doc: str | None) -> str:
    """A dense enum (``eng_state``) and a constant-bound one (``lvl``)."""
    dense = ["[[enum]]", 'name = "eng_state"', 'values = ["idle", "run"]']
    if doc is not None:
        dense.append(f"doc = {json.dumps(doc)}")
    keyed = [
        "[[enum]]",
        'name = "lvl"',
        'values = ["lo", "hi"]',
        'enumerators = ["LVL_LO", "LVL_HI"]',
    ]
    return "\n" + "\n".join(dense) + "\n\n" + "\n".join(keyed) + "\n"


def _project(tmp_path: Path, *, module: bool, doc: str | None) -> Path:
    """A project whose object(s) decode both enums through properties."""
    root = tmp_path / "proj"
    assert run_cli("new", "proj", cwd=tmp_path).returncode == 0
    manifest = root / "just-makeit.toml"
    manifest.write_text(manifest.read_text() + _enum_rows(doc))
    mod = ["--module", "m"] if module else []
    if module:
        assert run_cli("module", "m", cwd=root).returncode == 0
    r = run_cli("object", "eng", *mod, "--state", "mode:int:0", cwd=root)
    assert r.returncode == 0, r.stderr
    for prop, enum in (("mode", "eng_state"), ("lv", "lvl")):
        r = run_cli(
            "property", "eng", prop, *mod, "--type", "int", "--enum", enum,
            cwd=root,
        )  # fmt: skip
        assert r.returncode == 0, r.stderr
    return root


def _published(root: Path) -> dict[str, tuple[str, str]]:
    """Every published annotation in the project's stubs, with what follows
    it: ``{attr: (annotation, docstring)}``. The docstring is the whole
    ``\"\"\"...\"\"\"`` block, joined to one line (a long one is wrapped),
    or ``""`` when the next line does not open one."""
    out: dict[str, tuple[str, str]] = {}
    for pyi in (root / "src").rglob("*.pyi"):
        lines = pyi.read_text().splitlines() + [""]
        for i, line in enumerate(lines):
            m = _ANNOT.match(line)
            if not m:
                continue
            first, doc = lines[i + 1], ""
            if first.startswith('"""'):
                # A one-liner closes on its own line; a wrapped one
                # (`summary_docstring`) on a later line of its own.
                j, doc = i + 1, first
                while len(doc) < 6 or not doc.endswith('"""'):
                    j += 1
                    doc = doc + " " + lines[j].strip()
                doc = doc.replace(' """', '"""')
            out[m.group(1)] = (m.group(2), doc)
    return out


@pytest.mark.parametrize("module", [False, True], ids=["object", "module"])
def test_a_declared_doc_is_the_attribute_docstring(tmp_path, module):
    pub = _published(_project(tmp_path, module=module, doc=DOC))
    assert pub["ENG_STATE"] == ("tuple[str, ...]", f'"""{DOC}"""')


@pytest.mark.parametrize("module", [False, True], ids=["object", "module"])
def test_without_doc_the_docstring_names_the_enum_and_shape(tmp_path, module):
    pub = _published(_project(tmp_path, module=module, doc=None))
    dense, keyed = pub["ENG_STATE"][1], pub["LVL"][1]
    assert dense == '"""Names of the eng_state values, indexed by value."""'
    assert keyed == (
        '"""Names of the lvl values, keyed by code (LVL_LO .. LVL_HI)."""'
    )


@pytest.mark.parametrize("doc", [DOC, None], ids=["doc", "default"])
@pytest.mark.parametrize("module", [False, True], ids=["object", "module"])
def test_no_published_table_is_undocumented(tmp_path, module, doc):
    pub = _published(_project(tmp_path, module=module, doc=doc))
    assert set(pub) == {"ENG_STATE", "LVL"}
    bare = [a for a, (_t, nxt) in pub.items() if not nxt.startswith('"""')]
    assert not bare, f"published with no docstring: {bare}"


def test_the_doc_survives_apply_and_status(tmp_path):
    root = _project(tmp_path, module=False, doc=None)
    manifest = root / "just-makeit.toml"
    text = manifest.read_text()
    manifest.write_text(
        text.replace(
            'values = ["idle", "run"]',
            f'values = ["idle", "run"]\ndoc = "{DOC}"',
        )
    )
    assert run_cli("apply", cwd=root).returncode == 0
    assert _published(root)["ENG_STATE"][1] == f'"""{DOC}"""'
    assert run_cli("status", "--check", cwd=root).returncode == 0


def test_jm_script_replays_the_doc(tmp_path):
    root = _project(tmp_path, module=False, doc=DOC)
    r = run_cli("script", cwd=root)
    assert r.returncode == 0, r.stderr
    assert f'doc = "{DOC}"' in r.stdout


def test_doc_is_a_known_enum_key_and_a_misspelling_is_reported():
    cfg = {
        "project": {"name": "p", "version": "0.1.0"},
        "enum": [
            {"name": "a", "values": ["x"], "doc": "ok"},
            {"name": "b", "values": ["x"], "docs": "typo"},
        ],
    }
    found = [(u.kind, u.key) for u in _keys.unknown_keys(cfg)]
    assert found == [("enum", "docs")]
    assert C.enums(cfg)["a"].doc == "ok"


#: Text that ends a docstring literal early unless it is escaped: a triple
#: quote, a backslash (a lone one before the closer escapes it), a final
#: quote that merges with the closer.
HOSTILE = [
    'Say """hi""" to it.',
    "Path C:" + chr(92) + "tmp" + chr(92) + "n, not a newline",
    'Ends in a "quote"',
    "Ends in a backslash " + chr(92),
]


def _attr_docs(pyi: Path) -> dict[str, str]:
    """``{name: docstring}`` for each module attribute and each class
    property in *pyi*, read by ``ast`` -- so the stub must parse."""
    tree = ast.parse(pyi.read_text())
    out: dict[str, str] = {}
    body = list(tree.body)
    for i, node in enumerate(body):
        nxt = body[i + 1] if i + 1 < len(body) else None
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(nxt, ast.Expr)
            and isinstance(nxt.value, ast.Constant)
        ):
            out[node.target.id] = nxt.value.value
        if isinstance(node, ast.ClassDef):
            for fn in node.body:
                if isinstance(fn, ast.FunctionDef):
                    doc = ast.get_docstring(fn, clean=False)
                    out.setdefault(f"{node.name}.{fn.name}", doc or "")
    return out


@pytest.mark.parametrize("text", HOSTILE)
def test_a_doc_that_would_end_the_literal_still_parses(tmp_path, text):
    root = _project(tmp_path, module=False, doc=text)
    docs = _attr_docs(root / "src" / "proj" / "eng.pyi")
    assert docs["ENG_STATE"] == text.strip()
    assert run_cli("status", "--check", cwd=root).returncode == 0


@pytest.mark.parametrize("text", HOSTILE)
def test_a_property_brief_that_would_end_the_literal_still_parses(
    tmp_path, text
):
    root = _project(tmp_path, module=False, doc=None)
    hdr = root / "native" / "inc" / "proj" / "eng" / "eng_core.h"
    src = hdr.read_text()
    old = "@brief Get current mode."
    assert src.count(old) == 1
    hdr.write_text(src.replace(old, "@brief " + text))
    assert run_cli("apply", cwd=root).returncode == 0
    docs = _attr_docs(root / "src" / "proj" / "eng.pyi")
    assert docs["Eng.mode"] == text.strip()


@pytest.mark.parametrize("text", HOSTILE)
def test_an_authored_property_doc_that_would_end_the_literal_still_parses(
    tmp_path, text
):
    # A manifest `doc` is laid out as written (`authored_docstring`,
    # gh-1493), and reads back as written: a backslash too.
    root = _project(tmp_path, module=False, doc=None)
    r = run_cli(
        "property", "eng", "gain", "--type", "double", "--doc", text,
        cwd=root,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    docs = _attr_docs(root / "src" / "proj" / "eng.pyi")
    assert docs["Eng.gain"] == text.strip()


def test_a_class_brief_holding_a_triple_quote_still_parses(tmp_path):
    # The class docstring's multi-line layout (`class_docstring`): the
    # create() `@brief` is its summary, above its Parameters section.
    root = _project(tmp_path, module=False, doc=None)
    hdr = root / "native" / "inc" / "proj" / "eng" / "eng_core.h"
    src = hdr.read_text()
    old = "@brief Create a eng instance."
    assert src.count(old) == 1
    hdr.write_text(src.replace(old, "@brief " + HOSTILE[0]))
    assert run_cli("apply", cwd=root).returncode == 0
    tree = ast.parse((root / "src" / "proj" / "eng.pyi").read_text())
    (cls,) = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    assert (ast.get_docstring(cls) or "").startswith(HOSTILE[0])
