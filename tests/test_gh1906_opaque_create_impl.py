"""gh-1906: every apply and status refuses a C body apply cannot honour.

An ``opaque = true`` state field is assigned by nothing jm generates: the
struct is ``calloc``'d and ``create()`` sets only the managed fields, so an
object declaring one without ``create_impl`` / ``create_impl_file`` ships
the pointer NULL. That was refused only by ``jm apply <fragment-path>``,
which asked the fragment alone. The same fragment already under
``objects/`` applied through a bare ``jm apply``, and ``jm status
--check`` passed it. The other half of that validator -- a body and its
``_file`` companion set together -- lived on in a second copy inside
``_apply._resolve_impl``.

Now ``_config.impl_key_errors`` asks both of the MERGED manifest, and
``apply`` (bare and fragment) and ``status`` refuse on it before anything
is written. It is the one copy: ``_resolve_impl`` no longer asks.

GATE: a bare ``jm apply`` and ``jm status --check`` of an objects/
      fragment with an opaque field and no ``create_impl`` exit 1 with one
      ``error:`` line and the tree byte-identical; every body/``_file``
      pair, on an object, a method and a module function, is refused the
      same way; and an opaque field WITH ``create_impl`` applies, builds,
      and its pointer is non-NULL once constructed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from _cmake import PYTHON3_ARG

_SKIP_BUILD = (
    "cmake not found"
    if shutil.which("cmake") is None
    else "no C compiler found"
    if default_cc() is None
    else None
)

#: An object with a managed field and an opaque one, and no create_impl.
_OPAQUE = """\
[buf]
arg_type = "float"
return_type = "float"
<<extra>>
[[buf.state]]
name = "gain"
type = "double"
default = "1.0"

[[buf.state]]
name = "ring"
type = "float *"
opaque = true
"""

#: What makes the opaque field real, and a step() that reads it: the first
#: slot plus the input, or -1 when the pointer is NULL.
_WITH_CREATE = '''\
create_impl = """
obj->gain = gain;
obj->ring = calloc(4, sizeof(float));
if (!obj->ring) { free(obj); return NULL; }
obj->ring[0] = 7.0f;
"""
destroy_impl = "free(state->ring);"
impl = "return state->ring ? state->ring[0] + x : -1.0f;"
'''


def _project(root: Path, fragment: str) -> Path:
    """A fresh project with *fragment* already under ``objects/``."""
    r = run_cli("new", "op", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    p = root / "op"
    (p / "objects").mkdir(exist_ok=True)
    (p / "objects" / "buf.toml").write_text(fragment, encoding="utf-8")
    return p


def _tree(root: Path) -> "dict[str, bytes]":
    return {
        f.relative_to(root).as_posix(): f.read_bytes()
        for f in sorted(root.rglob("*"))
        if f.is_file()
    }


def _refused(p: Path, *args: str, needle: str) -> None:
    """``jm <args>`` exits 1 with one ``error:`` line naming *needle*, and
    leaves the tree as it found it."""
    before = _tree(p)
    r = run_cli(*args, cwd=p)
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert len(errors) == 1 and needle in errors[0], r.stderr
    assert _tree(p) == before


@pytest.mark.parametrize("cmd", [("apply",), ("status", "--check")])
def test_opaque_without_create_impl_is_refused(tmp_path, cmd):
    p = _project(tmp_path, _OPAQUE.replace("<<extra>>", ""))
    _refused(p, *cmd, needle="opaque state field(s) [ring]")


#: Each body/`_file` pair, set together on each section that reads one.
_PAIR_CASES = [(ik, fk, "object") for ik, fk in C.IMPL_PAIRS] + [
    ("impl", "impl_file", "method"),
    ("impl", "impl_file", "function"),
]


@pytest.mark.parametrize("cmd", [("apply",), ("status", "--check")])
@pytest.mark.parametrize("ik,fk,where", _PAIR_CASES)
def test_body_and_file_together_are_refused(tmp_path, cmd, ik, fk, where):
    both = f'{ik} = "return 0;"\n{fk} = "legacy.c::f"\n'
    if where == "object":
        frag = f'[buf]\narg_type = "float"\nreturn_type = "float"\n{both}'
    elif where == "method":
        frag = (
            '[buf]\narg_type = "float"\nreturn_type = "float"\n'
            '[[buf.methods]]\nname = "run"\narg_type = "float"\n'
            f'return_type = "float"\n{both}'
        )
    else:
        frag = (
            "[module.m]\nobjects = []\n"
            '[[module.m.functions]]\nname = "f"\nreturn_type = "int"\n'
            f"{both}"
        )
    p = _project(tmp_path, frag)
    _refused(p, *cmd, needle=f"`{ik}` and `{fk}` are mutually exclusive")


def test_fragment_path_refused_before_the_copy(tmp_path):
    """``jm apply <path>`` asks the same validator of the manifest the
    fragment would make, before copying it -- so a library caller, which
    has no command undo, is left no ``objects/buf.toml`` either."""
    r = run_cli("new", "op", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    p = tmp_path / "op"
    frag = tmp_path / "buf.toml"
    frag.write_text(_OPAQUE.replace("<<extra>>", ""), encoding="utf-8")
    _refused(p, "apply", str(frag), needle="opaque state field(s) [ring]")
    with pytest.raises(SystemExit):
        apply_run(p, fragment=frag)
    assert not (p / "objects" / "buf.toml").exists()


def _cmake_build(root: Path) -> None:
    for cmd in (
        [
            "cmake",
            "-S",
            str(root),
            "-B",
            str(root / "build"),
            PYTHON3_ARG,
        ],
        ["cmake", "--build", str(root / "build"), "-j", "4"],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        assert r.returncode == 0, f"{cmd}:\n{r.stdout}\n{r.stderr}"


def test_opaque_with_create_impl_builds_and_is_set(tmp_path):
    """The positive case, through a compiler and an import: the opaque
    pointer ``create_impl`` allocates is the one ``step()`` reads."""
    if _SKIP_BUILD:
        pytest.skip(_SKIP_BUILD)
    p = _project(tmp_path, _OPAQUE.replace("<<extra>>", _WITH_CREATE))
    r = run_cli("apply", cwd=p)
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli("status", "--check", cwd=p)
    assert r.returncode == 0, r.stdout + r.stderr
    _cmake_build(p)
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from op import Buf\nprint(Buf(gain=2.0).step(1.0))",
        ],
        cwd=p / "src",
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    # 7 + 1: ring[0] as create_impl set it. NULL would read -1.
    assert out.stdout.strip() == "8.0", out.stdout
