"""gh-2211: a count-in, array-out handle method passes every argument to C.

Shape (c) is ``read(n) -> ndarray``: jm allocates ``n`` elements, calls
``fn(h, out, n)`` and trims to the count C returns. It parsed the count and
nothing else, so a method declaring ``args = [n, gain]`` accepted ``gain``
at the call and never handed it to C: a value the caller passes changed
nothing, and ``jm apply`` said nothing about it.

Every argument after the count now passes through behind it,
``fn(h, out, n, gain, offset)`` -- the order shape (b) threads its scalars
in after ``n_in`` -- and parses with keywords and defaults as (b) and (d)
do once they carry trailing scalars.

The gate reads what reached C: the compiled binding's output is scaled by
``gain`` and shifted by ``offset``, so a dropped or reordered argument
changes the numbers. The ``.pyi`` names what the binding takes.
"""

from __future__ import annotations

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

_GEN_H = """\
#ifndef GEN_H
#define GEN_H
#include <stddef.h>
typedef struct gen gen_t;
gen_t *gen_open(void);
void gen_close(gen_t *g);
size_t gen_fill(gen_t *g, float *out, size_t n, float gain, float offset);
#endif
"""

# `out[i] = gain * (i + 1) + offset`: each argument moves the result in a
# way the other cannot imitate.
_GEN_C = """\
#include "gen/gen.h"
#include <stdlib.h>
struct gen { int unused; };
gen_t *gen_open(void) { return calloc(1, sizeof(gen_t)); }
void gen_close(gen_t *g) { free(g); }
size_t gen_fill(gen_t *g, float *out, size_t n, float gain, float offset) {
    (void)g;
    for (size_t i = 0; i < n; i++)
        out[i] = gain * (float)(i + 1) + offset;
    return n;
}
"""

_MODULE = {
    "kind": "handle",
    "backing": "gen",
    "header": "gen/gen.h",
    "type_name": "Gen",
    "create_fn": "gen_open",
    "close_fn": "gen_close",
    "methods": [
        {
            "name": "fill",
            "fn": "gen_fill",
            "returns": "float[]",
            "args": [
                {"name": "n", "type": "size_t"},
                {"name": "gain", "type": "float"},
                {"name": "offset", "type": "float", "default": "0.0f"},
            ],
        }
    ],
}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The module, applied by `jm apply`, compiled and imported.

    A failed apply or compile is returned rather than raised, so each test
    FAILS on it by name instead of erroring in setup.
    """
    if default_cc() is None:
        pytest.skip("no C compiler available")
    from test_handle_build import _compile_import, _placed

    tmp = tmp_path_factory.mktemp("gh2211")
    r = run_cli("new", "proj", "--object", "widget", cwd=tmp)
    assert r.returncode == 0, r.stderr
    root = tmp / "proj"
    cfg = C.load(root)
    cfg.setdefault("module", {})["gen"] = _placed(_MODULE, root)
    C.save(root, cfg)
    applied = run_cli("apply", cwd=root)
    if applied.returncode != 0:
        return root, f"jm apply failed:\n{applied.stderr}"
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return root, _compile_import(root, "gen", _GEN_H, _GEN_C)
    except subprocess.CalledProcessError as exc:
        return root, f"the binding did not compile:\n{exc.stderr}"


def _built(built):
    root, mod = built
    assert not isinstance(mod, str), mod
    return root, mod


def test_every_argument_after_the_count_reaches_c(built):
    """The issue's gate: ``gain = 2`` doubles what C writes."""
    _, mod = _built(built)
    g = mod.Gen()
    assert g.fill(3, 2.0).tolist() == [2.0, 4.0, 6.0]
    assert g.fill(3, 1.0).tolist() == [1.0, 2.0, 3.0]
    assert g.fill(2, 2.0, 0.5).tolist() == [2.5, 4.5]


def test_the_arguments_take_keywords_and_defaults(built):
    """Keyword-capable as (b) and (d) are with trailing scalars."""
    _, mod = _built(built)
    g = mod.Gen()
    assert g.fill(n=2, gain=3.0, offset=1.0).tolist() == [4.0, 7.0]
    assert g.fill(2, offset=-1.0, gain=1.0).tolist() == [0.0, 1.0]
    with pytest.raises(TypeError):
        g.fill(2)  # `gain` has no default


def test_the_stub_names_what_the_binding_takes(built):
    """The `.pyi` signature is the binding's, argument for argument."""
    root, _ = _built(built)
    pyi = next(root.rglob("gen.pyi")).read_text()
    sig = re.search(r"def fill\((.*?)\) -> (.+?):", pyi, re.S)
    assert sig is not None, pyi
    params = [p.strip() for p in sig.group(1).split(",")]
    assert params == [
        "self",
        "n: int",
        "gain: float",
        "offset: float = ...",
    ], sig.group(0)
