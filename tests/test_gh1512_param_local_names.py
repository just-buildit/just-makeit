"""gh-1512: a param may not share a name with a C identifier jm's wrapper
declares -- and every identifier a wrapper declares is claimed by that rule.

A method's ``--param y:double`` became the C local ``y`` beside the ``y`` jm
stores the result in, and the tree did not compile (``redefinition of 'y'``)
while ``jm method`` exited 0. The rule that answers it lives in
``_builtins`` (:func:`~just_makeit._builtins.param_name_clash`): the result
local is renamed on collision, everything else is refused up front, on the
CLI and on ``apply``'s replay alike.

A hand-kept list of reserved names is exactly what drifts when an emitter
gains a local, so the first test does not trust the list. It scaffolds every
param-bearing binding shape with **sentinel** param names, pulls every
identifier each wrapper declares out of the generated C, and requires the
rule to claim each one. A local added to an emitter later fails here until
it is reserved -- which is the only way a new collision could be kept out.

The rest pin the behaviour: the refusals and their messages (CLI and
``apply``), the two shapes that must stay legal (doppler's out-param named
``out``, and ``y`` itself), and a compiled run of a project that declares
``y`` everywhere it used to collide.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from _jmrun import run_cli
from test_gh1109_seeded_construction_is_attempted import _pytest_counts

from just_makeit._builtins import param_name_clash, result_local

_NO_TOOLCHAIN = shutil.which("cmake") is None or (
    shutil.which("cc") is None and shutil.which("gcc") is None
)

#: Sentinel param names: nothing jm emits is spelled like these, so every
#: other identifier a wrapper declares is jm's own.
_ARR, _SCA = "zqa", "zqb"

#: ``(method name, extra argv, out-buffer shape, multi-output shape)``.
#: Every method carries an array and a scalar sentinel unless its shape
#: says otherwise; the flags mirror what `jm method` passes to the rule.
_METHODS = (
    ("m_sc", ("--return-type", "double"), False, False),
    ("m_void", (), False, False),
    ("m_neg", ("--return-type", "int", "--error-negative"), False, False),
    ("m_nogil", ("--return-type", "double", "--nogil"), False, False),
    ("m_status", ("--status-return",), False, False),
    ("m_vo", ("--out-type", "float", "--variable-output"), True, False),
    ("m_ot", ("--out-type", "float"), True, False),
    (
        "m_multi",
        (
            "--return-type",
            "double",
            "--multi-output",
            "int",
            "--multi-output",
            "double",
        ),
        False,
        True,
    ),
)
_FUNCTIONS = (
    ("f_sc", ("--return-type", "double"), False),
    ("f_out", ("--out-param", "zqc:float[]"), False),
    ("f_vo", ("--out-type", "float", "--variable-output"), True),
)
_SENTINEL_PARAMS = ("--param", f"{_ARR}:float[]", "--param", f"{_SCA}:double")


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


@pytest.fixture(scope="module")
def sentinel_tree(tmp_path_factory) -> Path:
    """Every param-bearing shape, params named only with sentinels."""
    base = tmp_path_factory.mktemp("gh1512")
    _ok("new", "p", "--no-c-prefix", cwd=base)
    root = base / "p"
    _ok(
        "object",
        "w",
        "--no-step",
        "--init-param",
        f"{_SCA}:int:0",
        cwd=root,
    )
    for name, extra, _, _ in _METHODS:
        _ok("method", "w", name, *_SENTINEL_PARAMS, *extra, cwd=root)
    _ok("module", "mod", cwd=root)
    _ok(
        "object",
        "o",
        "--module",
        "mod",
        "--no-step",
        "--init-param",
        f"{_SCA}:int:0",
        cwd=root,
    )
    _ok(
        "method",
        "o",
        "m_sc",
        "--module",
        "mod",
        *_SENTINEL_PARAMS,
        "--return-type",
        "double",
        cwd=root,
    )
    for name, extra, _ in _FUNCTIONS:
        _ok(
            "function",
            name,
            "--module",
            "mod",
            *_SENTINEL_PARAMS,
            *extra,
            cwd=root,
        )
    return root


#: A C function definition at column 0: return type line(s), then
#: ``name(signature)``, then a body closed by ``}`` at column 0.
_FUNC = re.compile(
    r"^(?:static\s+)?[A-Za-z_][\w \*]*\n(\w+)\(([^)]*)\)\n\{\n(.*?)^\}",
    re.M | re.S,
)
#: A block-scope declaration jm writes: a type, then the declared name,
#: then ``=``, ``;`` or ``[``. Anchored at four-plus spaces so file-scope
#: code and prose in comments are not read as locals.
_DECL = re.compile(
    r"^ {4,}(?:static\s+|const\s+)*"
    r"(?!return\b|else\b|goto\b|case\b)[A-Za-z_]\w*"
    r"(?:\s+[A-Za-z_]\w*)*[\s\*]+\**(\w+)\s*(?:=|;|\[)",
    re.M,
)


def _wrappers(root: Path):
    """Yield ``(function name, declared identifiers)`` for every generated
    wrapper that parses one of the sentinel params."""
    for src in sorted((root / "native" / "src").rglob("*_ext*.c")):
        for m in _FUNC.finditer(src.read_text()):
            name, sig, body = m.groups()
            if f'"{_SCA}"' not in body and f'"{_ARR}"' not in body:
                continue
            declared = set(_DECL.findall(body))
            declared |= {
                re.split(r"[\s\*]+", arg.strip())[-1]
                for arg in sig.split(",")
                if arg.strip() and arg.strip() != "void"
            }
            yield name, declared


def _shape_of(wrapper: str) -> "tuple[bool, bool]":
    """``(outbuf, multi_output)`` of the method/function a wrapper binds.

    Longest name first, so ``m_sc`` is never matched inside another name.
    """
    shapes = [(n, ob, mo) for n, _, ob, mo in _METHODS]
    shapes += [(n, ob, False) for n, _, ob in _FUNCTIONS]
    for name, ob, mo in sorted(shapes, key=lambda s: -len(s[0])):
        if wrapper.endswith(name):
            return ob, mo
    return False, False  # tp_init


def test_every_local_a_wrapper_declares_is_reserved(sentinel_tree: Path):
    """The derivation: each identifier jm declares beside a param is one a
    param of that name would be refused as -- or the renamed result."""
    wrappers = dict(_wrappers(sentinel_tree))
    # Armed: the walk must have found the wrappers and the locals it exists
    # to check. A regex that matched nothing would pass everything below.
    assert len(wrappers) >= len(_METHODS) + len(_FUNCTIONS) + 2, wrappers
    seen = set().union(*wrappers.values())
    for known in (
        "_kwlist",
        "self",
        "args",
        "kwds",
        f"{_ARR}_obj",
        "y",
        "n_out",
    ):
        assert known in seen, (known, sorted(seen))

    sentinels = {_ARR, _SCA, "zqc"}
    params = [(_ARR, "float[]"), (_SCA, "double"), ("zqc", "float[]")]
    unclaimed = []
    for wrapper, declared in sorted(wrappers.items()):
        outbuf, multi = _shape_of(wrapper)
        for ident in sorted(declared - sentinels):
            if ident == result_local([]):
                continue  # renamed on collision, see result_local
            why = param_name_clash(
                ident,
                params + [(ident, "double")],
                outbuf=outbuf,
                multi_output=multi,
            )
            if why is None:
                unclaimed.append(f"{wrapper}: {ident}")
    assert not unclaimed, (
        "a generated wrapper declares a C identifier that a param of the"
        " same name would NOT be refused as -- the tree would not compile."
        " Reserve it in _builtins (gh-1512):\n  " + "\n  ".join(unclaimed)
    )


_REFUSED = [
    (
        ("method", "w", "a", "--param", "self:double"),
        "'self' is a C parameter",
    ),
    (("method", "w", "b", "--param", "_n:double"), "starts with '_'"),
    (
        (
            "method",
            "w",
            "c",
            "--param",
            "x:float[]",
            "--param",
            "x_len:size_t",
        ),
        "the length jm passes to C for array param 'x'",
    ),
    (
        (
            "method",
            "w",
            "d",
            "--param",
            "x:float[]",
            "--param",
            "x_obj:double",
        ),
        "a local jm marshals for array param 'x'",
    ),
    (
        (
            "method",
            "w",
            "e",
            "--param",
            "out:size_t",
            "--out-type",
            "float",
            "--variable-output",
        ),
        "output buffer this binding allocates",
    ),
    (
        (
            "method",
            "w",
            "f",
            "--param",
            "out1:double",
            "--return-type",
            "double",
            "--multi-output",
            "int",
        ),
        "extra outputs this binding returns",
    ),
    (
        ("function", "g", "--module", "mod", "--param", "kwds:int"),
        "'kwds' is a C parameter",
    ),
    (
        ("object", "o2", "--no-step", "--init-param", "args:int:0"),
        "'args' is a C parameter",
    ),
]


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    _ok("new", "p", "--no-c-prefix", cwd=tmp_path)
    root = tmp_path / "p"
    _ok("object", "w", "--no-step", cwd=root)
    _ok("module", "mod", cwd=root)
    return root


@pytest.mark.parametrize("argv, why", _REFUSED, ids=lambda v: str(v)[:40])
def test_a_colliding_param_is_refused_before_any_c(project, argv, why):
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    r = run_cli(*argv, cwd=project)
    assert r.returncode == 1, r.stdout
    assert "collides with the generated C binding" in r.stderr, r.stderr
    assert why in r.stderr, r.stderr
    after = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    assert after == before, "a refused command wrote to the tree"


def test_apply_refuses_the_same_declaration(project: Path):
    """A hand-written table reaches the same gate through the replay."""
    toml = project / "objects" / "w.toml"
    toml.write_text(
        toml.read_text()
        + '\n[[w.methods]]\nname = "m"\nreturn_type = "double"\n'
        + '[[w.methods.params]]\nname = "kwds"\ntype = "double"\n'
    )
    r = run_cli("apply", cwd=project)
    assert r.returncode == 1, r.stdout
    assert "param 'kwds' collides with the generated C binding" in r.stderr


@pytest.mark.parametrize(
    "argv",
    [
        # doppler's cvt.int_to_bin: an out-param called `out` on a shape
        # that declares no `out` of its own.
        (
            "function",
            "h",
            "--module",
            "mod",
            "--param",
            "v:int",
            "--out-param",
            "out:uint8_t[]",
        ),
        (
            "method",
            "w",
            "k",
            "--param",
            "out:double",
            "--return-type",
            "double",
        ),
        ("method", "w", "j", "--param", "y:double", "--return-type", "double"),
        ("method", "w", "q", "--param", "x_len:size_t", "--param", "x:double"),
    ],
    ids=["fn-out-param", "scalar-out", "y", "scalar-x_len"],
)
def test_names_that_do_not_collide_stay_legal(project, argv):
    r = run_cli(*argv, cwd=project)
    assert r.returncode == 0, r.stderr


def test_the_result_local_moves_when_a_param_is_y(project: Path):
    _ok(
        "method",
        "w",
        "m",
        "--param",
        "y:double",
        "--return-type",
        "double",
        cwd=project,
    )
    ext = (project / "native/src/w/w_ext.c").read_text()
    body = ext[ext.index("\nW_m(") :]
    body = body[: body.index("\n}\n")]
    assert re.search(r"^ {4}double y = 0\.0;$", body, re.M), body
    assert re.search(
        r"^ {4}double _y = w_m\(self->handle, y\);$", body, re.M
    ), body
    assert "PyFloat_FromDouble(_y)" in body, body


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_a_project_declaring_y_everywhere_builds_and_runs(project: Path):
    """What the issue reported, compiled: `y` on every result shape."""
    for argv in (
        (
            "method",
            "w",
            "m1",
            "--param",
            "y:double",
            "--return-type",
            "double",
        ),
        (
            "method",
            "w",
            "m2",
            "--param",
            "y:float[]",
            "--return-type",
            "double",
        ),
        # --no-bench: the scaffolded benchmark omits a multi-output
        # method's out pointers whatever the params are called (gh-1523).
        (
            "method",
            "w",
            "m3",
            "--param",
            "y:double",
            "--return-type",
            "double",
            "--multi-output",
            "int",
            "--no-bench",
        ),
        (
            "function",
            "f",
            "--module",
            "mod",
            "--param",
            "v:int",
            "--out-param",
            "out:uint8_t[]",
        ),
    ):
        _ok(*argv, cwd=project)
    out = run_cli("test", cwd=project)
    assert out.returncode == 0, out.stdout[-3000:]
    counts = _pytest_counts(out.stdout)
    assert counts.get("passed", 0) > 0, out.stdout[-3000:]
    assert counts.get("failed", 0) == 0, out.stdout[-3000:]
    assert counts.get("skipped", 0) == 0, out.stdout[-3000:]
