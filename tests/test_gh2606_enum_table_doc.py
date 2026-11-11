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
* without ``doc``, the docstring is a generated sentence naming the
  ``[[enum]]`` and the shape -- a dense tuple, or a dict keyed by the C
  constants it names -- so no published table is ever undocumented. That is
  asserted registration-free: EVERY published annotation in every ``.pyi``
  of the project must be followed by a docstring;
* the key is recognised by ``_keys`` (a misspelling is reported), and
  ``jm script``, which replays the ``[[enum]]`` rows verbatim, carries it.
  ``tests/test_gh2045_dump_keeps_every_key.py`` holds the manifest writer to
  the ``enum`` vocabulary for every key and layout.

The runtime value is a plain ``tuple`` / ``dict``, which cannot carry a
``__doc__`` of its own; the ``.pyi`` is the face documentation tools read.
"""

from __future__ import annotations

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
        dense.append(f'doc = "{doc}"')
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
    assert dense.startswith('"""') and dense.endswith('"""'), dense
    assert "[[enum]] 'eng_state'" in dense and "tuple" in dense, dense
    assert keyed.startswith('"""') and keyed.endswith('"""'), keyed
    assert "[[enum]] 'lvl'" in keyed and "dict" in keyed, keyed
    assert "LVL_LO" in keyed and "LVL_HI" in keyed, keyed


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
