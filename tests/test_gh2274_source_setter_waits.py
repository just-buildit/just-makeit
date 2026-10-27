"""gh-2274: a composer source's setter replaces a buffer a generator may use.

A composer source that declares ``generates`` runs ``steps()`` with the GIL
released, on a generator built lazily from ``self->src`` (``bridge_fn(&src)``).
Three setter shapes freed a buffer in ``self->src`` -- a ``bytes`` field, a
complex stream, an owned pointer -- and installed the new one, holding the GIL
but not waiting for a ``steps()`` already running on another thread; and none
dropped the generator, so after ``src.x = ...`` ``steps()`` kept running the
generator built from the OLD config. Worse, ``_attach_bytes`` and the
complex-stream attach freed the old buffer BEFORE converting the new value,
and converting can run Python.

One generated function per field now does it for the setter and for
``__init__`` alike (``_composer._replace_c``): convert into a copy of the
struct (touching nothing live), wait for the running ``steps()``, drop the
generator, then free the old buffer and install the new one -- pure C.

The gates, cheapest first: the rendered C (no direct attach on ``self->src``
remains; in every replace the order is convert, wait, free), then a compiled
source whose ``steps()`` sleeps, driven from two threads.
"""

# gh-1591: the hand-written C in the shared scaffold spells jm's bare derived
# names, so its projects opt out of the prefix `jm new` now defaults to.

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

import test_gh2259_source_delegates_methods as base  # noqa: E402
from just_makeit import _composer  # noqa: E402
from test_composer_codegen import _complex_cfg  # noqa: E402
from test_composer_codegen import _gen_cfg  # noqa: E402

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None


def _render(cfg: dict) -> str:
    return _composer.render_source_type(cfg, "wfm_compose")


def _replaces(c: str) -> "dict[str, str]":
    return {
        m.group(1): m.group(0)
        for m in re.finditer(
            r"static int\n\w+_replace_(\w+)\(.*?\n\}\n", c, re.S
        )
    }


class TestTheRenderedReplace:
    def test_no_direct_attach_on_the_live_source(self):
        """Registration-free: every field is replaced through its replace.

        A call of an attach on ``&self->src`` is the pre-fix shape, the one
        that freed a buffer under a running generator.
        """
        for cfg in (_gen_cfg(), _complex_cfg()):
            c = _render(cfg)
            direct = re.findall(
                r"_attach_\w+\(\s*(?:\(uint8_t \*\*\))?&self->src", c
            )
            assert not direct, direct
            assert _replaces(c), "no replace rendered: the gate saw nothing"

    def test_every_replace_converts_then_waits_then_frees(self):
        cfg = _gen_cfg()
        fields = cfg["module"]["wfm_compose"]["source"]["fields"]
        fields.append({"name": "acq_code", "type": "uint8_t*", "bytes": True})
        c = _render(cfg)
        reps = _replaces(c)
        assert {"bits", "acq_code"} <= set(reps), sorted(reps)
        for name, body in reps.items():
            order = [
                body.index("_attach_"),
                body.index("jm_inflight_wait"),
                body.index("_gen = NULL"),
                body.index("free(self->src"),
                body.rindex("self->src."),
            ]
            assert order == sorted(order), (name, body)

    def test_a_source_without_a_generator_has_nothing_to_wait_for(self):
        c = _render(_complex_cfg())
        for name, body in _replaces(c).items():
            assert "jm_inflight_wait" not in body, (name, body)
            assert "_gen" not in body, (name, body)

    def test_the_new_value_is_converted_into_a_copy(self):
        for name, body in _replaces(_render(_gen_cfg())).items():
            head = body[: body.index("_attach_")]
            assert "_t = self->src;" in head, (name, body)
            assert "self->src." not in head.split("_t = self->src;")[1], body


#: Run in the built project: a source whose ``steps()`` sleeps 150 ms a
#: sample, with a setter on another thread while it runs.
_DRIVER = r"""
import json, sys, threading, time
sys.path.insert(0, "src")
from dlg.mix.mix import Src

out = {}
s = Src(start=10.0)
out["first"] = float(s.steps(1)[0].real)          # builds the generator

t = {}


def stepper():
    t["a0"] = time.monotonic()
    s.steps(2)                                    # ~0.3 s, GIL released
    t["a1"] = time.monotonic()


th = threading.Thread(target=stepper)
th.start()
time.sleep(0.1)                                   # steps() is running
t["b0"] = time.monotonic()
s.bits = b"abc"                                   # the setter
t["b1"] = time.monotonic()
th.join()
out["waited"] = t["b1"] >= t["a1"] - 0.02
out["setter_blocked_s"] = round(t["b1"] - t["b0"], 3)
out["bits"] = s.bits.decode()
# the generator was built from the old config; it was dropped, so the next
# steps() starts again from `start`.
out["restarted"] = float(s.steps(1)[0].real)
print(json.dumps(out))
"""


def _threaded_project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(
        base,
        "MIX_H",
        base.MIX_H.replace(
            "typedef struct { double start; } src_t;",
            "typedef struct { double start; uint8_t *bits; size_t n_bits; }"
            " src_t;",
        ).replace(
            "#include <stddef.h>", "#include <stddef.h>\n#include <stdint.h>"
        ),
    )
    table = base._composer_table

    def with_bits(methods):
        t = table(methods)
        t["source"]["fields"].append(
            {"name": "bits", "type": "uint8_t*", "bytes": True}
        )
        return t

    monkeypatch.setattr(base, "_composer_table", with_bits)
    proj = base._scaffold(tmp_path, shapes=["prod"])
    core = proj / "native" / "src" / "gen" / "gen_core.c"
    text = core.read_text(encoding="utf-8")
    loop = (
        "    for (size_t i = 0; i < n; i++)\n"
        f"        output[i] = {base.PKG}_gen_step(state);"
    )
    assert text.count(loop) == 1, text
    # 150 ms a sample, with the GIL released by the binding around it.
    text = text.replace(
        loop,
        "    for (size_t i = 0; i < n; i++) {\n"
        "        usleep(150000);\n"
        f"        output[i] = {base.PKG}_gen_step(state);\n"
        "    }",
        1,
    ).replace("#include", "#include <unistd.h>\n#include", 1)
    core.write_text(text, encoding="utf-8")
    return proj


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_a_setter_waits_for_a_running_steps(tmp_path, monkeypatch):
    proj = _threaded_project(tmp_path, monkeypatch)
    base._ok(run_cli("apply", cwd=proj))

    def run(cmd: list) -> None:
        r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True)
        assert r.returncode == 0, (cmd, r.stdout[-4000:], r.stderr[-4000:])

    run(
        [
            "cmake",
            "-S",
            ".",
            "-B",
            "build",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ]
    )
    run(["cmake", "--build", "build", "--parallel", "4"])
    driver = proj / "drive_gh2274.py"
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
    # Before the fix the setter returned at once, ~0.1 s into a 0.3 s steps().
    assert got["waited"], got
    assert got["bits"] == "abc", got
    # ...and the generator built from the old config was dropped.
    assert got["restarted"] == 10.0, got
