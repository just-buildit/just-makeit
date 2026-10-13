"""gh-2185 / gh-2234: the state triplet's prose is true for every object.

`get_state` said "Construction parameters are not included: restore into
an object built the same way", and `set_state` "the object keeps the
parameters it was constructed with". Both are false for a core whose blob
carries configuration as reject keys (doppler's Spectrogram writes its
mode, nfft, hop and window, and refuses a blob built differently), and for
one whose blob carries a setter's value (doppler's AccTrace `alpha`, under
doppler#2022's "a setter's value is state"). jm renders one text for every
`serializable` object and cannot see which kind a core is, so the text says
what holds for all of them: the maintainer's option 1 on #2185.

One definition (`_gluedoc._serialization`) feeds every face. This reads the
FACES, found by walking a generated project for every `get_state` /
`set_state` docstring: the runtime `PyMethodDef` rows, a module object's
binding, the standalone and the module-aggregated `.pyi`. A face that
renders its own text is held to the same wording.

GATE: no face of the state triplet claims construction parameters are left
      out of the blob or kept by `set_state`; every face says they are not
      restored, that a setter's value is captured, and that a core may refuse
      a blob built differently.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from _jmrun import run_cli

#: The claims gh-2185 / gh-2234 are about. Each is false for some object.
FALSE = (
    "Construction parameters are not included",
    "keeps the parameters it was constructed with",
)

#: What every face must say instead, per method.
REQUIRED = {
    "get_state": (
        "including any value a setter changed after construction",
        "Construction parameters are not restored",
        "may check some of them and refuse a blob built differently",
    ),
    "set_state": (
        "including any value a setter changed after construction",
        "Construction parameters are not restored",
        "refuse a blob built differently",
    ),
}


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    where = tmp_path_factory.mktemp("gh2185")
    root = where / "p"
    steps = [
        ("new", "p", "--no-c-prefix"),
        ("object", "w", "--state", "g:double:1.0", "--serializable"),
        ("module", "m"),
        (
            "object",
            "o",
            "--module",
            "m",
            "--state",
            "g:double:1.0",
            "--serializable",
        ),
    ]
    for step in steps:
        r = run_cli(*step, cwd=where if step[0] == "new" else root)
        assert r.returncode == 0, f"{step}: {r.stdout}{r.stderr}"
    return root


_ROW = re.compile(r'\{"(get_state|set_state)",')
_C_DOC_LINE = re.compile(r'^\s*"(.*)"[,}\s]*$')


def _c_faces(path: Path) -> "list[tuple[str, str]]":
    """``(method, doc)`` for each triplet row of a ``PyMethodDef`` table."""
    lines = path.read_text(encoding="utf-8").splitlines()
    out = []
    for i, line in enumerate(lines):
        m = _ROW.search(line)
        if not m:
            continue
        lits = []
        for raw in lines[i + 1 :]:
            lit = _C_DOC_LINE.match(raw)
            if not lit:
                break
            lits.append(lit.group(1))
        out.append(
            (m.group(1), "".join(lits).encode().decode("unicode_escape"))
        )
    return out


def _pyi_faces(path: Path) -> "list[tuple[str, str]]":
    """``(method, doc)`` for each triplet member of a stub's classes."""
    out = []
    for cls in ast.parse(path.read_text(encoding="utf-8")).body:
        if not isinstance(cls, ast.ClassDef):
            continue
        for fn in cls.body:
            if isinstance(fn, ast.FunctionDef) and fn.name in REQUIRED:
                out.append((fn.name, ast.get_docstring(fn) or ""))
    return out


@pytest.fixture(scope="module")
def faces(project) -> "list[tuple[str, str, str]]":
    """``(face, method, doc with whitespace collapsed)``, every face."""
    found = []
    for path in sorted(project.rglob("*")):
        if path.suffix == ".c":
            pairs = _c_faces(path)
        elif path.suffix == ".pyi":
            pairs = _pyi_faces(path)
        else:
            continue
        rel = path.relative_to(project).as_posix()
        found += [(rel, meth, " ".join(doc.split())) for meth, doc in pairs]
    return found


def test_both_kinds_of_face_are_found_for_both_objects(faces):
    """Armed: a runtime and a stub face of each method, for the standalone
    object and the module object alike."""
    for meth in REQUIRED:
        rels = [rel for rel, m, _ in faces if m == meth]
        for kind in (".c", ".pyi"):
            assert len([r for r in rels if r.endswith(kind)]) >= 2, (
                meth,
                kind,
                rels,
            )


def test_no_face_claims_construction_parameters_never_travel(faces):
    bad = [
        f"{rel} {meth}: {claim!r}"
        for rel, meth, doc in faces
        for claim in FALSE
        if claim in doc
    ]
    assert bad == [], "\n".join(bad)


def test_every_face_says_what_holds_for_every_object(faces):
    missing = [
        f"{rel} {meth}: {phrase!r}"
        for rel, meth, doc in faces
        for phrase in REQUIRED[meth]
        if phrase not in doc
    ]
    assert missing == [], "\n".join(missing)
