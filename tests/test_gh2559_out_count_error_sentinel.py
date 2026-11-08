"""An ``out=`` method's ``size_t`` count can refuse with a sentinel (gh-2559).

A method that fills a caller's ``out = true`` array and returns the count
it wrote has one return value, and ``0`` is a real answer ("nothing yet").
So a refusal has to be a value no count can be -- doppler's ``Correlator``
returns ``SIZE_MAX`` off a flavor that does not serve the member. jm refused
``error_sentinel`` there ("this method returns none"), so Python received
``18446744073709551615`` as an int.

The rule is gh-2012's, reached from a second shape: the same predicate
(`_method.count_why_not`, widened by *writes_out*), the same emitter
(`_context/_methods._count_refusal_c`), the same doc entry
(`_diagnostics.raises_doc`) and the same sacred-fragment marker
(`_docsync._REFUSAL_VALUE_RE`).

What carries this file:

1. **Compiled**, built by running jm: a kernel returning ``SIZE_MAX``,
   ``0`` and ``n`` for three inputs -- the declared raise, ``0`` with the
   buffer untouched, ``n`` with it filled -- on a standalone object, a
   module object, and a view of it rewording the parent's message.
2. **Refusals** that keep the rule gh-2012's: a signed return, a count
   that is not ``size_t``, a result that replaces the return, and no
   ``out=`` array at all -- through the CLI and ``apply`` alike.
3. **Faces**: ``apply`` replays it unchanged, ``jm script`` replays the
   flags, the ``.pyi`` and the runtime docstring name the raise, and a
   sacred module fragment without the test is reported.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc

from _jmrun import run_cli


def _skip_reason() -> "str | None":
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    try:
        import numpy  # noqa: F401
    except ImportError:
        return "numpy not importable"
    return None


_SKIP = _skip_reason()

#: Lifted by ``--impl``: mode < 0 refuses, 0 is "nothing yet", and any
#: other mode fills the whole buffer and returns its length.
KERNELS = """\
#include <stddef.h>
#include <stdint.h>

size_t surf_k(void *state, int64_t mode, float *out, size_t out_len)
{
    (void)state;
    if (mode < 0)
        return SIZE_MAX;
    if (mode == 0)
        return 0;
    for (size_t i = 0; i < out_len; i++)
        out[i] = (float)(i + 1);
    return out_len;
}
"""

#: The method's flags, but for its error and message.
SURFACE = [
    "--arg-type", "void", "--return-type", "size_t",
    "--param", "mode:int64_t", "--out-param", "out:float[]",
    "--error-sentinel", "SIZE_MAX",
]  # fmt: skip
#: class -> (exception, message) the refusal raises.
RAISES = {
    "Blk": ("OSError", "Blk.surface: this flavor has none"),
    "R": ("ValueError", "R.surface: this flavor has none"),
    "RV": ("ValueError", "RV.surface: the view words it otherwise"),
}


def _ok(*args, cwd):
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout + r.stderr)
    return r


def _implement(path: Path, fn: str, body: str) -> None:
    text = path.read_text(encoding="utf-8")
    head = re.search(rf"^{fn}\([^)]*\)\n\{{\n", text, re.M)
    assert head, (fn, text)
    end = text.index("\n}\n", head.end())
    path.write_text(
        text[: head.end()] + body.rstrip("\n") + text[end:], encoding="utf-8"
    )


def _scaffold(where: Path) -> Path:
    """A standalone ``blk``, a module object ``r`` in ``m``, and a view
    ``RV`` of it overriding the message -- every flag through the CLI."""
    _ok("new", "p", "--no-c-prefix", cwd=where)
    root = where / "p"
    (root / "kernels.c").write_text(KERNELS, encoding="utf-8")
    _ok("object", "blk", cwd=root)
    exc, msg = RAISES["Blk"]
    _ok(
        "method", "blk", "surface", *SURFACE,
        "--error", exc, "--error-message", msg,
        "--impl", "kernels.c::surf_k", cwd=root,
    )  # fmt: skip
    _ok("module", "m", cwd=root)
    _ok("object", "r", "--module", "m", cwd=root)
    exc, msg = RAISES["R"]
    _ok(
        "method", "r", "surface", "--module", "m", *SURFACE,
        "--error", exc, "--error-message", msg,
        "--impl", "kernels.c::surf_k", cwd=root,
    )  # fmt: skip
    _ok("view", "r", "RV", "--module", "m", "--create-fn", "r_create_rv",
        cwd=root)  # fmt: skip
    _ok(
        "method", "r", "surface", "--module", "m", "--view", "RV",
        "--error-message", RAISES["RV"][1], cwd=root,
    )  # fmt: skip
    # `jm view` wrote the view's fragment, and apply never re-renders a
    # wrapper body in a sacred one (gh-2498): its own remedy.
    (root / "native/src/m/m_ext_rv.c").unlink()
    _ok("apply", cwd=root)
    _implement(
        root / "native/src/r/r_core.c",
        "r_create_rv",
        "    return r_create(gain);",
    )
    return root


@pytest.fixture(scope="module")
def scaffolded(tmp_path_factory) -> Path:
    return _scaffold(tmp_path_factory.mktemp("gh2559src"))


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    if _SKIP:
        pytest.skip(_SKIP)
    root = _scaffold(tmp_path_factory.mktemp("gh2559"))
    build = root / "build"
    for cmd in (
        [
            "cmake", "-S", str(root), "-B", str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        ["cmake", "--build", str(build)],
    ):  # fmt: skip
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, f"{cmd[:2]}:\n{r.stdout}\n{r.stderr}"
    return root


def _call(root: Path, cls: str, mode: int) -> str:
    """``RET <count> <buffer>`` or ``ERR <type>: <msg> <buffer>``."""
    imp = "from p import Blk" if cls == "Blk" else f"from p.m import {cls}"
    body = (
        "import numpy as np\n"
        f"{imp}\n"
        f"o = {cls}()\n"
        "buf = np.zeros(3, np.float32)\n"
        "try:\n"
        f"    r = o.surface({mode}, buf)\n"
        "except Exception as e:\n"
        "    print('ERR', type(e).__name__ + ':', e, buf.tolist())\n"
        "else:\n"
        "    print('RET', repr(r), buf.tolist())\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", body],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    return r.stdout.strip()


# -- 1. compiled ---------------------------------------------------------------


@pytest.mark.parametrize("cls", list(RAISES))
def test_the_sentinel_raises_the_declared_error(built, cls):
    """The bug: this printed ``RET 18446744073709551615``."""
    exc, msg = RAISES[cls]
    assert _call(built, cls, -1) == f"ERR {exc}: {msg} [0.0, 0.0, 0.0]"


@pytest.mark.parametrize("cls", list(RAISES))
def test_zero_is_nothing_yet(built, cls):
    assert _call(built, cls, 0) == "RET 0 [0.0, 0.0, 0.0]"


@pytest.mark.parametrize("cls", list(RAISES))
def test_a_count_is_returned_with_the_buffer_filled(built, cls):
    assert _call(built, cls, 1) == "RET 3 [1.0, 2.0, 3.0]"


# -- 2. refusals ---------------------------------------------------------------

#: (id, flags replacing SURFACE's return/out, the refusal's words).
REFUSED = [
    ("signed-return",
     ["--return-type", "int64_t", "--out-param", "out:float[]"],
     "use --error-negative"),
    ("not-size_t",
     ["--return-type", "uint32_t", "--out-param", "out:float[]"],
     "this method returns 'uint32_t'"),
    ("status-return",
     ["--return-type", "int", "--out-param", "out:float[]",
      "--status-return"],
     "--status-return renders a binding that never returns it"),
    ("no-out-param",
     ["--return-type", "size_t", "--param", "out:float[]"],
     "returns none"),
]  # fmt: skip


@pytest.fixture
def project(tmp_path: Path) -> Path:
    _ok("new", "p", cwd=tmp_path)
    root = tmp_path / "p"
    _ok("object", "o", cwd=root)
    return root


@pytest.mark.parametrize(
    "flags, words", [r[1:] for r in REFUSED], ids=[r[0] for r in REFUSED]
)
def test_the_cli_refuses_what_cannot_take_effect(project, flags, words):
    before = (project / "objects" / "o.toml").read_text("utf-8")
    r = run_cli(
        "method", "o", "m", "--arg-type", "void", *flags,
        "--error-sentinel", "SIZE_MAX", cwd=project,
    )  # fmt: skip
    assert r.returncode == 1, r.stdout + r.stderr
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert len(errors) == 1 and words in errors[0], r.stderr
    assert (project / "objects" / "o.toml").read_text("utf-8") == before


#: The same declarations as manifest rows `apply` replays.
_ROWS = {
    "signed-return": ('"int64_t"', "out = true"),
    "not-size_t": ('"uint32_t"', "out = true"),
    "no-out-param": ('"size_t"', ""),
}


@pytest.mark.parametrize("case", list(_ROWS))
def test_apply_refuses_the_same_declarations(project, case):
    ret, out = _ROWS[case]
    p = project / "objects" / "o.toml"
    p.write_text(
        p.read_text("utf-8")
        + '\n[[o.methods]]\nname = "hand"\narg_type = "void"\n'
        f"return_type = {ret}\n"
        'error_sentinel = "SIZE_MAX"\n'
        "params = [\n"
        f'  {{ name = "out", type = "float[]"{", " + out if out else ""} }},\n'
        "]\n",
        encoding="utf-8",
    )
    r = run_cli("apply", cwd=project)
    assert r.returncode == 1, r.stdout + r.stderr
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    want = {r_[0]: r_[2] for r_ in REFUSED}[case]
    assert len(errors) == 1 and want in errors[0], r.stderr


def test_doppler_s_manifest_row_applies(project):
    """The issue's own row, written by hand: `apply` accepts it."""
    p = project / "objects" / "o.toml"
    p.write_text(
        p.read_text("utf-8")
        + '\n[[o.methods]]\nname = "surface"\narg_type = "void"\n'
        'return_type = "size_t"\nerror_sentinel = "SIZE_MAX"\n'
        'error = "ValueError"\nerror_message = "surface: none here"\n'
        'params = [\n  { name = "out", type = "float[]", out = true },\n]\n',
        encoding="utf-8",
    )
    _ok("apply", cwd=project)
    ext = (project / "native/src/o/o_ext.c").read_text("utf-8")
    assert "if (y == (SIZE_MAX)) {" in ext, ext


# -- 3. faces ------------------------------------------------------------------


def _ext(root: Path) -> str:
    return (root / "native/src/blk/blk_ext.c").read_text(encoding="utf-8")


def test_apply_rebuilds_it_unchanged(scaffolded):
    before = _ext(scaffolded)
    _ok("apply", cwd=scaffolded)
    assert _ext(scaffolded) == before
    r = run_cli("status", "--check", cwd=scaffolded)
    assert r.returncode == 0, r.stdout


def test_script_replays_the_flags(scaffolded):
    out = _ok("script", cwd=scaffolded).stdout.replace("\\\n", " ")
    lines = [
        ln
        for ln in out.splitlines()
        if re.search(r"method \w+ surface\b", ln) and "--view" not in ln
    ]
    assert len(lines) == 2, out
    for ln in lines:
        assert "--error-sentinel SIZE_MAX" in ln, ln
        assert '--out-param "out:float[]"' in ln, ln


@pytest.mark.parametrize(
    "face",
    ["src/p/blk.pyi", "native/src/blk/blk_ext.c", "src/p/m/m.pyi"],
)
def test_each_doc_face_names_the_raise(scaffolded, face):
    text = (scaffolded / face).read_text(encoding="utf-8")
    assert "If the C call returns ``SIZE_MAX``, its" in text, text


def test_a_sacred_fragment_without_it_is_reported(tmp_path):
    """A module fragment rendered before the sentinel was declared keeps
    its binding; the missing test is reported by name, not left to return
    18446744073709551615."""
    _ok("new", "q", cwd=tmp_path)
    proj = tmp_path / "q"
    _ok("module", "m", cwd=proj)
    _ok("object", "r", "--module", "m", cwd=proj)
    _ok(
        "method", "r", "surface", "--module", "m", "--arg-type", "void",
        "--return-type", "size_t", "--out-param", "out:float[]", cwd=proj,
    )  # fmt: skip
    frag = proj / "native/src/m/m_ext_r.c"
    p = proj / "objects" / "r.toml"
    body = p.read_text("utf-8")
    assert body.count('name = "surface"\n') == 1, body
    p.write_text(
        body.replace(
            'name = "surface"\n',
            'name = "surface"\nerror_sentinel = "SIZE_MAX"\n',
        ),
        encoding="utf-8",
    )
    r = run_cli("apply", cwd=proj)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "y == (" not in frag.read_text("utf-8")
    assert "declares a refusal value for the count" in out, out

    frag.unlink()
    _ok("apply", cwd=proj)
    assert "y == (SIZE_MAX)" in frag.read_text("utf-8")
    again = run_cli("apply", cwd=proj)
    assert "no longer matches" not in again.stdout + again.stderr


def test_an_undeclared_method_is_unchanged(project):
    """Zero churn: without the key the wrapper has no sentinel test."""
    _ok(
        "method", "o", "plain", "--arg-type", "void", "--return-type",
        "size_t", "--out-param", "out:float[]", cwd=project,
    )  # fmt: skip
    ext = (project / "native/src/o/o_ext.c").read_text("utf-8")
    assert "y == (" not in ext, ext


#: (id, the method's shape flags, the count local the wrapper declares).
_SHAPES = {
    "out-count": (
        ["--arg-type", "void", "--return-type", "size_t",
         "--out-param", "out:float[]"],
        "size_t y = ",
    ),
    "variable-output": (
        ["--arg-type", "float", "--return-type", "float",
         "--variable-output"],
        "size_t n_out = ",
    ),
}  # fmt: skip


@pytest.mark.parametrize("shape", list(_SHAPES))
def test_a_fragment_testing_another_value_is_still_reported(tmp_path, shape):
    """The marker reads the VALUE tested, per method: a sacred fragment
    comparing its count with something else (``(size_t)0``) does not
    satisfy a declared ``SIZE_MAX`` -- a spelling-only marker took it for
    the sentinel and the report vanished (review of #2561)."""
    flags, local = _SHAPES[shape]
    _ok("new", "q", cwd=tmp_path)
    proj = tmp_path / "q"
    _ok("module", "m", cwd=proj)
    _ok("object", "r", "--module", "m", cwd=proj)
    _ok("method", "r", "surface", "--module", "m", *flags, cwd=proj)
    frag = proj / "native/src/m/m_ext_r.c"
    code = frag.read_text("utf-8")
    at = code.index(local)
    end = code.index("\n", at) + 1
    decoy = f"    if ({local.split()[1]} == (size_t)0) {{ }}\n"
    frag.write_text(code[:end] + decoy + code[end:], encoding="utf-8")
    p = proj / "objects" / "r.toml"
    body = p.read_text("utf-8")
    assert body.count('name = "surface"\n') == 1, body
    p.write_text(
        body.replace(
            'name = "surface"\n',
            'name = "surface"\nerror_sentinel = "SIZE_MAX"\n',
        ),
        encoding="utf-8",
    )
    r = run_cli("apply", cwd=proj)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert decoy in frag.read_text("utf-8")
    assert "declares a refusal value for the count" in out, out


def test_the_stub_states_the_contract(project):
    """The `_core.c` stub's IMPLEMENT note says what to return and how to
    refuse, in the words a variable_output stub uses (`_refuse_note`)."""
    _ok("method", "o", "surface", *SURFACE, cwd=project)
    core = (project / "native/src/o/o_core.c").read_text("utf-8")
    assert (
        "/* <<IMPLEMENT: surface: return the count written into out, or "
        "SIZE_MAX to refuse >> */" in core
    ), core
