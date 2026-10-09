"""gh-1884: a string is refused as an object's step() type, on every face.

``docs/types.md`` and the template pages have always said ``const char *``
cannot be a step input or output, and nothing enforced it. Both command-line
copies of the step-type check accepted any ``_CTYPE_META`` key, the manifest
check ``apply`` runs never looked at a component's own ``arg_type`` /
``return_type``, and every resulting scaffold was broken while jm exited 0:

- ``jm new p1 --object cc --arg-type S --return-type S`` failed its own
  ``make test`` (``dtype('O') != str``);
- ``jm object gg --preset generator --return-type S`` segfaulted the suite:
  the stub returns ``NULL`` and the binding calls ``PyUnicode_FromString``;
- ``jm object kk --preset consumer --arg-type S`` was green and passed each
  slot's ``PyObject *`` of an ``NPY_OBJECT`` array to C as a string. So did
  ``--arg-type 'S[]'``.

``_types.step_type_error`` is now the one answer, keyed on the registry's
``kind``: ``jm new`` and ``jm object`` raise it as a ``Refusal`` while
parsing, ``_config.manifest_type_errors`` reports it before ``apply`` (and
``status``) write anything, and ``make_sample_ctx`` refuses a registered
type of a refused kind for the renders that do not pass through ``apply``:
a mutating command over a hand-edited manifest, and ``jm bind`` reading a
header.

The string types are DERIVED from the registry (``kind == "str"``), not
listed, so a second string spelling registered later is covered here with
no edit.

GATE: every string-kind type, as a scalar and as a ``T[]`` element, is
      refused as an object's step type with exit 1 and one ``error:`` line
      naming it -- by ``jm new``, by ``jm object`` (the generator, consumer
      and blockwise presets, and a module object), by ``apply`` and
      ``status --check`` of a manifest (the three issue shapes and a module
      object), by ``jm method`` over a hand-edited manifest and by
      ``jm bind`` of a header -- with the tree byte-identical (the binding,
      for ``jm method``); every other registered type remains a legal step
      type, and a string stays legal as an init param and as a method's or
      function's param or return.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

try:
    import tomllib
except ImportError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _types as T

#: Every registered string type, read from the registry by its kind.
STR_TYPES = sorted(t for t, m in T._CTYPE_META.items() if m["kind"] == "str")

#: The words every refusal of a string step type carries.
REFUSED = "cannot be an object's step() type"


def test_the_registry_has_a_string_type():
    """The derivation is armed: an empty set would make every case vacuous."""
    assert STR_TYPES, "no `kind == 'str'` type in _CTYPE_META"
    assert "const char *" in STR_TYPES


def _snapshot(root: Path) -> "dict[str, bytes]":
    """Every file under *root*, and every directory as an empty marker."""
    out = {}
    for p in sorted(root.rglob("*")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(root).as_posix()
        out[rel] = p.read_bytes() if p.is_file() else b"<dir>"
    return out


def _run_ok(root: Path, *args: str) -> None:
    r = run_cli(*args, cwd=root)
    assert r.returncode == 0, (args, r.stdout + r.stderr)


def _refused(r, needle: str) -> None:
    """Exit 1, one ``error:`` line, no traceback, and a line naming it."""
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert len(errors) == 1, r.stderr
    assert any(needle in ln for ln in r.stderr.splitlines()), (
        f"no line says {needle!r}:\n{r.stderr}"
    )


@pytest.fixture(scope="module")
def blank(tmp_path_factory) -> Path:
    """A processor, a generator, a consumer and a module object.

    ``--no-c-prefix`` so the C names below are the ones written here.
    """
    base = tmp_path_factory.mktemp("gh1884_blank")
    _run_ok(base, "new", "p", "--no-c-prefix")
    root = base / "p"
    _run_ok(root, "object", "cc", "--arg-type", "float")
    _run_ok(root, "object", "gg", "--preset", "generator")
    _run_ok(root, "object", "kk", "--preset", "consumer")
    _run_ok(root, "module", "mm")
    _run_ok(root, "object", "mo", "--module", "mm")
    _run_ok(root, "status", "--check")
    return root


def _copy(blank: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "p"
    shutil.copytree(blank, dst)
    return dst


# -- the command line: refused while parsing, nothing written ---------------

#: (id, the command, run in a project or in an empty directory). ``{S}`` is
#: the string type under test, or its ``T[]`` form.
CLI_SHAPES = [
    (
        "new-processor",
        [
            "new",
            "p1",
            "--object",
            "cc",
            "--arg-type",
            "{S}",
            "--return-type",
            "{S}",
        ],
        False,
    ),
    (
        "new-array-element",
        ["new", "p1", "--object", "cc", "--arg-type", "{S}[]"],
        False,
    ),
    (
        "object-generator",
        ["object", "g2", "--preset", "generator", "--return-type", "{S}"],
        True,
    ),
    (
        "object-consumer",
        ["object", "k2", "--preset", "consumer", "--arg-type", "{S}"],
        True,
    ),
    (
        "object-blockwise-element",
        ["object", "b2", "--preset", "blockwise", "--arg-type", "{S}[]"],
        True,
    ),
    (
        "object-module",
        ["object", "m2", "--module", "mm", "--arg-type", "{S}"],
        True,
    ),
]


@pytest.mark.parametrize("stype", STR_TYPES)
@pytest.mark.parametrize(
    "args,in_project",
    [pytest.param(a, p, id=i) for i, a, p in CLI_SHAPES],
)
def test_the_command_line_refuses_it(blank, tmp_path, stype, args, in_project):
    root = _copy(blank, tmp_path) if in_project else tmp_path
    argv = [a.replace("{S}", stype) for a in args]
    at = next(i for i, a in enumerate(argv) if a.startswith(stype))
    before = _snapshot(root)

    r = run_cli(*argv, cwd=root)

    # Named by the flag the author typed, which only the parse can say: the
    # render behind it refuses the same type in the manifest's words.
    _refused(r, f"{argv[at - 1]} '{argv[at]}' {REFUSED}")
    assert _snapshot(root) == before, f"jm {' '.join(argv)} wrote"


# -- the manifest: refused before apply or status writes anything ------------

#: (id, component, the keys rewritten). The issue's three shapes, its
#: generator and consumer as `jm object --preset` writes them, a module
#: object, and an array of strings.
MANIFEST_SHAPES = [
    ("processor", "cc", ("arg_type", "return_type"), ""),
    ("generator", "gg", ("return_type",), ""),
    ("consumer", "kk", ("arg_type",), ""),
    ("module-object", "mo", ("arg_type",), ""),
    ("array-element", "cc", ("arg_type",), "[]"),
]


def _poison(root: Path, comp: str, keys, value: str) -> None:
    """Rewrite *keys* of ``objects/<comp>.toml`` to *value*, exactly once
    each, and prove they landed where `C.load` will read them."""
    frag = root / "objects" / f"{comp}.toml"
    text = frag.read_text(encoding="utf-8")
    for key in keys:
        text, n = re.subn(
            rf'^{key} = ".*"$', f'{key} = "{value}"', text, flags=re.M
        )
        assert n == 1, (frag, key, n)
    frag.write_text(text, encoding="utf-8")
    with open(frag, "rb") as fh:
        table = tomllib.load(fh)[comp]
    assert all(table[k] == value for k in keys), table


@pytest.mark.parametrize("command", [["apply"], ["status", "--check"]])
@pytest.mark.parametrize("stype", STR_TYPES)
@pytest.mark.parametrize(
    "comp,keys,suffix",
    [pytest.param(c, k, s, id=i) for i, c, k, s in MANIFEST_SHAPES],
)
def test_the_manifest_refuses_it(
    blank, tmp_path, stype, comp, keys, suffix, command
):
    root = _copy(blank, tmp_path)
    value = stype + suffix
    _poison(root, comp, keys, value)
    before = _snapshot(root)

    r = run_cli(*command, cwd=root)

    for key in keys:
        _refused(r, f"'{comp}' {key} '{value}' {REFUSED}")
    assert _snapshot(root) == before, f"jm {' '.join(command)} wrote"


# -- the renders that do not pass through apply ------------------------------


@pytest.mark.parametrize("stype", STR_TYPES)
def test_a_mutating_command_over_a_hand_edit_writes_no_binding(
    blank, tmp_path, stype
):
    """``jm method`` re-renders the object's binding from the manifest
    without asking ``apply``'s check; the render refuses it instead."""
    root = _copy(blank, tmp_path)
    _poison(root, "cc", ("arg_type",), stype)
    binding = [root / "native/src/cc/cc_ext.c", root / "src/p/cc.pyi"]
    before = [p.read_bytes() for p in binding]

    r = run_cli("method", "cc", "foo", cwd=root)

    _refused(r, f"arg_type '{stype}' {REFUSED}")
    assert [p.read_bytes() for p in binding] == before


@pytest.mark.parametrize("stype", STR_TYPES)
def test_bind_refuses_a_header_whose_step_takes_one(blank, tmp_path, stype):
    """``jm bind`` reads the step type from the header, not the manifest."""
    from test_bind import undeclare

    root = _copy(blank, tmp_path)
    # bind refuses a declared component first (gh-2072); undeclared, the
    # header is all it reads, so the type check is the one that refuses.
    undeclare(root, "cc")
    header = root / "native/inc/p/cc/cc_core.h"
    text = header.read_text(encoding="utf-8")
    old = "cc_step(const cc_state_t *state, float x)"
    assert text.count(old) == 1, text
    # Spaced around the `*`, the one spelling bind's parser reads as a type.
    spaced = stype.replace("*", " * ").rstrip()
    header.write_text(
        text.replace(old, f"cc_step(const cc_state_t *state, {spaced} x)"),
        encoding="utf-8",
    )
    before = _snapshot(root)

    r = run_cli("bind", "cc", cwd=root)

    _refused(r, f"'{stype}' {REFUSED}")
    assert _snapshot(root) == before, "jm bind wrote"


# -- what still passes -------------------------------------------------------


@pytest.mark.parametrize(
    "ctype",
    sorted(t for t, m in T._CTYPE_META.items() if m["kind"] != "str"),
)
def test_every_other_registered_type_is_a_step_type(ctype):
    for spelled in (ctype, f"{ctype}[]"):
        assert T.step_type_error("--arg-type", spelled) is None, spelled
        cfg = {"o": {"arg_type": spelled, "return_type": spelled}}
        assert C.manifest_type_errors(cfg) == [], spelled


@pytest.mark.parametrize("stype", STR_TYPES)
def test_a_string_stays_legal_off_the_step(stype):
    """The refusal is the step's alone: an init param, a method's param
    and return, and a function's param keep their string support."""
    cfg = {
        "o": {
            "init_params": [{"name": "path", "type": stype}],
            "methods": [
                {
                    "name": "label",
                    "arg_type": "void",
                    "return_type": stype,
                    "params": [{"name": "s", "type": stype}],
                }
            ],
        },
        "module": {
            "mm": {
                "functions": [
                    {"name": "f", "params": [{"name": "s", "type": stype}]}
                ]
            }
        },
    }
    assert C.manifest_type_errors(cfg) == []
