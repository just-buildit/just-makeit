"""gh-2380: scan the object kind's whole generated surface.

Renders one project with every shape the object kind has -- state accessors,
a writable property, ``step`` / ``steps``, all twenty method shapes of
gh-2259, a module object, a view -- and reads the C it generated for any
function that checks liveness, converts an argument, and then uses the state
without checking again (`_order_scan`).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from _jmrun import run_cli
from _order_scan import unrechecked

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _config as C  # noqa: E402
from test_gh2259_batch_method_faces import SHAPES  # noqa: E402


#: The property flags the CLI offers, one declaration each. Tried and found
#: NOT to exist or not to apply: `char[N]` and `str` state (refused: the
#: type is unsupported), a container property without `--type
#: dict|list|tuple` (refused). Containers and `--codec` need hand-written
#: accessors, so they have their own tests.
PROPERTIES: "dict[str, list[str]]" = {
    "p1": ["--type", "double"],  # read-only, getter fn
    "p2": ["--type", "double", "--writable"],  # getter + setter fn
    "p3": ["--type", "double", "--field"],  # read-only field
    "p4": ["--type", "int", "--field", "--writable"],
    "p5": ["--type", "float _Complex", "--field", "--writable"],
    "p6": ["--type", "bool", "--field", "--writable"],
    "b1": ["--type", "float[]", "--buf-field", "buf"],
    "b2": [
        "--type",
        "float[]",
        "--buf-field",
        "buf2",
        "--len-field",
        "n2",
        "--valid-field",
        "v2",
    ],  # fmt: skip
    "b3": ["--type", "float[]", "--buf-field", "buf3", "--writable"],
    "e1": ["--type", "float[]", "--expr", "o_view(self->handle)"],
    "c1": [
        "--type",
        "capsule",
        "--capsule",
        "o.thing",
        "--capsule-type",
        "thing_t *",
    ],  # fmt: skip
    "w1": [
        "--type",
        "double",
        "--writable",
        "--error",
        "ValueError",
        "--error-message",
        "bad",
    ],  # fmt: skip
}

STATES = [
    "s:float[4]",
    "v:uint8_t[8]",
    "x:float _Complex",
    "y:bool:false",
    "z:size_t:0",
    "q:int64_t:0",
]


def _ok(root: Path, *argv: str) -> None:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (argv, r.stdout, r.stderr)


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("order_scan")
    _ok(tmp, "new", "mp", "--no-c-prefix")
    root = tmp / "mp"
    _ok(
        root, "object", "o", "--state", "k:double:0.0", "--state", "n:int:0",
        "--state", "taps:float[4]",
    )  # fmt: skip
    _ok(
        root, "property", "o", "gain", "--type", "double", "--field",
        "--writable",
    )  # fmt: skip
    for name, flags in SHAPES.items():
        _ok(root, "method", "o", name, *flags)
    for pname, flags in PROPERTIES.items():
        _ok(root, "property", "o", pname, *flags)
    # An [[enum]] has no CLI; the manifest declares it, a property names it.
    cfg = C.load(root)
    cfg["enum"] = [{"name": "mode", "values": ["a", "b"]}]
    C.save(root, cfg)
    _ok(
        root, "property", "o", "en1", "--type", "int", "--field",
        "--writable", "--enum", "mode",
    )  # fmt: skip
    # Every state type the CLI takes, scalar and array.
    _ok(root, "object", "st", *[a for s in STATES for a in ("--state", s)])
    # Every preset: they change which step/steps wrappers are generated.
    for name, preset in (
        ("g", "generator"),
        ("b", "blockwise"),
        ("c", "consumer"),
    ):
        _ok(root, "object", name, "--preset", preset, "--state", "k:int:0")
    _ok(root, "object", "r", "--preset", "reader", "--state", "k:int:0")
    # A module object, with its own step and a view over it.
    _ok(root, "module", "dsp")
    _ok(root, "object", "mo", "--module", "dsp", "--state", "k:double:0.0")
    _ok(root, "view", "mo", "Peek", "--module", "dsp",
        "--create-fn", "mo_create_peek")  # fmt: skip
    return root


def _flagged(root: Path) -> "dict[str, list[tuple[str, str]]]":
    out = {}
    for c in sorted((root / "native" / "src").rglob("*_ext*.c")):
        hits = unrechecked(c.read_text(encoding="utf-8"))
        if hits:
            out[c.name] = hits
    return out


#: Functions known to have the window, by file. It may only shrink: a
#: fixed emitter removes its rows, and a new shape that opens the window is
#: not in it, so it fails here.
BASELINE: "dict[str, list[str]]" = {}


def test_object_kind_has_no_unrechecked_conversion(project):
    got = {f: [n for n, _ in hs] for f, hs in _flagged(project).items()}
    assert got == BASELINE, (
        "an object-kind binding function checks liveness, converts an "
        "argument (which can run Python and destroy the object) and then "
        "calls the kernel with no recheck (gh-2380): "
        f"{got}"
    )
