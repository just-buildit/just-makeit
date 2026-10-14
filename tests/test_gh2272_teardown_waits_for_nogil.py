"""gh-2272: a GIL-free call and the teardown that freed under it.

jm 0.105.0 (gh-2259) made ``nogil = true`` really release the GIL on every
shape, and every binding kind also generates an explicit free --
``destroy()``, ``close()``, ``__exit__``. The free ran holding the GIL and
asked nothing, so a ``close()`` on one thread freed the state under a
released call running on another. doppler's ``Writer.write`` crashed with
SIGSEGV in 10 of 10 runs (doppler-dsp/doppler#2294).

The contract (`just_makeit._inflight`): every released region is counted on
the binding struct; the teardown first refuses new calls, then waits with the
GIL released for the count to reach zero, then frees. The gates, cheapest
first:

1. only `_inflight` spells a GIL release in jm's source (registration-free:
   a new generator that releases through a string of its own is refused the
   moment it is written -- gh-2267's two peers were that);
2. every release in a rendered binding of every kind is counted;
3. every kind's teardown refuses, then waits, then frees -- in that order;
4. compiled: a core whose destroy ``abort()``\\ s if a kernel is still
   running survives ``close()`` from another thread, the running call
   returns its value, and a call started during the wait is refused --
   for an object (standalone, fallible ``close``, module member) and a
   handle module;
5. ``apply`` names a fragment rendered before this, which it does not
   re-render.
"""

# gh-1591: this file's hand-written C spells jm's bare derived names, so its
# projects opt out of the prefix `jm new` now defaults to.

from __future__ import annotations

import ast
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

from just_makeit import _capsule  # noqa: E402
from just_makeit import _composer  # noqa: E402
from just_makeit import _inflight  # noqa: E402
from test_capsule_codegen import _cfg as _capsule_cfg  # noqa: E402
from test_composer_codegen import _gen_cfg  # noqa: E402
from test_composer_codegen import TestRealtimeStream  # noqa: E402
from test_gh2259_batch_method_faces import SHAPES  # noqa: E402

PKG = SRC / "just_makeit"

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


#: The helper every binding carries; its own wait releases the GIL, and is
#: no call's.
_HELPER = re.compile(
    r"#ifndef JM_INFLIGHT_DEFINED\n.*?#endif /\* JM_INFLIGHT_DEFINED \*/",
    re.S,
)


def _uncounted(c: str) -> list[str]:
    """Each ``Py_BEGIN``/``Py_END_ALLOW_THREADS`` not wrapped by the count.

    The count must be taken on the line BEFORE the release and given back
    on the line AFTER the reacquire: anything between them runs with the
    GIL dropped, or holding it while the count says otherwise.
    """
    lines = [ln.strip() for ln in _HELPER.sub("", c).splitlines()]
    bad = []
    for i, ln in enumerate(lines):
        if ln == "Py_BEGIN_ALLOW_THREADS" and not lines[i - 1].startswith(
            "jm_inflight_enter(&"
        ):
            bad.append("\n".join(lines[max(0, i - 3) : i + 2]))
        if ln == "Py_END_ALLOW_THREADS" and not lines[i + 1].startswith(
            "jm_inflight_leave(&"
        ):
            bad.append("\n".join(lines[max(0, i - 1) : i + 3]))
    return bad


def _fn(c: str, name: str) -> str:
    """The body of the C function *name* defined in *c*."""
    m = re.search(rf"^{re.escape(name)}\(.*?^}}$", c, re.S | re.M)
    assert m, f"{name} is not defined here"
    return m.group(0)


def _in_order(body: str, *steps: str) -> None:
    at = [body.find(s) for s in steps]
    assert -1 not in at and at == sorted(at), (steps, at, body)


# ── 1. one owner of the release ───────────────────────────────────────────


def test_only_inflight_writes_the_release():
    """A string constant that renders the release statement lives in
    `_inflight` and nowhere else. A line of code ends in a newline; prose
    and refusal messages naming the macro do not, and are not code."""
    offenders = []
    for py in sorted(PKG.rglob("*.py")):
        # `templates/` holds `.py` files with `<<slot>>` placeholders: text
        # jm renders, not jm's own source.
        if py.name == "_inflight.py" or "templates" in py.parts:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        docs = {
            id(n.body[0].value)
            for n in ast.walk(tree)
            if isinstance(
                n,
                (
                    ast.Module,
                    ast.ClassDef,
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                ),
            )
            and n.body
            and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and id(node) not in docs
                and isinstance(node.value, str)
                and re.search(r"Py_(BEGIN|END)_ALLOW_THREADS\n", node.value)
            ):
                offenders.append(f"{py.relative_to(PKG)}:{node.lineno}")
    assert not offenders, (
        "these render a GIL release without the in-flight count "
        "(gh-2272): build it with _inflight.region / opening / closing\n"
        + "\n".join(offenders)
    )


# ── 2/3. every kind: counted, and its teardown waits ──────────────────────


@pytest.fixture(scope="module")
def object_ext(tmp_path_factory) -> str:
    """An object declaring a released method of every shape but a borrow
    (a lending object parks its free for tp_dealloc instead, gh-2187)."""
    root = tmp_path_factory.mktemp("obj")
    _ok("new", "mp", "--no-c-prefix", cwd=root)
    proj = root / "mp"
    _ok("object", "o", "--state", "k:double:0.0", cwd=proj)
    for name, flags in SHAPES.items():
        if name != "borrow":
            _ok("method", "o", name, *flags, "--nogil", cwd=proj)
    return (proj / "native/src/o/o_ext.c").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def handle_ext(tmp_path_factory) -> str:
    from test_handle_build import _placed, _ringbuf_module
    from just_makeit import _config as C
    from just_makeit._apply import run as apply_run
    from just_makeit._new import run as new_run

    tmp = tmp_path_factory.mktemp("handle")
    new_run("proj", tmp, ["widget"], [("gain", "float", "0.0f")])
    cfg = C.load(tmp)
    cfg.setdefault("module", {})["ringbuf"] = _placed(_ringbuf_module(), tmp)
    C.save(tmp, cfg)
    apply_run(tmp)
    return (tmp / "native/src/ringbuf/ringbuf_ext.c").read_text(
        encoding="utf-8"
    )


def _capsule_ext() -> str:
    return _capsule.render_ext(_capsule_cfg(nogil=True), "ddc_fn")


def _composer_ext() -> str:
    return _composer.render_composer_type(
        TestRealtimeStream()._rt_cfg(), "wfm_compose"
    ) + _composer.render_source_type(_gen_cfg(), "wfm_compose")


class TestEveryReleaseIsCounted:
    def test_object(self, object_ext):
        assert object_ext.count("jm_inflight_enter(&self") >= len(SHAPES) - 1
        assert not _uncounted(object_ext)

    def test_handle(self, handle_ext):
        assert "jm_inflight_enter(&self" in handle_ext
        assert not _uncounted(handle_ext)

    def test_capsule(self):
        c = _capsule_ext()
        assert "jm_inflight_enter(&w" in c
        assert not _uncounted(c)

    def test_composer(self):
        c = _composer_ext()
        # execute, the iterator's block, its pace, and a source's steps.
        assert c.count("jm_inflight_enter(&self") == 4, c
        assert not _uncounted(c)

    def test_the_gate_sees_an_uncounted_release(self):
        """The detector itself, on the shape it exists to refuse."""
        assert _uncounted(
            "    Py_BEGIN_ALLOW_THREADS\n    k();\n"
            "    Py_END_ALLOW_THREADS\n    x();\n"
        )


class TestTheTeardownRefusesThenWaitsThenFrees:
    def test_object_destroy(self, object_ext):
        _in_order(
            _fn(object_ext, "O_destroy"),
            "self->handle = NULL;",
            "jm_inflight_wait(&self->_jm_inflight)",
            "o_destroy(_jm_st);",
        )

    def test_object_dealloc_frees_the_lock(self, object_ext):
        assert "jm_inflight_free(&self->_jm_inflight)" in _fn(
            object_ext, "O_dealloc"
        )

    def test_handle_close(self, handle_ext):
        _in_order(
            _fn(handle_ext, "Ring_close"),
            "self->closed = 1;",
            "jm_inflight_wait(&self->_jm_inflight)",
            "ringbuf_close(self->h)",
        )

    def test_handle_reinit(self, handle_ext):
        _in_order(
            _fn(handle_ext, "Ring_init"),
            "self->closed = 1;",
            "jm_inflight_wait(&self->_jm_inflight)",
            "ringbuf_close(self->h)",
        )

    def test_capsule_destroy(self):
        _in_order(
            _fn(_capsule_ext(), "_fn_ddcr_destroy"),
            "w->destroyed = 1;",
            "jm_inflight_wait(&w->_jm_inflight)",
            "ddcr_destroy(w->state);",
        )

    def test_composer_close(self):
        c = _composer.render_composer_type(
            TestRealtimeStream()._rt_cfg(), "wfm_compose"
        )
        body = _fn(c, re.search(r"^(\w+_close)\(", c, re.M).group(1))
        _in_order(
            body,
            "self->destroyed = 1;",
            "jm_inflight_wait(&self->_jm_inflight)",
            "(self->state);",
        )

    def test_a_source_reinit_waits_before_dropping_its_generator(self):
        c = _composer.render_source_type(_gen_cfg(), "wfm_compose")
        body = _fn(c, re.search(r"^(\w+_init)\(", c, re.M).group(1))
        _in_order(body, "jm_inflight_wait(&self->_jm_inflight)", "_gen = NULL")

    def test_without_nogil_the_teardown_is_unchanged(self, tmp_path):
        _ok("new", "p", "--no-c-prefix", cwd=tmp_path)
        proj = tmp_path / "p"
        _ok("object", "q", cwd=proj)
        _ok("method", "q", "m", "--return-type", "int", cwd=proj)
        c = (proj / "native/src/q/q_ext.c").read_text(encoding="utf-8")
        assert _inflight.FIELD not in c


# ── 4. compiled: close() from another thread ──────────────────────────────

#: The kernel sleeps half a second with the GIL released, flagging that it
#: runs; the core's destroy aborts if it is called while it does. So the
#: process dies (SIGABRT) exactly when a teardown frees under a running call.
_RUNNING = "static volatile int jm_test_running;\n"
_SLEEP = """
    jm_test_running = 1;
    struct timespec half = {0, 500000000L};
    nanosleep(&half, NULL);
    jm_test_running = 0;
"""
_TRIP = """    if (jm_test_running) {
        fprintf(stderr, "FREED UNDER A RUNNING KERNEL\\n");
        abort();
    }
"""
_PRELUDE = (
    "#define _POSIX_C_SOURCE 199309L\n"
    "#include <stdio.h>\n#include <stdlib.h>\n#include <time.h>\n"
)

#: Thread A runs the kernel; B closes 0.1 s in; C starts a call 0.1 s
#: after that, while B waits. Prints A's result, C's, and B's wait.
PROBE = """
import sys, threading, time
sys.path.insert(0, "src")
sys.path.insert(0, ".")
{imp}
w = {make}
res = {{}}
def call(tag):
    try:
        res[tag] = w.work()
    except Exception as e:
        res[tag] = type(e).__name__
a = threading.Thread(target=call, args=("a",)); a.start()
time.sleep(0.1)
def close():
    t0 = time.monotonic()
    w.{close}()
    res["waited"] = time.monotonic() - t0
b = threading.Thread(target=close); b.start()
time.sleep(0.1)
call("c")
a.join(); b.join()
print(res["a"], res["c"], "waited" if res["waited"] > 0.2 else "freed")
"""


def _run_probe(proj: Path, imp: str, make: str, close: str) -> str:
    script = proj / "probe.py"
    script.write_text(
        PROBE.format(imp=imp, make=make, close=close), encoding="utf-8"
    )
    out = subprocess.run(
        [sys.executable, str(script)],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, (out.returncode, out.stderr[-2000:])
    return out.stdout.strip()


def _arm_core(core: Path, destroy: str) -> None:
    text = core.read_text(encoding="utf-8")
    head = f"{destroy}(w_state_t *state)\n{{\n"
    assert text.count(head) == 1, text
    core.write_text(
        _PRELUDE + _RUNNING + text.replace(head, head + _TRIP),
        encoding="utf-8",
    )


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
@pytest.mark.parametrize("variant", ["standalone", "close_int", "module"])
def test_close_from_another_thread_waits(tmp_path, variant):
    kfile = tmp_path / "k.c"
    kfile.write_text(
        "int work(void)\n{" + _SLEEP + "    return 7;\n}\n", encoding="utf-8"
    )
    _ok("new", "p", "--no-c-prefix", cwd=tmp_path)
    proj = tmp_path / "p"
    where = []
    if variant == "module":
        _ok("module", "dsp", cwd=proj)
        where = ["--module", "dsp"]
    _ok("object", "w", *where, "--state", "k:int:0", cwd=proj)
    _ok(
        "method",
        "w",
        "work",
        "--return-type",
        "int",
        "--nogil",
        "--impl",
        f"{kfile}::work",
        cwd=proj,
    )
    close = "destroy"
    if variant == "close_int":
        frag = proj / "objects" / "w.toml"
        frag.write_text(
            frag.read_text(encoding="utf-8")
            + '\n[w.destroy]\nname = "close"\nreturns = "int"\n',
            encoding="utf-8",
        )
        _ok("apply", cwd=proj)
        close = "close"
    core = proj / "native" / "src" / "w" / "w_core.c"
    if variant == "close_int":
        text = core.read_text(encoding="utf-8")
        core.write_text(
            text.replace(
                "w_destroy(w_state_t *state)\n{\n    free(state);\n}",
                "w_destroy(w_state_t *state)\n{\n    free(state);\n"
                "    return 0;\n}",
            ),
            encoding="utf-8",
        )
    _arm_core(core, "w_destroy")
    build = subprocess.run(
        ["make", f"PYTHON={sys.executable}"],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert build.returncode == 0, build.stderr[-3000:]
    imp = "from p.dsp import W" if variant == "module" else "from p import W"
    got = _run_probe(proj, imp, "W(k=0)", close)
    assert got == "7 RuntimeError waited", got


_SLOW_H = """\
#ifndef SLOW_H
#define SLOW_H
typedef struct slow slow_t;
slow_t *slow_open(int k);
void slow_close(slow_t *s);
int slow_work(slow_t *s);
#endif
"""

_SLOW_C = (
    _PRELUDE
    + _RUNNING
    + """#include "slow/slow.h"
struct slow { int k; };
slow_t *slow_open(int k) {
    slow_t *s = calloc(1, sizeof *s);
    if (s) s->k = k;
    return s;
}
void slow_close(slow_t *s) {
"""
    + _TRIP
    + """    free(s);
}
int slow_work(slow_t *s) {"""
    + _SLEEP
    + """    return 7 + s->k;
}
"""
)


@pytest.mark.slow
@pytest.mark.skipif(default_cc() is None, reason="no C compiler")
def test_a_handle_close_from_another_thread_waits(tmp_path):
    from test_handle_build import _compile_import, _placed
    from just_makeit import _config as C
    from just_makeit._apply import run as apply_run
    from just_makeit._new import run as new_run

    new_run("proj", tmp_path, ["widget"], [("gain", "float", "0.0f")])
    cfg = C.load(tmp_path)
    cfg.setdefault("module", {})["slow"] = _placed(
        {
            "kind": "handle",
            "backing": "slow",
            "header": "slow/slow.h",
            "type_name": "Slow",
            "create_fn": "slow_open",
            "close_fn": "slow_close",
            "create_args": [{"name": "k", "type": "int"}],
            "methods": [
                {
                    "name": "work",
                    "fn": "slow_work",
                    "returns": "int",
                    "nogil": True,
                }
            ],
        },
        tmp_path,
    )
    C.save(tmp_path, cfg)
    apply_run(tmp_path)
    # Compiled to `tmp_path/slow<EXT_SUFFIX>`, which the probe imports.
    _compile_import(tmp_path, "slow", _SLOW_H, _SLOW_C)
    got = _run_probe(tmp_path, "import slow", "slow.Slow(0)", "close")
    assert got == "7 RuntimeError waited", got


# ── 5. a fragment rendered before this ────────────────────────────────────


def test_apply_names_a_fragment_that_does_not_count(tmp_path):
    """A sacred module fragment only gains MISSING members, so `apply`
    does not re-render a released body to count it -- it names it, as
    gh-2259 named a held-GIL one. Simulated by removing the count from a
    fresh render: what a 0.105.0 fragment looks like."""
    _ok("new", "p", "--no-c-prefix", cwd=tmp_path)
    proj = tmp_path / "p"
    _ok("module", "dsp", cwd=proj)
    _ok("object", "mx", "--module", "dsp", cwd=proj)
    _ok("method", "mx", "w", "--return-type", "int", "--nogil", cwd=proj)
    frag = proj / "native" / "src" / "dsp" / "dsp_ext_mx.c"
    text = frag.read_text(encoding="utf-8")
    old = re.sub(
        r"^\s*jm_inflight_(enter|leave)\(&self->_jm_inflight\);\n",
        "",
        text,
        flags=re.M,
    )
    assert old != text
    frag.write_text(old, encoding="utf-8")
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stderr
    said = r.stdout + r.stderr
    assert (
        "w: the manifest declares nogil and this fragment does not count"
        in said
    ), said
