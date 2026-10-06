"""gh-1760: `jm apply` replays a module function's params WHOLE.

`apply` rebuilds a throwaway tree from the manifest and renders the bindings
from that tree's manifest. A module function's params used to cross into it as
a positional tuple, ``(name, type, out, default, enum, doc, str_hint)``, and
`_function.run` rebuilt each manifest row from that tuple. A key the tuple had
no slot for was absent from the replayed manifest, so the binding never saw
it -- while the real manifest kept it and `apply` exited 0.

`rank` and `elements_per_sample` (gh-805 §C) were two such keys. Neither has a
CLI flag, so a manifest edit and `apply` is the only way to declare them, and
on this face both were accepted and ignored: the binding lost the rank guard
and the interleave divisor -- handing the kernel ``n`` elements where it
counts ``n / k`` samples, the overrun §C was written to prevent. gh-1493
(`doc`) and gh-1756 (`str_hint`) had each patched the tuple one more slot.

The fix passes each row through as a dict, as gh-432 did for method params,
and `_function.run` stores it verbatim. This file holds the class, not the
two keys: EVERY key `_keys.FUNCTION_PARAM_KEYS` accepts must arrive, with its
value, in the manifest the binding is rendered from. The one hand-written
table here is `_REPRESENTATIVE`, and it cannot fall behind the vocabulary
without failing `test_every_accepted_key_has_a_representative`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest
from _compilers import default_cc

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from _jmrun import run_cli  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit import _function  # noqa: E402
from just_makeit._keys import FUNCTION_PARAM_KEYS  # noqa: E402

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

#: Accepted key -> one param row that carries it with a non-default value.
#: Rows are ordered as they are declared: every defaulted scalar after every
#: required one, which is what the binding's argument parser needs.
_REPRESENTATIVE: dict[str, dict] = {
    "name": {"name": "nm", "type": "double"},
    "type": {"name": "ty", "type": "uint16_t"},
    "out": {"name": "o", "type": "float[]", "out": True},
    "mutable": {"name": "mu", "type": "float[]", "mutable": True},
    "rank": {"name": "r", "type": "float[]", "rank": 1},
    "elements_per_sample": {
        "name": "k",
        "type": "float[]",
        "elements_per_sample": 2,
    },
    "str_hint": {"name": "s", "type": "int8_t[]", "str_hint": "pass bytes"},
    "enum": {"name": "e", "type": "int", "enum": "mode"},
    "doc": {"name": "dc", "type": "double", "doc": "DC marker."},
    "default": {"name": "d", "type": "double", "default": "1.5"},
}


def _jm(*args, cwd):
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, f"jm {' '.join(args)}\n{r.stdout}\n{r.stderr}"
    return r


def test_every_accepted_key_has_a_representative() -> None:
    """A key added to the function-param vocabulary is covered here.

    Without a representative the replay test below cannot ask about it, so
    the gap is named rather than skipped.
    """
    missing = sorted(FUNCTION_PARAM_KEYS - set(_REPRESENTATIVE))
    assert not missing, f"no representative row for: {missing}"
    for key, row in _REPRESENTATIVE.items():
        assert key in row, (key, row)
        assert set(row) <= FUNCTION_PARAM_KEYS, (key, row)


@pytest.fixture(scope="module")
def replayed(tmp_path_factory) -> tuple[list[dict], list[dict]]:
    """(declared rows, the rows the replayed manifest holds after `apply`).

    The replayed rows are read from the TEMP tree, right after
    `_function.run` returns -- that manifest is what `_regenerate_module`
    rendered the binding from, so it is what the renderer received.
    """
    root = tmp_path_factory.mktemp("gh1760")
    _jm("new", "jmp", cwd=root)
    p = root / "jmp"
    _jm("module", "m", cwd=p)
    _jm(
        "function", "f", "--module", "m",
        "--param", "x:double", "--return-type", "int",
        cwd=p,
    )  # fmt: skip
    cfg = C.load(p)
    cfg.setdefault("enum", []).append({"name": "mode", "values": ["a", "b"]})
    (fn,) = C.module_functions(cfg, "m")
    declared = [dict(row) for row in _REPRESENTATIVE.values()]
    fn["params"] = declared
    C.save(p, cfg)

    seen: list[list[dict]] = []
    real_run = _function.run

    def spy(root_, fn_name, module, *a, **kw):
        real_run(root_, fn_name, module, *a, **kw)
        (row,) = [
            f
            for f in C.module_functions(C.load(root_), module)
            if f["name"] == fn_name
        ]
        seen.append([dict(x) for x in row.get("params", [])])

    mp = pytest.MonkeyPatch()
    mp.setattr(_function, "run", spy)
    try:
        _jm("apply", cwd=p)
    finally:
        mp.undo()
    assert len(seen) == 1, seen
    return declared, seen[0]


@pytest.mark.parametrize("key", sorted(_REPRESENTATIVE))
def test_the_key_reaches_the_rendered_manifest(replayed, key: str) -> None:
    """The row carrying *key* arrives with *key*'s declared value."""
    declared, got = replayed
    row = _REPRESENTATIVE[key]
    by_name = {r["name"]: r for r in got}
    assert row["name"] in by_name, f"param {row['name']!r} not replayed"
    assert by_name[row["name"]].get(key) == row[key], (
        f"`{key}` dropped by apply's function replay: declared {row}, "
        f"replayed {by_name[row['name']]}"
    )


def test_every_row_arrives_whole(replayed) -> None:
    """No key is dropped, and none is invented or respelled."""
    declared, got = replayed
    assert got == declared


# -- the build: the guard and the divisor, from the running extension --------

_CALLS = r"""
import json, numpy as np
from jmp.m import f
out = {}
for name, call in {
    "interleaved": lambda: f(np.zeros(6, dtype=np.float32)),
    "2-D": lambda: f(np.zeros((2, 2), dtype=np.float32)),
}.items():
    try:
        out[name] = {"ok": call()}
    except Exception as e:
        out[name] = {"err": type(e).__name__, "msg": str(e)}
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def called(tmp_path_factory) -> dict:
    """A function whose kernel returns the sample count it was handed."""
    root = tmp_path_factory.mktemp("gh1760_build")
    _jm("new", "jmp", cwd=root)
    p = root / "jmp"
    _jm("module", "m", cwd=p)
    _jm(
        "function", "f", "--module", "m",
        "--param", "x:float[]", "--return-type", "int",
        cwd=p,
    )  # fmt: skip
    cfg = C.load(p)
    (fn,) = C.module_functions(cfg, "m")
    fn["params"][0].update(rank=1, elements_per_sample=2)
    C.save(p, cfg)
    _jm("apply", cwd=p)

    body = p / "native" / "src" / "m" / "f.c"
    text = body.read_text(encoding="utf-8")
    stub = "return (int)0; /* placeholder */"
    assert text.count(stub) == 1, text
    body.write_text(text.replace(stub, "return (int)x_len;"), "utf-8")

    build = subprocess.run(["make"], cwd=p, capture_output=True, text=True)
    assert build.returncode == 0, build.stdout[-3000:] + build.stderr[-3000:]
    ext = sysconfig.get_config_var("EXT_SUFFIX")
    so = list(p.rglob(f"m{ext}"))
    assert so, "extension module was not built"
    run = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(p / 'src')!r})\n"
         + _CALLS],
        cwd=p,
        capture_output=True,
        text=True,
    )  # fmt: skip
    assert run.returncode == 0, run.stderr[-3000:]
    return json.loads(run.stdout.strip().splitlines()[-1])


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="needs cmake and a C compiler")
class TestTheBindingHonoursTheKeys:
    def test_elements_per_sample_divides_the_count(self, called):
        """Six floats at two per sample are three samples, not six."""
        assert called["interleaved"] == {"ok": 3}

    def test_rank_refuses_a_2d_array(self, called):
        assert called["2-D"]["err"] == "ValueError", called["2-D"]
        assert "x must be a 1-D array" in called["2-D"]["msg"]
