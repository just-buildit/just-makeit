"""A generating source's setters drop the generator they made stale
(gh-2758).

A composer source with ``[module.X.source.generates]`` builds its generator
lazily (``<Src>_ensure_gen``, through ``bridge_fn``) and caches it in
``self->_gen``. Only a re-``__init__`` and the buffer setters (gh-2274)
dropped it, so ``s.freq = 2e3`` after ``s.steps(64)`` read back ``2e3`` and
went on rendering 1 kHz: the object read one value and rendered another.

Decision: every writer of a source's value -- each field setter (the plain
store, an enum, a ``ranged`` pair, a ``check_fn`` copy, a ``set_fn``) and the
``fs`` setter -- waits for a running ``steps()`` (gh-2272) and drops the
generator, through ``_composer._gen_drop_c``, the one spelling ``tp_init`` and
every ``_replace_<name>`` use too. The wait comes after the value is
converted and before it is written, and the drop after, so a refused ``set_fn``
writes nothing and so keeps its generator where it was. The next ``steps()``
builds it from the new value, which is also what ``__init__`` does: **a
setter restarts the generator** -- ``reset()`` rewinds a generator, a setter
rebuilds one. Said in the generated ``steps`` docstring and the manifest
docs.

GATE: the rendered C (registration-free: every setter of a generating source
is held to wait, write, drop, in that order, and a source without
``generates`` renders none of it), then a compiled source whose generator
starts at ``start + tweak``: steps after setting ``start``, ``fs`` and a
``set_fn`` field render the new value, and a refused ``set_fn`` leaves the
running generator alone.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from _cmake import PYTHON3_ARG
from _jmrun import run_cli

from just_makeit import _composer

sys.path.insert(0, str(Path(__file__).parent))
import test_gh2259_source_delegates_methods as base  # noqa: E402
from test_composer_codegen import _complex_cfg  # noqa: E402
from test_composer_codegen import _gen_cfg  # noqa: E402

MOD = "wfm_compose"


def _gen_module() -> dict:
    """_gen_cfg()'s source plus the writers it lacks: a ``set_fn`` field, a
    ranged one and a ``check_fn`` copy."""
    cfg = copy.deepcopy(_gen_cfg())
    src = cfg["module"][MOD]["source"]
    src["fields"] += [
        {
            "name": "tweak",
            "type": "double",
            "default": "0.0",
            "set_fn": "wfm_set_tweak",
        },
        {"name": "gain", "type": "double", "default": "1.0"},
    ]
    src["ranged"] = [{"name": "gain", "flag": "R_GAIN"}]
    return cfg


def _setters(c: str) -> "dict[str, str]":
    return {
        m.group(1): m.group(0)
        for m in re.finditer(r"static int\n\w+_set_(\w+)\(.*?\n\}\n", c, re.S)
    }


class TestTheRenderedSetters:
    def test_every_setter_waits_writes_then_drops(self) -> None:
        c = _composer.render_source_type(_gen_module(), MOD)
        sets = _setters(c)
        # The test saw the shapes it claims to: enum, scalar, ranged, a
        # set_fn field and fs; the buffer setters go through their replace.
        assert {"type", "freq", "gain", "tweak", "fs"} <= set(sets), sets
        for name, body in sets.items():
            if "_replace_" in body:
                continue
            order = [
                body.index("jm_inflight_wait"),
                max(
                    body.rfind("self->src"),
                    body.rfind("self->fs"),
                    body.rfind("wfm_set_tweak("),
                ),
                body.index("self->_gen = NULL"),
            ]
            assert order == sorted(order), (name, body)
            assert body.count("_destroy(self->_gen)") == 1, (name, body)

    def test_a_refused_set_fn_drops_nothing(self) -> None:
        c = _composer.render_source_type(_gen_module(), MOD)
        body = _setters(c)["tweak"]
        refusal = body.index("PyErr_SetString(PyExc_ValueError, _why)")
        assert refusal < body.index("self->_gen = NULL"), body

    def test_the_drop_is_one_spelling(self) -> None:
        c = _composer.render_source_type(_gen_module(), MOD)
        drop = _composer._gen_drop_c("wfm_synth_destroy")
        # Setters, replaces and tp_init alike.
        assert c.count(drop) >= len(_setters(c)), c.count(drop)
        assert "{ wfm_synth_destroy(self->_gen); self->_gen = NULL; }" not in c

    def test_a_source_without_a_generator_has_nothing_to_drop(self) -> None:
        c = _composer.render_source_type(_complex_cfg(), MOD)
        for name, body in _setters(c).items():
            assert "_gen" not in body, (name, body)
            assert "jm_inflight_wait" not in body, (name, body)


#: Run in the built project. The generator's sample is its phase, advancing
#: by one a sample from ``start + tweak``, so which generator ran is read off
#: the numbers.
_DRIVER = r"""
import json, sys
sys.path.insert(0, "src")
from dlg.mix.mix import Src

def first(s):
    return float(s.steps(1)[0].real)

out = {}
s = Src(start=10.0)
out["first"] = first(s)                   # builds the generator: 10
out["continues"] = first(s)               # same generator: 11
s.start = 20.0
out["start"] = first(s)                   # rebuilt from 20: 20
out["start_continues"] = first(s)         # 21
s.fs = 2e6
out["fs"] = first(s)                      # rebuilt again: 20
first(s)
s.tweak = 5.0                             # set_fn: written, rebuilt
out["tweak"] = first(s)                   # 25
first(s)
try:
    s.tweak = -1.0                        # refused: nothing written
except ValueError as e:
    out["why"] = str(e)
out["refused_kept_its_place"] = first(s)  # the running generator: 27
out["tweak_kept"] = s.tweak
print(json.dumps(out))
"""


def _project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(
        base,
        "MIX_H",
        base.MIX_H.replace(
            "typedef struct { double start; } src_t;",
            "typedef struct { double start; double tweak; } src_t;",
        ),
    )
    monkeypatch.setattr(
        base,
        "BRIDGE_C",
        base.BRIDGE_C.replace(
            f"return {base.PKG}_gen_create(s->start);",
            f"return {base.PKG}_gen_create(s->start + s->tweak);",
        )
        + """const char *src_set_tweak(src_t *s, double v)
{
  if (v < 0.0)
    return "tweak must be >= 0";
  s->tweak = v;
  return NULL;
}
""",
    )
    table = base._composer_table

    def with_tweak(methods):
        t = table(methods)
        t["source"]["fields"].append(
            {
                "name": "tweak",
                "type": "double",
                "default": "0.0",
                "set_fn": "src_set_tweak",
            }
        )
        return t

    monkeypatch.setattr(base, "_composer_table", with_tweak)
    return base._scaffold(tmp_path, shapes=["prod"])


@pytest.mark.slow
@pytest.mark.skipif(base._NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_a_setter_rebuilds_the_generator(tmp_path, monkeypatch) -> None:
    proj = _project(tmp_path, monkeypatch)
    base._ok(run_cli("apply", cwd=proj))

    def run(cmd: list) -> None:
        r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True)
        assert r.returncode == 0, (cmd, r.stdout[-4000:], r.stderr[-4000:])

    run(["cmake", "-S", ".", "-B", "build", PYTHON3_ARG])
    run(["cmake", "--build", "build", "--parallel", "4"])
    driver = proj / "drive_gh2758.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(driver)],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(r.stdout)
    assert got == {
        "first": 10.0,
        "continues": 11.0,
        "start": 20.0,
        "start_continues": 21.0,
        "fs": 20.0,
        "tweak": 25.0,
        "why": "tweak must be >= 0",
        "refused_kept_its_place": 27.0,
        "tweak_kept": 5.0,
    }, got
