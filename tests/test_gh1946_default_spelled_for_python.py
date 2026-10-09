"""gh-1946 / gh-1947: a declared default is one value, spelled for Python.

A default has two spellings: C's, which the manifest holds and the C faces
write, and Python's, which the generated test, the stub's signature, its
construction doctest and the runtime docstring write. Two issues found the
Python one wrong:

- gh-1946: ``jm new p --object g --state a:double:0.1L`` scaffolded a test,
  a stub and a doctest that were all SyntaxErrors. Both ``_py_default``
  peers stripped only ``f``/``F`` from a float default, so ``0.1L`` reached
  Python verbatim. Measuring the class found more of the same: a hex
  default for a float field became ``0x10.0``, an octal ``010`` stayed
  ``010`` (a SyntaxError for an int, the wrong value -- 10.0, not 8.0 -- for
  a float), ``0.1f`` in a ``double`` field holds 0.10000000149011612 while
  every Python face said ``0.1``, and ``1.5`` for an ``int`` -- which C
  converts and Python refuses -- was accepted at declaration.
- gh-1947: a ``float _Complex`` default's stub doctest printed the declared
  ``(0.1+0j)`` where the getter returns ``(0.10000000149011612+0j)``, so a
  fresh scaffold's ``pytest --doctest-glob='*.pyi'`` failed.

The Python spelling is now ``_context._types._py_default``'s answer on every
face (the module stub generator's peer delegates to it), reading the literal
through ``_types`` -- the one place a C suffix is read -- and the doctest
expectation is ``_types.held_default_py``, the value the field HOLDS. A
literal Python has no spelling of is refused where it is declared.

GATE: every scalar type in the vocabulary, given a default in each C form
      jm accepts, scaffolds a project -- standalone and in a module -- whose
      generated tests and stubs parse and pass, whose stub doctests pass,
      whose every getter returns what the doctest writer says it does, and
      whose every Python spelling stores what the C default stores; a form
      Python has no spelling of is refused where it is declared, on the
      command line and in a manifest; and no module strips a C suffix itself.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc

from _jmrun import run_cli
from just_makeit import _stubs
from just_makeit import _types as T
from just_makeit._context._types import _py_default

PKG = Path(__file__).resolve().parent.parent / "src" / "just_makeit"

#: The C spellings jm accepts for a default, per kind: suffix families in
#: both cases and orders, hex, octal, and each shape of a floating literal.
#: The TYPES are the vocabulary's; these are C's grammar.
FORMS = {
    "int": ("7", "7U", "7LL", "7lu", "0x1F", "0X1fU", "017", "00"),
    "float": (
        "0.1",
        "0.1f",
        "0.1L",
        "2",
        "2U",
        "1e-3",
        "1E3f",
        ".5",
        "2.",
        "0x10",
        "017",
        "-0.25",
    ),
    "complex": (
        "0.1",
        "0.1f + 0.2f * I",
        "0.1L - 0.2L * I",
        "2.0 * I",
        "-1.5",
        "017 + 0x1 * I",
    ),
}

#: A signed integer type takes a negative literal too.
NEGATIVE = ("-7", "-0x1F")

#: Literals Python has no spelling of, for the type beside each.
REFUSED = (
    ("int", "1.5"),
    ("size_t", "1e3"),
    ("int", "08"),
    ("double", "09"),
    ("double _Complex", "08 * I"),
)

#: gh-1947's trigger, leading a MODULE object so its stub's doctest shows it.
MODULE_STATES = (
    ("z", "float _Complex", "0.1"),
    ("f", "float", "0.1"),
    ("w", "double _Complex", "0.1f - 0.2L * I"),
)


def _forms(ct: str) -> "tuple[str, ...]":
    """Every C spelling of a default the gate declares for *ct*."""
    if ct == "bool":
        return T.BOOL_LITERALS
    meta = T._CTYPE_META[ct]
    forms = FORMS[meta["kind"]]
    if meta["kind"] == "int" and not meta["py_type"].startswith("np.uint"):
        forms += NEGATIVE
    return forms


#: Every scalar type a state field may carry, from the vocabulary. Complex
#: first, so the standalone stub's doctest (it shows three getters) leads
#: with a value that rounds.
SCALARS = sorted(
    (ct for ct, m in T._CTYPE_META.items() if m["kind"] in FORMS),
    key=lambda ct: (T._CTYPE_META[ct]["kind"] != "complex", ct),
)

FIELDS = [
    (f"v{i}", ct, form)
    for i, (ct, form) in enumerate(
        (ct, form) for ct in SCALARS for form in _forms(ct)
    )
]


def _no_toolchain() -> "str | None":
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    return None


def test_the_vocabulary_is_covered():
    """Never vacuous: every kind, and both float precisions, are in it."""
    kinds = {T._CTYPE_META[ct]["kind"] for ct in SCALARS}
    assert kinds == set(FORMS), kinds
    for ct in ("bool", "float", "double", "float _Complex", "uint64_t"):
        assert ct in SCALARS, SCALARS
    assert len(FIELDS) > 100, len(FIELDS)


#: Run inside the built project: the stub doctests, then every getter of a
#: default-constructed object and of one constructed with the Python
#: spellings. The last line of output is the JSON the test reads.
PROBE = """
import ast, doctest, json, sys
from p import G
from p.m import H

spec = json.loads(sys.argv[1])
failed = 0
for path in spec["pyi"]:
    failed += doctest.testfile(path, module_relative=False).failed
out = {}
for cls, spelled in ((G, spec["g"]), (H, spec["h"])):
    plain = cls()
    given = cls(**{n: ast.literal_eval(v) for n, v in spelled.items()})
    out[cls.__name__] = {
        n: [repr(getattr(plain, "get_" + n)()),
            repr(getattr(given, "get_" + n)())]
        for n in spelled
    }
print(json.dumps({"failed": failed, "held": out}))
"""


@pytest.mark.skipif(bool(_no_toolchain()), reason=str(_no_toolchain()))
def test_every_form_of_every_type_is_one_value_on_every_face(tmp_path):
    """One scaffold holding every field, one build, every face checked."""
    states = [a for n, ct, f in FIELDS for a in ("--state", f"{n}:{ct}:{f}")]
    r = run_cli("new", "p", "--object", "g", *states, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    proj = tmp_path / "p"
    r = run_cli("module", "m", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    mstates = [
        a for n, ct, f in MODULE_STATES for a in ("--state", f"{n}:{ct}:{f}")
    ]
    r = run_cli("object", "h", "--module", "m", *mstates, cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr

    pyis = [proj / "src/p/g.pyi", proj / "src/p/m/m.pyi"]
    for pyi in pyis:
        ast.parse(pyi.read_text("utf-8"), str(pyi))
    # Armed: each stub demonstrates a getter whose value rounds.
    for pyi in pyis:
        assert "0.10000000149011612" in pyi.read_text("utf-8"), pyi

    # Both generated suites, against the one build.
    r = run_cli("test", cwd=proj)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "100% tests passed" in out, out
    assert re.search(r"\b\d+ passed\b", out), out

    spec = {
        "pyi": [str(p) for p in pyis],
        "g": {n: _py_default(ct, f) for n, ct, f in FIELDS},
        "h": {n: _py_default(ct, f) for n, ct, f in MODULE_STATES},
    }
    probe = subprocess.run(
        [sys.executable, "-c", PROBE, json.dumps(spec)],
        capture_output=True,
        text=True,
        cwd=proj,
        env={**os.environ, "PYTHONPATH": str(proj / "src")},
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    result = json.loads(probe.stdout.strip().splitlines()[-1])
    assert result["failed"] == 0, probe.stdout

    wrong = []
    for cls, fields in (("G", FIELDS), ("H", MODULE_STATES)):
        for name, ct, form in fields:
            plain, given = result["held"][cls][name]
            # The doctest writer's answer is what the getter returns.
            if T.held_default_py(ct, form) != plain:
                wrong.append(
                    (ct, form, "held", T.held_default_py(ct, form), plain)
                )
            # The Python spelling stores exactly what the C default stores.
            if given != plain:
                wrong.append(
                    (ct, form, "spelled", _py_default(ct, form), given, plain)
                )
            # And a getter the stub demonstrates prints that value.
            shown = _stubs._doctest_out(ct, form)
            if shown is not None and shown != plain:
                wrong.append((ct, form, "doctest", shown, plain))
    assert not wrong, "\n".join(map(repr, wrong))


@pytest.mark.parametrize("ct, form", REFUSED)
def test_a_default_python_cannot_spell_is_refused_where_declared(
    tmp_path, ct, form
):
    """The command line refuses it before writing; a manifest, on apply."""
    r = run_cli(
        "new", "p", "--object", "g", "--state", f"v:{ct}:{form}", cwd=tmp_path
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"error: state field 'v': default `{form}`" in r.stderr, r.stderr
    assert not (tmp_path / "p").exists()

    r = run_cli(
        "new", "p", "--object", "g", "--state", f"v:{ct}:3", cwd=tmp_path
    )
    assert r.returncode == 0, r.stdout + r.stderr
    proj = tmp_path / "p"
    frag = proj / "objects" / "g.toml"
    text = frag.read_text("utf-8")
    assert text.count('default = "3"') == 1, text
    frag.write_text(
        text.replace('default = "3"', f'default = "{form}"'), "utf-8"
    )
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"state field 'v': default `{form}`" in r.stderr, r.stderr


def test_every_declaring_verb_refuses_it(tmp_path):
    """An init-param, a method param and a function param, as the state."""
    r = run_cli("new", "p", "--object", "g", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    proj = tmp_path / "p"
    before = sorted(p.read_text("utf-8") for p in proj.rglob("*.toml"))
    for argv, said in (
        (("object", "k", "--init-param", "n:int:1.5"), "init_param 'n'"),
        (("method", "g", "m", "--param", "k:int=08"), "param 'k'"),
        (("function", "f", "--param", "n:int=1e3"), "param 'n'"),
    ):
        r = run_cli(*argv, cwd=proj)
        assert r.returncode == 1, (argv, r.stdout + r.stderr)
        assert f"error: {said}: default `" in r.stderr, (argv, r.stderr)
    assert sorted(p.read_text("utf-8") for p in proj.rglob("*.toml")) == before

    # A method param in a manifest has no check of its own: the one
    # function every Python face reads refuses it.
    r = run_cli("method", "g", "m", "--param", "k:int=3", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    frag = proj / "objects" / "g.toml"
    text = frag.read_text("utf-8")
    assert text.count('default = "3"') == 1, text
    frag.write_text(text.replace('default = "3"', 'default = "08"'), "utf-8")
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "error: default `08` is not a C number" in r.stderr, r.stderr


def _suffix_strips(source: str) -> "list[int]":
    """Lines of *source* that strip C suffix letters off a string by hand.

    >>> _suffix_strips('s = default.rstrip("fF")\\n')
    [1]
    >>> _suffix_strips('s = name.rstrip("_")\\n')
    []
    """
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("strip", "rstrip")
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and node.args[0].value
        and set(node.args[0].value) <= set("uUlLfF")
    ]


def test_no_module_strips_a_c_suffix_itself():
    """gh-1946's cause, refused: a suffix read anywhere but the grammar.

    Both ``_py_default`` peers and the doctest writer each stripped ``fF``
    by hand, so ``L`` was the suffix none of them knew. ``_types`` reads a
    literal with ``_C_NUMERIC_LITERAL`` and nothing strips one by hand.
    """
    found = {
        str(p.relative_to(PKG)): lines
        for p in sorted(PKG.rglob("*.py"))
        if p.relative_to(PKG).parts[0] not in ("templates", "examples")
        and (lines := _suffix_strips(p.read_text("utf-8")))
    }
    assert not found, found
