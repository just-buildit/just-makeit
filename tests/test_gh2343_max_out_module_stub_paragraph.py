"""gh-2343: a module stub's `*_max_out` keeps its header paragraph whole.

A `*_max_out` whose header Doxygen carries a wrapped prose paragraph rendered
in the module-aggregated `.pyi` with a blank line after every SOURCE line, so
a six-line paragraph became six one-line paragraphs. The runtime face (the
`PyMethodDef` doc in the fragment) was right, and so was the standalone
object's stub, because that one goes through `_context/_methods._max_out_doc`.

The cause: `_stubs.py` re-derived the same `max_out` doc inline and swapped
the header block in with a bare `dataclasses.replace(gm, block=...)`. That
skipped `GlueMethod.with_header_block`, which is the one place that records
the block's body as LINES, so `GlueMethod._spaced` still blank-separated
them. gh-1052 fixed exactly this on the standalone path and left this copy.

Two gates:

- the behaviour, driven through ``run_cli`` on a module object, the path the
  report came from;
- the shape, derived from the source: no ``replace(..., block=...)`` anywhere
  in ``src/just_makeit`` outside ``with_header_block``, so the next copy of
  the substitution fails the suite instead of shipping.
"""

# gh-1591: this file's hand-written C spells jm's bare derived names, so its
# project opts out of the prefix `jm new` now defaults to.

from __future__ import annotations

import ast
import re
from pathlib import Path

import just_makeit
from _jmrun import run_cli
from _jminc import INC_ROOT

SRC = Path(just_makeit.__file__).parent

#: Three source lines of ONE paragraph, as an author wraps a Doxygen brief.
_PARA = [
    "Returns the worst case for a call that completes a code period carried",
    "over from earlier calls, so a caller sizing an out= buffer can allocate",
    "once and never resize it between calls.",
]
_PARA_TEXT = " ".join(_PARA)


def _module_project(tmp_path: Path) -> Path:
    """A module object whose ``encode_max_out`` header has a wrapped paragraph."""
    assert (
        run_cli("new", "demo", "--no-c-prefix", cwd=tmp_path).returncode == 0
    )
    root = tmp_path / "demo"
    for args in (
        ("module", "filter"),
        (
            "object",
            "enc",
            "--module",
            "filter",
            "--arg-type",
            "uint8_t[]",
            "--return-type",
            "uint8_t",
            "--state",
            "n:int:0",
        ),
        ("method", "enc", "encode", "--variable-output", "--module", "filter"),
    ):
        r = run_cli(*args, cwd=root)
        assert r.returncode == 0, (args, r.stderr)

    hdr = root / INC_ROOT / "enc" / "enc_core.h"
    text = hdr.read_text(encoding="utf-8")
    decl = "size_t enc_encode_max_out(enc_state_t *state, size_t n);"
    assert text.count(decl) == 1, "the scaffold's max_out declaration moved"
    block = (
        "/**\n"
        " * @brief The output capacity encode() needs for n input samples.\n"
        " *\n" + "".join(f" * {ln}\n" for ln in _PARA) + " *\n"
        " * @param state  Must be non-NULL.\n"
        " * @param n      Number of input samples the call will be given.\n"
        " * @return Largest number of output samples encode() can write.\n"
        " */\n"
    )
    hdr.write_text(text.replace(decl, block + decl), encoding="utf-8")

    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stderr
    return root


def _max_out_doc(root: Path) -> str:
    text = (root / "src/demo/filter/filter.pyi").read_text(encoding="utf-8")
    m = re.search(
        r'    def encode_max_out\(.*?\) -> int:\n        """(.*?)\n        """',
        text,
        re.S,
    )
    assert m, "no module-stub docstring for encode_max_out()"
    return m.group(1)


def _paragraphs(doc: str) -> list[str]:
    """The docstring's paragraphs above ``Parameters``, stripped."""
    body = doc.split("Parameters")[0]
    return [p.strip() for p in body.split("\n\n") if p.strip()]


class TestModuleStubMaxOut:
    """The report, driven through the CLI on a module object."""

    def test_the_wrapped_paragraph_is_one_paragraph(self, tmp_path):
        doc = _max_out_doc(_module_project(tmp_path))
        paras = _paragraphs(doc)
        # [0] is the @brief; the wrapped paragraph must be exactly one more.
        assert len(paras) == 2, f"{len(paras)} paragraphs:\n{doc}"
        assert " ".join(paras[1].split()) == _PARA_TEXT, doc


class TestTheShapeIsOwnedByOneSubstitution:
    """Derived from the source, so a new copy cannot slip past unlisted.

    ``GlueMethod.with_header_block`` is the one place a header block replaces
    a jm definition, and it sets the provenance flag. Any other
    ``replace(..., block=...)`` skips that flag and reintroduces gh-2343.
    """

    @staticmethod
    def _block_replacements():
        """``(file, enclosing function, line)`` of every block= replace call."""
        found = []
        for path in sorted(SRC.rglob("*.py")):
            # templates/ holds Python TEMPLATES (`<<Component>>`), not modules.
            if "templates" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(fn):
                    if not isinstance(node, ast.Call):
                        continue
                    f = node.func
                    name = (
                        f.id
                        if isinstance(f, ast.Name)
                        else f.attr
                        if isinstance(f, ast.Attribute)
                        else ""
                    )
                    if name.lstrip("_") != "replace":
                        continue
                    if any(k.arg == "block" for k in node.keywords):
                        found.append((path.name, fn.name, node.lineno))
        return found

    def test_the_source_has_one_block_substitution(self):
        found = self._block_replacements()
        assert found, "no replace(..., block=...) found: the scan is blind"
        stray = [
            f for f in found if f[:2] != ("_gluedoc.py", "with_header_block")
        ]
        assert not stray, (
            "a header block replaces a jm definition without the provenance "
            f"flag; use GlueMethod.with_header_block: {stray}"
        )
