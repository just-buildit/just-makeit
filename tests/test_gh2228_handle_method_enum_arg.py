"""gh-2228: a handle method arg or factory param declaring ``enum`` takes the
enum's choice string, as a create-arg does.

``_emit_method`` passed ``_arg_<name>`` for an enum arg -- the local a
create-arg's validation declares -- but no method shape declared it, and
the parse read the arg as its declared ``int``. The module did not
compile. A factory's ``init_params`` parsed the choice string and passed
the same undeclared ``_arg_<name>``.

Each now parses the string (``s``) and validates it into ``_arg_<name>``
through ``_enumc.validate_c``, the one emitter every face shares, so a
valid choice reaches C as its int and an invalid one raises naming the
choices; the ``.pyi`` annotates it ``str``.

Two gates:

- registration-free, over gh-2207's walk of every method the dispatch keys
  form (which carries an enum row kind): each ``_arg_<name>`` a wrapper
  uses is a local it declares from the lookup, and the stub says ``str``;
- compiled and run: one method of each shape and a factory, each taking a
  choice, where C returns or writes the int it was handed.
"""

from __future__ import annotations

import ast
import contextlib
import io
import re
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _handle  # noqa: E402
from test_gh2207_handle_stub_param_names import (  # noqa: E402
    _BASE,
    _ENUMS,
    _methods,
)

# ── registration-free: every shape the walk forms ───────────────────────────


@pytest.fixture(scope="module")
def choice_cases():
    """``(id, method, wrapper C, {param: annotation})`` for each method in
    the walk that takes an enum choice and that jm accepts."""
    out = []
    for ident, m in _methods():
        if not any(a.get("enum") for a in m["args"]):
            continue
        cfg = {
            "project": {"name": "p"},
            "enum": _ENUMS,
            "module": {"h": {**_BASE, "methods": [m]}},
        }
        try:
            ext = _handle.render_ext(cfg, "h")
            pyi = _handle.render_pyi(cfg, "h")
        except (ValueError, NotImplementedError):
            continue
        body = ext.split("\nH_op(")[1].split("\nstatic ")[0]
        fn = next(
            node
            for node in ast.walk(ast.parse(pyi))
            if isinstance(node, ast.FunctionDef) and node.name == "op"
        )
        anns = {
            a.arg: ast.unparse(a.annotation)
            for a in fn.args.posonlyargs + fn.args.args
            if a.annotation is not None
        }
        out.append((ident, m, body, anns))
    return out


def test_the_walk_reaches_every_shape_with_a_choice(choice_cases):
    """Armed: a choice on each of the six shapes -- scalar args (a), array
    in (b), count in (c), caller's buffer (d), handle-length array (e) and
    ``bytes`` (f)."""
    shapes = set()
    for _ident, m, _body, _anns in choice_cases:
        kinds = {
            "out" if a.get("writable") else "array"
            for a in m["args"]
            if a["type"].endswith("[]")
        }
        ret = m.get("returns")
        if ret == "bytes":
            shapes.add("f")
        elif ret == "float[]" and "out" in kinds:
            shapes.add("d")
        elif ret == "float[]" and not kinds:
            shapes.add("e" if m.get("out_len_fn") else "c")
        elif "array" in kinds:
            shapes.add("b")
        elif not kinds:
            shapes.add("a")
    assert shapes == set("abcdef"), shapes


def test_each_choice_is_validated_into_the_local_the_call_passes(
    choice_cases,
):
    wrong = []
    for ident, m, body, _anns in choice_cases:
        used = set(re.findall(r"\b_arg_(\w+)\b", body))
        declared = set(
            re.findall(r"int _arg_(\w+) = _enum_index\(_enum_zqmode,", body)
        )
        choices = {a["name"] for a in m["args"] if a.get("enum")}
        if used != choices or declared != choices:
            wrong.append(
                f"{ident}: uses _arg_{sorted(used)}, declares"
                f" {sorted(declared)}, for choices {sorted(choices)}"
            )
        for n in choices:
            if f"const char *{n} = " not in body:
                wrong.append(f"{ident}: '{n}' is not parsed as a string")
        if body.count("(choices: zqlo, zqhi)") != len(choices):
            wrong.append(f"{ident}: a refusal does not name the choices")
    assert not wrong, "\n".join(wrong)


def test_the_stub_takes_the_choice_string(choice_cases):
    wrong = [
        f"{ident}: '{a['name']}' is annotated {anns.get(a['name'])}"
        for ident, m, _body, anns in choice_cases
        for a in m["args"]
        if a.get("enum") and anns.get(a["name"]) != "str"
    ]
    assert not wrong, "\n".join(wrong)


def test_a_count_cannot_be_a_choice():
    """Shape (c)'s first argument sizes the result: a choice there is
    refused, naming the argument, rather than parsed as a count."""
    from just_makeit._report import Refusal

    m = {
        "name": "op",
        "fn": "h_op",
        "returns": "float[]",
        "args": [{"name": "zqa", "type": "int", "enum": "zqmode"}],
    }
    cfg = {
        "project": {"name": "p"},
        "enum": _ENUMS,
        "module": {"h": {**_BASE, "methods": [m]}},
    }
    with pytest.raises(Refusal, match="'zqa' is the count"):
        _handle.render_ext(cfg, "h")


# ── compiled: a valid choice reaches C as its int ───────────────────────────

_MO_H = """\
#ifndef MO_H
#define MO_H
#include <stddef.h>
typedef struct mo mo_t;
mo_t *mo_open(void);
mo_t *mo_open_at(int level);
void mo_close(mo_t *h);
void mo_set(mo_t *h, int level);
int mo_get(const mo_t *h);
size_t mo_push(mo_t *h, const float *x, size_t n, int level);
size_t mo_fill(mo_t *h, float *out, size_t n, int level);
size_t mo_scale(mo_t *h, const float *in, size_t n_in, int level,
                float *out, size_t max_out);
size_t mo_len(const mo_t *h);
size_t mo_at(mo_t *h, int level, float *out);
size_t mo_blob(mo_t *h, int level, void *out);
#endif
"""

# Every function hands back the int it was given, so the test reads what
# reached C: `push` as `10 * n + level`, the arrays as `level` per element.
_MO_C = """\
#include "mo/mo.h"
#include <stdlib.h>
struct mo { int level; };
mo_t *mo_open(void) { return calloc(1, sizeof(mo_t)); }
mo_t *mo_open_at(int level) {
    mo_t *h = mo_open();
    if (h) h->level = level;
    return h;
}
void mo_close(mo_t *h) { free(h); }
void mo_set(mo_t *h, int level) { h->level = level; }
int mo_get(const mo_t *h) { return h->level; }
size_t mo_push(mo_t *h, const float *x, size_t n, int level) {
    (void)h; (void)x;
    return 10 * n + (size_t)level;
}
size_t mo_fill(mo_t *h, float *out, size_t n, int level) {
    (void)h;
    for (size_t i = 0; i < n; i++) out[i] = (float)level;
    return n;
}
size_t mo_scale(mo_t *h, const float *in, size_t n_in, int level,
                float *out, size_t max_out) {
    (void)h;
    size_t k = 0;
    for (; k < n_in && k < max_out; k++) out[k] = in[k] * (float)level;
    return k;
}
size_t mo_len(const mo_t *h) { (void)h; return 2; }
size_t mo_at(mo_t *h, int level, float *out) {
    (void)h;
    out[0] = out[1] = (float)level;
    return 2;
}
size_t mo_blob(mo_t *h, int level, void *out) {
    (void)h;
    ((unsigned char *)out)[0] = (unsigned char)level;
    return 1;
}
"""

_LEVEL = {"name": "level", "type": "int", "enum": "level"}

_MODULE = {
    "kind": "handle",
    "backing": "mo",
    "header": "mo/mo.h",
    "type_name": "Mo",
    "create_fn": "mo_open",
    "close_fn": "mo_close",
    "factories": [
        {"name": "make", "create_fn": "mo_open_at", "init_params": [_LEVEL]},
    ],
    "methods": [
        # (a), with a default choice.
        {
            "name": "set",
            "fn": "mo_set",
            "args": [{**_LEVEL, "default": "low"}],
        },
        {"name": "get", "fn": "mo_get", "returns": "int"},
        # (b)
        {
            "name": "push",
            "fn": "mo_push",
            "returns": "size_t",
            "args": [{"name": "x", "type": "float[]"}, _LEVEL],
        },
        # (c)
        {
            "name": "fill",
            "fn": "mo_fill",
            "returns": "float[]",
            "args": [{"name": "n", "type": "size_t"}, _LEVEL],
        },
        # (d)
        {
            "name": "scale",
            "fn": "mo_scale",
            "returns": "float[]",
            "args": [
                {"name": "x", "type": "float[]"},
                {"name": "out", "type": "float[]", "writable": True},
                _LEVEL,
            ],
        },
        # (e)
        {
            "name": "at",
            "fn": "mo_at",
            "returns": "float[]",
            "out_len_fn": "mo_len",
            "args": [_LEVEL],
        },
        # (f)
        {
            "name": "blob",
            "fn": "mo_blob",
            "returns": "bytes",
            "out_len_fn": "mo_len",
            "args": [_LEVEL],
        },
    ],
}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The module, applied by `jm apply`, compiled and imported; a failure
    is returned so each test FAILS on it by name."""
    if default_cc() is None:
        pytest.skip("no C compiler available")
    from test_handle_build import _compile_import, _placed

    tmp = tmp_path_factory.mktemp("gh2228")
    r = run_cli("new", "proj", "--object", "widget", cwd=tmp)
    assert r.returncode == 0, r.stderr
    root = tmp / "proj"
    cfg = C.load(root)
    cfg.setdefault("enum", []).append(
        {"name": "level", "values": ["off", "low", "high"]}
    )
    cfg.setdefault("module", {})["mo"] = _placed(_MODULE, root)
    C.save(root, cfg)
    applied = run_cli("apply", cwd=root)
    if applied.returncode != 0:
        return root, f"jm apply failed:\n{applied.stderr}"
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return root, _compile_import(root, "mo", _MO_H, _MO_C)
    except subprocess.CalledProcessError as exc:
        return root, f"the binding did not compile:\n{exc.stderr}"


def _built(built):
    root, mod = built
    assert not isinstance(mod, str), mod
    return root, mod


def test_a_valid_choice_reaches_c_as_its_int(built):
    import numpy as np

    _, mod = _built(built)
    h = mod.Mo()
    h.set("high")
    assert h.get() == 2
    h.set(level="off")
    assert h.get() == 0
    h.set()  # the declared default, "low"
    assert h.get() == 1
    assert h.push(np.zeros(3, np.float32), "high") == 32
    assert h.fill(3, "low").tolist() == [1.0, 1.0, 1.0]
    x = np.array([1.0, 2.0], np.float32)
    out = np.zeros(2, np.float32)
    assert h.scale(x, out, "high").tolist() == [2.0, 4.0]
    assert h.at("high").tolist() == [2.0, 2.0]
    assert h.blob("low") == b"\x01"
    assert mod.make("high").get() == 2


def test_an_invalid_choice_raises_naming_the_choices(built):
    import numpy as np

    _, mod = _built(built)
    h = mod.Mo()
    calls = (
        lambda: h.set("loud"),
        lambda: h.push(np.zeros(1, np.float32), "loud"),
        lambda: h.fill(1, "loud"),
        lambda: h.scale(
            np.zeros(1, np.float32), np.zeros(1, np.float32), "loud"
        ),
        lambda: h.at("loud"),
        lambda: h.blob("loud"),
        lambda: mod.make("loud"),
    )
    for call in calls:
        with pytest.raises(
            ValueError,
            match=r"invalid level 'loud' \(choices: off, low, high\)",
        ):
            call()
    assert h.get() == 0, "a refused choice must not reach C"


def test_the_built_stub_names_each_choice_str(built):
    root, _ = _built(built)
    pyi = next(root.rglob("mo.pyi")).read_text()
    for sig in (
        "def set(self, level: str = ...) -> None:",
        "def fill(self, n: int, level: str) -> NDArray[Any]:",
        "def at(self, level: str, /) -> NDArray[Any]:",
        "def blob(self, level: str, /) -> bytes:",
        "def make(level: str, /) -> Mo:",
    ):
        assert sig in pyi, (sig, pyi)
