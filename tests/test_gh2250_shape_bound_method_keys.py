"""A method key only some shapes read is honoured or refused on every shape.

gh-2202, gh-2250, gh-2313 -- the #1408 / gh-1232 silent-key family. Five
keys are read on only some method shapes, and each was accepted on every
one, stored in the manifest, and read by nobody:

- ``none_on_empty`` (gh-2202) on a scalar, a record, a status -- shapes
  with no empty result to answer ``None`` for;
- ``status_return`` (gh-2250) on a single record or a list of them, a
  ``variable_output`` result or a borrow, whose kernels return a record, a
  count or a pointer: the refusing kernel's error code became a result.
  Beside ``out_type`` or ``multi_output`` it was read, and rendered a call
  without the outputs: ``too few arguments`` in the user's build;
- ``strict`` with no input array, and ``count_default`` / ``count_name``
  beside an input that sizes the call (gh-2313).

The shapes that read each key are DECLARED -- `_keys.RESULT_SHAPE_KEYS` and
`_keys.INPUT_SIDE_KEYS` -- and `_method.shape_keys_why_not` refuses the
rest, from `_method.run`, the front door ``jm method`` and ``apply`` share.
``status_return`` beside an output is IMPLEMENTED rather than refused: the
status is checked, the outputs are returned.

The gate does not ask the predicate which pairs it accepts -- that would be
the code grading itself. Registration-free over both axes: every key of
those two tables, on every shape of the gh-2259 roster (`SHAPES`, every
shape ``jm method`` writes a binding for), and for each pair:

1. **Accepted means read.** The method's C wrapper with the key differs
   from the one without it; for ``status_return`` it tests a status.
2. **Refused means refused by both doors**, with one ``error:`` line naming
   the key and nothing written: ``jm method`` with the flag, and ``apply``
   over a row ``jm method`` wrote without it.
3. **Armed.** Every key is accepted on some shape and refused on another.

Then, built: every shape ``status_return`` is accepted on returns what its
``.pyi`` says from a kernel that succeeds, and raises ``ValueError`` naming
the code from one that refuses.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc

from _jmrun import run_cli
from just_makeit import _keys
from just_makeit._method import _SIGNATURE_COERCIONS
from test_gh2259_batch_method_faces import SHAPES

#: Every key whose reading depends on the shape, read off the registry.
KEYS = sorted(set(_keys.RESULT_SHAPE_KEYS) | set(_keys.INPUT_SIDE_KEYS))

#: A value for a key whose sample cannot be derived from its coercion.
_SAMPLES = {"count_default": "7", "count_name": "k"}


def _cli(key: str) -> "list[str]":
    """The ``jm method`` flag(s) declaring *key*."""
    flag = "--" + key.replace("_", "-")
    coerce, _default = _SIGNATURE_COERCIONS[key]
    return [flag] if coerce is bool else [flag, _SAMPLES[key]]


def _toml(key: str) -> str:
    """The manifest spelling of the same declaration."""
    coerce, _default = _SIGNATURE_COERCIONS[key]
    return "true" if coerce is bool else f'"{_SAMPLES[key]}"'


def _flags(key: str, shape: str) -> "list[str]":
    """*shape*'s flags, with an int return where *key* reads one.

    ``status_return`` reads the kernel's return as an integer status, so a
    shape whose return is a float or ``void`` is asked with ``int`` -- the
    question is the SHAPE, and a non-integer return is refused for its own
    reason (`test_status_return_needs_an_integer_return`).
    """
    flags = list(SHAPES[shape])
    if key != "status_return":
        return flags
    if "--return-type" not in flags:
        return [*flags, "--return-type", "int"]
    at = flags.index("--return-type") + 1
    if flags[at] in ("float", "void"):
        flags[at] = "int"
    return flags


def _snapshot(root: Path) -> "dict[str, bytes]":
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def _error_lines(stderr: str) -> "list[str]":
    return [ln for ln in stderr.splitlines() if ln.startswith("error:")]


def _wrapper(root: Path) -> str:
    """Method ``m``'s C wrapper, from its definition to its closing brace."""
    ext = (root / "native/src/o/o_ext.c").read_text("utf-8")
    start = ext.index("\nO_m(OObject *self")
    return ext[start : ext.index("\n}\n", start)]


@pytest.fixture(scope="module")
def blank(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("gh2250")
    assert run_cli("new", "p", "--no-c-prefix", cwd=base).returncode == 0
    r = run_cli("object", "o", "--state", "k:double:0.0", cwd=base / "p")
    assert r.returncode == 0, r.stdout + r.stderr
    return base / "p"


@pytest.fixture(scope="module")
def outcomes(blank, tmp_path_factory) -> "dict[tuple[str, str], object]":
    """Per (key, shape): the wrapper pair when accepted, else the refusal.

    Accepted: ``(without, with)`` wrapper text. Refused: the ``JmRun``,
    after asserting nothing was written.
    """
    base = tmp_path_factory.mktemp("pairs")
    out: "dict[tuple[str, str], object]" = {}
    for key in KEYS:
        for shape in SHAPES:
            plain = base / f"{key}-{shape}-plain"
            shutil.copytree(blank, plain)
            flags = _flags(key, shape)
            r = run_cli("method", "o", "m", *flags, cwd=plain)
            assert r.returncode == 0, (shape, flags, r.stdout + r.stderr)
            keyed = base / f"{key}-{shape}-keyed"
            shutil.copytree(blank, keyed)
            before = _snapshot(keyed)
            r = run_cli("method", "o", "m", *flags, *_cli(key), cwd=keyed)
            if r.returncode == 0:
                out[(key, shape)] = (_wrapper(plain), _wrapper(keyed))
            else:
                assert _snapshot(keyed) == before, (key, shape)
                out[(key, shape)] = r
    return out


def _accepted(outcomes, key: str, shape: str) -> bool:
    return isinstance(outcomes[(key, shape)], tuple)


def test_the_tables_name_method_keys():
    assert set(KEYS) <= _keys.METHOD_KEYS
    assert {"none_on_empty", "status_return", "strict"} <= set(KEYS)


@pytest.mark.parametrize("key", KEYS)
def test_every_key_is_accepted_somewhere_and_refused_elsewhere(outcomes, key):
    verdicts = {_accepted(outcomes, key, s) for s in SHAPES}
    assert verdicts == {True, False}, (key, verdicts)


@pytest.mark.parametrize("key", KEYS)
def test_an_accepted_key_is_read(outcomes, key):
    ignored = []
    for shape in SHAPES:
        if not _accepted(outcomes, key, shape):
            continue
        without, with_key = outcomes[(key, shape)]
        if key == "status_return":
            # The flag is a no-op only on the roster's own status shape,
            # so the question is whether the wrapper tests a status.
            if "if (_rc != 0) {" not in with_key:
                ignored.append(shape)
        elif with_key == without:
            ignored.append(shape)
    assert not ignored, f"{key} is accepted and ignored on {ignored}"


@pytest.mark.parametrize("key", KEYS)
def test_a_refused_key_is_refused_by_both_doors(
    outcomes, blank, tmp_path, key
):
    named = (key, "--" + key.replace("_", "-"))
    for shape in SHAPES:
        if _accepted(outcomes, key, shape):
            continue
        r = outcomes[(key, shape)]
        errors = _error_lines(r.stderr)
        assert len(errors) == 1 and "Traceback" not in r.stderr, (
            shape,
            r.stderr,
        )
        assert any(n in errors[0] for n in named), (shape, errors[0])
        # `apply`, over the row `jm method` wrote without the key.
        root = tmp_path / shape / "p"
        shutil.copytree(blank, root)
        flags = _flags(key, shape)
        assert run_cli("method", "o", "m", *flags, cwd=root).returncode == 0
        frag = root / "objects" / "o.toml"
        text = frag.read_text("utf-8")
        row = text.index('name = "m"')
        frag.write_text(
            text[:row] + f"{key} = {_toml(key)}\n" + text[row:], "utf-8"
        )
        before = _snapshot(root)
        a = run_cli("apply", cwd=root)
        assert a.returncode == 1, f"apply accepted {key} on {shape}"
        errors = _error_lines(a.stderr)
        assert len(errors) == 1 and "Traceback" not in a.stderr, (
            shape,
            a.stderr,
        )
        assert any(n in errors[0] for n in named), (shape, errors[0])
        assert _snapshot(root) == before, f"{shape}: apply wrote first"


# -- the issues' own triggers -------------------------------------------------

TRIGGERS = [
    # gh-2202
    ("--arg-type float --return-type float --none-on-empty", "none_on_empty"),
    (
        "--arg-type void --return-type int --status-return --none-on-empty",
        "none_on_empty",
    ),
    # gh-2250
    (
        "--arg-type float --return-type rec_t --single --result-field a:double"
        " --status-return",
        "status_return",
    ),
    (
        "--arg-type float --return-type rec_t --result-field a:double"
        " --status-return",
        "status_return",
    ),
    # gh-2313
    (
        "--arg-type float --return-type float --batch --count-default 4",
        "count_default",
    ),
    ("--arg-type void --return-type float --batch --strict", "strict"),
]


@pytest.mark.parametrize("flags,key", TRIGGERS)
def test_the_issues_triggers_are_refused(blank, tmp_path, flags, key):
    root = tmp_path / "p"
    shutil.copytree(blank, root)
    before = _snapshot(root)
    r = run_cli("method", "o", "m", *flags.split(), cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    errors = _error_lines(r.stderr)
    assert len(errors) == 1 and key in errors[0], r.stderr
    assert _snapshot(root) == before


@pytest.mark.parametrize("rt", ["void", "float", "double _Complex"])
def test_status_return_needs_an_integer_return(blank, tmp_path, rt):
    """``void`` did not compile; a float was truncated to ``int``."""
    root = tmp_path / "p"
    shutil.copytree(blank, root)
    r = run_cli(
        "method", "o", "m", "--arg-type", "float", "--return-type", rt,
        "--status-return", cwd=root,
    )  # fmt: skip
    assert r.returncode == 1, r.stdout + r.stderr
    assert "status_return" in r.stderr and "not an integer" in r.stderr


# -- built: every accepted status_return shape raises on a refusal ------------


def _skip_reason() -> "str | None":
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    return None


def _call_args(flags: "list[str]") -> str:
    """Python arguments for a method declared with *flags*."""
    args = []
    if "--arg-type" in flags:
        at = flags[flags.index("--arg-type") + 1]
        args.append("np.zeros(4, np.float32)" if at.endswith("[]") else "1.0")
    for i, f in enumerate(flags):
        if f == "--param":
            _name, ptype = flags[i + 1].split(":")
            args.append(
                "np.zeros(4, np.float32)"
                if ptype.endswith("[]")
                else "4"
                if ptype == "size_t"
                else "1.0"
            )
    return ", ".join(args)


@pytest.fixture(scope="module")
def status_built(outcomes, blank, tmp_path_factory):
    """Every shape `status_return` is accepted on, one method each, built.

    Each kernel returns the object's ``k`` as its status, so ``O(k=0)``
    succeeds and ``O(k=3)`` refuses with code 3.
    """
    why = _skip_reason()
    if why:
        pytest.skip(why)
    shapes = [s for s in SHAPES if _accepted(outcomes, "status_return", s)]
    root = tmp_path_factory.mktemp("built") / "p"
    shutil.copytree(blank, root)
    for s in shapes:
        flags = _flags("status_return", s)
        flags += [] if "--status-return" in flags else ["--status-return"]
        r = run_cli("method", "o", s, *flags, cwd=root)
        assert r.returncode == 0, (s, r.stdout + r.stderr)
    core = root / "native/src/o/o_core.c"
    text = core.read_text("utf-8")
    for s in shapes:
        at = text.index(f"\no_{s}(o_state_t *state")
        stub = text.index("return (int)0;", at)
        text = text[:stub] + "return (int)state->k;" + text[stub + 14 :]
    core.write_text(text, "utf-8")
    build = root / "build"
    for cmd in (
        ["cmake", "-S", str(root), "-B", str(build),
         f"-DPython3_EXECUTABLE={sys.executable}"],
        ["cmake", "--build", str(build)],
    ):  # fmt: skip
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, f"{cmd[:2]}:\n{r.stdout}\n{r.stderr}"
    return root, shapes


def _pyi_return(root: Path, method: str) -> str:
    tree = ast.parse((root / "src/p/o.pyi").read_text("utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == method:
            assert node.returns is not None
            return ast.unparse(node.returns)
    raise AssertionError(f"{method}: not in the .pyi")


def test_status_return_shapes_include_the_output_forms(status_built):
    """Armed: the two forms that did not compile are among those built."""
    _root, shapes = status_built
    assert {"out_type", "multi_output", "multi_output_void"} <= set(shapes)


def test_a_status_return_method_raises_on_a_refusal(status_built):
    """Each built method, called on an accepting and a refusing kernel."""
    root, shapes = status_built
    probe = ["import numpy as np", "from p import O"]
    for shape in shapes:
        call = f"{shape}({_call_args(_flags('status_return', shape))})"
        probe += [
            f"r = O(k=0).{call}",
            "n = len(r) if isinstance(r, tuple) else 0",
            "try:",
            f"    O(k=3).{call}",
            "    why = 'NOTHING'",
            "except ValueError as e:",
            "    why = str(e)",
            f"print({shape!r}, type(r).__name__, n, repr(why))",
        ]
    r = subprocess.run(
        [sys.executable, "-c", "\n".join(probe)],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    ext = (root / "native/src/o/o_ext.c").read_text("utf-8")
    seen = []
    for line in r.stdout.splitlines():
        shape, kind, n, why = line.split(" ", 3)
        seen.append(shape)
        assert "rc=3" in why, (shape, why)
        ann = _pyi_return(root, shape)
        # What the call returned is what the `.pyi` says it returns.
        if ann == "None":
            assert kind == "NoneType", (shape, ann, kind)
        elif ann.startswith("tuple["):
            assert kind == "tuple", (shape, ann, kind)
            assert int(n) == ann.count(",") + 1, (shape, ann, n)
        elif ann.startswith("NDArray"):
            assert kind == "ndarray", (shape, ann, kind)
        else:
            assert kind == ann, (shape, ann, kind)
        # ...and the runtime synopsis says the same.
        synopsis = re.search(rf'"{shape}\(.*?\) -> (.*?)\\n"', ext)
        assert synopsis, shape
        assert synopsis.group(1) == re.sub(
            r"NDArray\[[^]]*\]", "ndarray", ann
        ), shape
    assert seen == shapes, r.stdout
