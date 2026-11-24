"""A record's field docs come from the blocks its C descriptor reads (gh-2755).

gh-2755 gave a module function a single record, and with it read the module
header's struct member docs. The first cut merged those into the one doc
map every record class in the module stub was rendered from, while each C
descriptor kept reading its own face's blocks: a method's its object's
header, a function's the module header. So a struct documented in the
"other" header documented its record in the ``.pyi`` and nowhere in
``help()``:

* A -- a method record whose struct is documented in the MODULE header: the
  stub gained ``Attributes`` its fragment's ``{"a", NULL}`` never had, and
  ``jm apply`` rewrote the stub of every existing project shaped so;
* B -- a function record whose struct is documented in an OBJECT header:
  the stub showed the doc, the C did not, and ``jm status --docs`` reported
  a gap the stub contradicted.

GATE. Both shapes, rendered: every field's doc in the C descriptor equals
its doc in the stub's record class, and the method record's class is the
one main renders -- its synopsis, no ``Attributes``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from _jmrun import run_cli

#: ``mrec_t`` and ``frec_t`` are documented in the header their record does
#: NOT read; ``grec_t``, a function's, in the one it does.
MODULE_H_STRUCT = """\
typedef struct {
  double a;  /**< Field a of mrec. */
  double b;  /**< Field b of mrec. */
} mrec_t;

typedef struct {
  double g;  /**< Field g of grec. */
} grec_t;

"""
OBJECT_H_STRUCT = """\
typedef struct {
  double c;  /**< Field c of frec. */
} frec_t;

"""

_FIELDS = re.compile(
    r"static PyStructSequence_Field (\w+)_fields\[\] = \{\n(.*?)\};", re.S
)
_ROW = re.compile(r'\{"(\w+)", (NULL|"(?:[^"\\]|\\.)*")\}')


def _c_docs(text: str) -> "dict[str, dict[str, str]]":
    """``{sid: {field: doc}}`` of every structseq descriptor in *text*."""
    return {
        sid: {
            name: "" if doc == "NULL" else doc[1:-1]
            for name, doc in _ROW.findall(body)
        }
        for sid, body in _FIELDS.findall(text)
    }


def _insert(path: Path, text: str) -> None:
    src = path.read_text("utf-8")
    anchor = '#ifdef __cplusplus\nextern "C" {\n#endif\n'
    assert anchor in src, src
    path.write_text(src.replace(anchor, anchor + "\n" + text, 1), "utf-8")


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("gh2755docs")
    assert run_cli("new", "p", "--no-c-prefix", cwd=base).returncode == 0
    dest = base / "p"
    for argv in (
        ("module", "m"),
        ("object", "fir", "--module", "m"),
        (
            "method", "fir", "meas", "--module", "m", "--return-type",
            "mrec_t", "--single", "--result-field", "a:double",
            "--result-field", "b:double",
        ),
        (
            "function", "frec", "--module", "m", "--return-type", "frec_t",
            "--single", "--result-field", "c:double", "--param", "x:double",
        ),
        (
            "function", "grec", "--module", "m", "--return-type", "grec_t",
            "--single", "--result-field", "g:double", "--param", "x:double",
        ),
    ):  # fmt: skip
        r = run_cli(*argv, cwd=dest)
        assert r.returncode == 0, r.stdout + r.stderr
    _insert(dest / "native/inc/p/m/m_core.h", MODULE_H_STRUCT)
    _insert(dest / "native/inc/p/fir/fir_core.h", OBJECT_H_STRUCT)
    r = run_cli("apply", cwd=dest)
    assert r.returncode == 0, r.stdout + r.stderr
    return dest


def _stub_class(pyi: str, name: str) -> str:
    start = pyi.index(f"class {name}(")
    end = pyi.find("\n@final", start)
    end = pyi.find("\ndef ", start) if end < 0 else end
    return pyi[start:end]


def _c(project: Path) -> "dict[str, dict[str, str]]":
    src = project / "native/src/m"
    return {
        **_c_docs((src / "m_ext_fir.c").read_text("utf-8")),
        **_c_docs((src / "m_ext.c").read_text("utf-8")),
    }


@pytest.mark.parametrize(
    ("record", "sid", "fields"),
    [
        ("Mrec", "Fir_meas", ("a", "b")),
        ("Frec", "_jm_fnrec_frec", ("c",)),
        ("Grec", "_jm_fnrec_grec", ("g",)),
    ],
    ids=[
        "A-method-struct-in-module-header",
        "B-fn-struct-in-object-header",
        "fn-struct-in-its-own-header",
    ],
)
def test_the_stub_documents_what_the_c_documents(project, record, sid, fields):
    c = _c(project)
    assert sid in c, sorted(c)
    cls = _stub_class((project / "src/p/m/m.pyi").read_text("utf-8"), record)
    for f in fields:
        doc = c[sid][f]
        # Whatever the C says, the stub says; where the C says nothing (its
        # header documents another struct), the stub may not either.
        if doc:
            assert doc in cls, (f, doc, cls)
        else:
            assert f"Field {f} of" not in cls, cls
    # Armed: the one record documented where it is read IS documented.
    assert (record == "Grec") == any(c[sid].values()), c[sid]


def test_the_method_record_reads_as_main_renders_it(project):
    cls = _stub_class((project / "src/p/m/m.pyi").read_text("utf-8"), "Mrec")
    assert '"""Mrec(a, b)"""' in cls, cls
    assert "Attributes" not in cls, cls


def test_a_second_apply_changes_nothing(project):
    pyi = project / "src/p/m/m.pyi"
    before = pyi.read_text("utf-8")
    r = run_cli("apply", cwd=project)
    assert r.returncode == 0, r.stdout + r.stderr
    assert pyi.read_text("utf-8") == before
