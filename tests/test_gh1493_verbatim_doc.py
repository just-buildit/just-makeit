"""gh-1493: a manifest ``doc`` is rendered verbatim, on every face, both faces.

Measured before this: the same three-paragraph ``doc`` was kept whole on a
module and an init param, FLATTENED into one reflowed paragraph on an object,
a method and a property's stub, TRUNCATED to its first line in a module
function's stub, and silently DROPPED on a state field, a method param and a
function param. A property and a module function also disagreed with
themselves: one text in the ``.pyi``, another in ``help()``.

The rule the maintainer decided::

    doc = \"\"\"
    This is an example.
    It has two lines.
    \"\"\"

renders as exactly those lines. The only normalisation is
``inspect.cleandoc`` -- Python's own docstring rule -- so a doc whose text
starts on the opening line, with its continuation indented under an indented
TOML table, renders the same.

Built and imported, not read from generated text: the runtime face is what
``help()`` shows, and the only honest way to read it is to ask Python.
"""

from __future__ import annotations

import ast
import inspect
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli

pytestmark = pytest.mark.skipif(
    not shutil.which("cmake"), reason="cmake not found"
)


#: One doc per face, each tagged so a hit cannot come from a neighbour: text
#: on the opening line, an indented continuation, and a second paragraph.
def _doc(face: str) -> str:
    return (
        f'"""{face}: what happens in this case?\n'
        f"    This is an example of {face}.\n"
        "    It has two lines.\n"
        "\n"
        f"    A second paragraph for {face}.\n"
        '    """'
    )


def _expected(face: str) -> "list[str]":
    from just_makeit import _config as C  # tomllib, or tomli before 3.11

    raw = C.tomllib.loads(f"doc = {_doc(face)}")["doc"]
    return inspect.cleandoc(raw).split("\n")


FACES = [
    "object",
    "method",
    "property",
    "modobject",
    "function",
    "fnonly",
    "initparam",
    "state",
    "mparam",
    "fparam",
]


def _split(line: str) -> "tuple[int, str]":
    """``(indent, text)`` with a docstring's own quotes removed."""
    body = line.lstrip(" ")
    text = body.removeprefix('r"""').removeprefix('"""').removesuffix('"""')
    return len(line) - len(body), text


def _contains_block(text: str, lines: "list[str]") -> bool:
    """*lines*, consecutive, in *text*, each at the indent the author gave it.

    The block may sit at any depth -- a Parameters entry indents its
    description, a method docstring sits eight deep, and a docstring's first
    line follows its own quotes -- but every line keeps its indent RELATIVE to
    the block's first line. gh-1499: this compared stripped lines, so a doc
    whose continuation lines kept an indented TOML table's indent (raw text,
    never through `inspect.cleandoc`) read as verbatim here and was not.
    """
    have = [_split(ln) for ln in text.split("\n")]
    for i in range(len(have) - len(lines) + 1):
        seg = have[i : i + len(lines)]
        if [t.strip() for _, t in seg] != [ln.strip() for ln in lines]:
            continue
        ind0, t0 = seg[0]
        base = ind0 + len(t0) - len(t0.lstrip()) - _lead(lines[0])
        if all(
            not want.strip() or ind + _lead(t) == base + _lead(want)
            for (ind, t), want in zip(seg, lines)
        ):
            return True
    return False


def _lead(s: str) -> int:
    return len(s) - len(s.lstrip(" "))


def _add(path: Path, anchor: str, text: str) -> None:
    s = path.read_text(encoding="utf-8")
    assert s.count(anchor) == 1, (path, anchor)
    path.write_text(s.replace(anchor, anchor + text), encoding="utf-8")


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("gh1493")
    assert run_cli(
        "new", "vd", "--object", "gain", "--state", "level:float:1.0",
        "--arg-type", "float", "--return-type", "float", cwd=root,
    ).returncode == 0  # fmt: skip
    proj = root / "vd"
    for args in (
        ("module", "mod"),
        ("object", "blk", "--module", "mod", "--init-param", "n:int:1",
         "--arg-type", "float", "--return-type", "float"),
        ("function", "twice", "--module", "mod", "--param", "x:double",
         "--return-type", "double"),
        ("function", "thrice", "--module", "mod", "--param", "y:double",
         "--return-type", "double"),
        ("method", "gain", "scale", "--arg-type", "float",
         "--return-type", "float"),
        ("property", "gain", "level", "--type", "float", "--field",
         "--writable"),
    ):  # fmt: skip
        r = run_cli(*args, cwd=proj)
        assert r.returncode == 0, (args, r.stdout, r.stderr)

    gain = proj / "objects" / "gain.toml"
    _add(gain, "[gain]\n", f"doc = {_doc('object')}\n")
    _add(gain, 'name = "level"\ntype = "float"\ndefault = "1.0"\n',
         f"doc = {_doc('state')}\n")  # fmt: skip
    _add(gain, 'name = "scale"\n', f"doc = {_doc('method')}\n")
    _add(gain, "field = true\n", f"doc = {_doc('property')}\n")
    blk = proj / "objects" / "blk.toml"
    _add(blk, "[blk]\n", f"doc = {_doc('modobject')}\n")
    _add(blk, 'name = "n"\n', f"doc = {_doc('initparam')}\n")
    mod = proj / "modules" / "mod.toml"
    _add(mod, 'name = "twice"\n', f"doc = {_doc('function')}\n")
    # A doc with NO documented param: the stub's one-line branch, which is
    # where the truncation lived. `twice` takes the full-render branch.
    _add(mod, 'name = "thrice"\n', f"doc = {_doc('fnonly')}\n")
    _add(mod, 'name = "x"\n', f"doc = {_doc('fparam')}\n")
    # A method param doc is manifest-only, so it rides on a manifest-only
    # method: declared before `apply`, which scaffolds its C body to match.
    # (Adding a param to `scale` would change a signature under the
    # author's `_core.c`, which jm never rewrites.)
    with open(gain, "a", encoding="utf-8") as f:
        f.write(
            '\n[[gain.methods]]\nname = "shift"\nreturn_type = "float"\n'
            '\n[[gain.methods.params]]\nname = "k"\ntype = "float"\n'
            f"doc = {_doc('mparam')}\n"
        )
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    # `status` replays the manifest onto a copy and diffs: a doc the replay
    # drops renders differently there, and the file reads as STALE.
    r = run_cli("status", "--check", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr

    for cmd in (
        ["cmake", "-S", ".", "-B", "build",
         f"-DPython3_EXECUTABLE={sys.executable}"],
        ["cmake", "--build", "build"],
    ):  # fmt: skip
        r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
    return proj


def _stub_text(proj: Path) -> str:
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((proj / "src" / "vd").rglob("*.pyi"))
    )


def _runtime_docs(proj: Path) -> str:
    probe = (
        "import sys; sys.path.insert(0, 'src')\n"
        "from vd import Gain\n"
        "import vd.mod as m\n"
        "from vd.mod import Blk\n"
        "for d in (Gain.__doc__, Gain.scale.__doc__, Gain.shift.__doc__,"
        " Gain.level.__doc__, Blk.__doc__, m.twice.__doc__,"
        " m.thrice.__doc__):\n"
        "    print(d)\n"
        "    print('=' * 20)\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", probe], cwd=proj, capture_output=True,
        text=True,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.mark.parametrize("face", FACES)
def test_the_stub_renders_the_doc_verbatim(built, face) -> None:
    stub = _stub_text(built)
    ast.parse(stub)
    assert _contains_block(stub, _expected(face)), (
        f"{face}: the .pyi does not carry the doc line for line"
    )


@pytest.mark.parametrize("face", FACES)
def test_the_runtime_renders_the_doc_verbatim(built, face) -> None:
    assert _contains_block(_runtime_docs(built), _expected(face)), (
        f"{face}: help() does not carry the doc line for line"
    )


# ── width: the author's, and reported ─────────────────────────────────────────

LONG = "x" * 90


def _scaffold(tmp_path: Path, doc: str) -> "tuple[Path, str]":
    """A one-object project whose method carries *doc*; returns the apply
    output after the doc is in place."""
    assert (
        run_cli("new", "wd", "--object", "eng", cwd=tmp_path).returncode == 0
    )
    proj = tmp_path / "wd"
    r = run_cli(
        "method", "eng", "go", "--arg-type", "float", "--return-type",
        "float", cwd=proj,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    _add(proj / "objects" / "eng.toml", 'name = "go"\n', f"doc = {doc}\n")
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    return proj, r.stdout + r.stderr


def test_a_too_wide_line_is_kept_and_reported(tmp_path: Path) -> None:
    proj, out = _scaffold(tmp_path, f'"""Short summary.\n{LONG}\n"""')
    stub = (proj / "src" / "wd" / "eng.pyi").read_text(encoding="utf-8")
    assert f"        {LONG}\n" in stub, "jm re-wrapped an authored line"
    assert "1 manifest `doc` line(s) will exceed 79 columns" in out, out
    assert "eng.methods.go.doc: line will be 98 columns" in out, out


def test_a_doc_that_fits_is_not_reported(tmp_path: Path) -> None:
    _, out = _scaffold(tmp_path, '"""Short summary.\nA second line."""')
    assert "manifest `doc` line(s)" not in out, out
