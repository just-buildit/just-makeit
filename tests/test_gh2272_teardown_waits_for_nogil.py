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


@pytest.fixture(scope="module")
def lending_ext(tmp_path_factory) -> str:
    """The borrow shape alone: a lending object, whose teardown parks the
    state and NULLs ``self->handle`` just as a waiting one does."""
    root = tmp_path_factory.mktemp("lend")
    _ok("new", "mp", "--no-c-prefix", cwd=root)
    proj = root / "mp"
    _ok("object", "o", "--state", "k:double:0.0", cwd=proj)
    _ok("method", "o", "borrow", *SHAPES["borrow"], "--nogil", cwd=proj)
    return (proj / "native/src/o/o_ext.c").read_text(encoding="utf-8")


def _tails(c: str) -> "dict[str, str]":
    """Each function's text after its LAST ``jm_inflight_leave``."""
    out = {}
    for m in re.finditer(r"^(\w+)\(.*?^}$", c, re.S | re.M):
        body = m.group(0)
        at = body.rfind("jm_inflight_leave(")
        if at >= 0:
            out[m.group(1)] = body[at:]
    return out


class TestNothingReadsTheHandleAfterTheRegion:
    """The teardown NULLs ``self->handle`` before it waits, so the running
    call reads its state through the local hoisted before the release
    (`_inflight.hoist_handle`). Code after the region runs once the
    teardown may already have NULLed it: a ``self->handle`` there would
    read NULL (review of #2275)."""

    @pytest.mark.parametrize("fixture", ["object_ext", "lending_ext"])
    def test_no_tail_reads_the_handle(self, request, fixture):
        tails = _tails(request.getfixturevalue(fixture))
        assert tails, "no released method was rendered"
        bad = {n: t for n, t in tails.items() if "self->handle" in t}
        assert not bad, bad

    def test_the_kernel_reads_the_hoisted_local(self, object_ext):
        for name, body in _tails(object_ext).items():
            assert "self->handle" not in body
        region = re.findall(
            r"Py_BEGIN_ALLOW_THREADS\n(.*?)Py_END_ALLOW_THREADS",
            _HELPER.sub("", object_ext),
            re.S,
        )
        assert region and not [r for r in region if "self->handle" in r]

    def test_the_gate_sees_a_tail_that_reads_it(self):
        c = (
            "O_m(OObject *self)\n{\n"
            "    jm_inflight_leave(&self->_jm_inflight);\n"
            "    k(self->handle);\n}\n"
        )
        assert "self->handle" in _tails(c)["O_m"]


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_every_released_shape_compiles(tmp_path):
    """The hoisted handle is ``void *``; a shape whose call dereferenced
    ``self->handle`` would not compile through it. Every shape, built."""
    _ok("new", "mp", "--no-c-prefix", cwd=tmp_path)
    proj = tmp_path / "mp"
    _ok("object", "o", "--state", "k:double:0.0", cwd=proj)
    for name, flags in SHAPES.items():
        _ok("method", "o", name, *flags, "--nogil", cwd=proj)
    # The two record shapes return the author's structs, which the sacred
    # header declares (one `double a` column each, as SHAPES declares).
    hdr = proj / "native/inc/mp/o/o_core.h"
    text = hdr.read_text(encoding="utf-8")
    first = text.index("#include")
    eol = text.index("\n", first) + 1
    hdr.write_text(
        text[:eol]
        + "typedef struct { double a; } rec_t;\n"
        + "typedef struct { double a; } rec2_t;\n"
        + text[eol:],
        encoding="utf-8",
    )
    build = subprocess.run(
        ["make", f"PYTHON={sys.executable}"],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert build.returncode == 0, build.stderr[-3000:]
    # A green make that built nothing proves nothing: the extension exists.
    built = list((proj / "src" / "mp").glob("o*.so")) + list(
        (proj / "src" / "mp").glob("o*.pyd")
    )
    assert built, sorted(p.name for p in (proj / "src" / "mp").iterdir())


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

#: The kernel counts itself in and out around a GATE; the core's destroy
#: aborts if any is running. So the process dies (SIGABRT) exactly when a
#: teardown frees under a running call. A COUNT, atomically: with two calls
#: in flight a flag cleared by the first to finish hid a free under the
#: second.
#:
#: Nothing here is ordered by the clock (gh-2290: a call started 50 ms
#: before the closers reached the kernel after them on a loaded macOS
#: runner). The kernel appends a byte to the file ``$JM_PROBE_ENTERED`` once
#: it is counted in, and to ``$JM_PROBE_LEFT`` once counted out; the n-th
#: call to enter is held until the probe creates ``$JM_PROBE_GO<n>``. The
#: probe reads those files and opens the gates, so every step it asserts on
#: has been observed, not waited for. The 60 s cap only keeps a broken
#: probe from hanging; no passing run comes near it.
_RUNNING = r"""static int jm_test_running;
static int jm_test_entered;
static void jm_test_mark(const char *var)
{
    const char *path = getenv(var);
    FILE *f = path ? fopen(path, "ab") : NULL;
    if (!f) {
        fprintf(stderr, "no probe file in %s\n", var);
        abort();
    }
    fputc('.', f);
    fclose(f);
}
static void jm_test_gate(int n)
{
    const char *go = getenv("JM_PROBE_GO");
    struct timespec ms = {0, 1000000L};
    char path[4096];
    int i;
    if (!go) {
        fprintf(stderr, "no JM_PROBE_GO\n");
        abort();
    }
    snprintf(path, sizeof path, "%s%d", go, n);
    for (i = 0; i < 60000; i++) {
        FILE *f = fopen(path, "rb");
        if (f) {
            fclose(f);
            return;
        }
        nanosleep(&ms, NULL);
    }
}
"""
_SLEEP = """
    int jm_n = __atomic_add_fetch(&jm_test_entered, 1, __ATOMIC_SEQ_CST);
    __atomic_fetch_add(&jm_test_running, 1, __ATOMIC_SEQ_CST);
    jm_test_mark("JM_PROBE_ENTERED");
    jm_test_gate(jm_n);
    __atomic_fetch_sub(&jm_test_running, 1, __ATOMIC_SEQ_CST);
    jm_test_mark("JM_PROBE_LEFT");
"""
_TRIP = """    if (__atomic_load_n(&jm_test_running, __ATOMIC_SEQ_CST)) {
        fprintf(stderr, "FREED UNDER A RUNNING KERNEL\\n");
        abort();
    }
"""
_PRELUDE = (
    "#define _POSIX_C_SOURCE 199309L\n"
    "#include <stdio.h>\n#include <stdlib.h>\n#include <time.h>\n"
)

#: What both probes share: the files the kernel writes, the gates it waits
#: on, and ``until``, which polls for an observed state and exits non-zero
#: naming the one it never saw.
_PROBE_HEAD = """
import os, sys, threading, time
sys.path.insert(0, "src")
sys.path.insert(0, ".")
for k in ("ENTERED", "LEFT", "GO"):
    os.environ["JM_PROBE_" + k] = os.path.abspath("probe-" + k.lower())
for f in os.listdir("."):
    if f.startswith("probe-"):
        os.remove(f)
def count(k):
    try:
        return os.path.getsize(os.environ["JM_PROBE_" + k])
    except FileNotFoundError:
        return 0
def go(n):
    open(os.environ["JM_PROBE_GO"] + str(n), "w").close()
def until(pred, what, limit=30.0):
    end = time.monotonic() + limit
    while not pred():
        if time.monotonic() > end:
            sys.exit("never observed: " + what)
        time.sleep(0.001)
{imp}
w = {make}
res = {{}}
"""

#: Thread A enters the kernel and is held there; B closes once A is
#: observed inside; the main thread polls a GIL-holding member ({ping})
#: until B has observably refused new calls, then starts call C, while B
#: still waits on A. Only then is A released. Prints A's result, C's, and
#: whether B was still waiting when C was refused.
PROBE = (
    _PROBE_HEAD
    + """
def call(tag):
    try:
        res[tag] = w.work()
    except Exception as e:
        res[tag] = type(e).__name__
w.{ping}()  # answers before the teardown, so a refusal below is B's
a = threading.Thread(target=call, args=("a",)); a.start()
until(lambda: count("ENTERED") == 1, "A in the kernel")
b = threading.Thread(target=w.{close}); b.start()
def refused():
    try:
        w.{ping}()
    except RuntimeError:
        return True
    return False
until(refused, "B refusing new calls")
call("c")
waiting = b.is_alive() and count("ENTERED") == 1 and count("LEFT") == 0
go(1)
a.join(); b.join()
print(res["a"], res["c"], "waited" if waiting else "freed")
"""
)


#: Two calls held in the kernel (the count at 2, so only the LAST leave may
#: wake the teardown) and two closers started once both are observed
#: inside. The first closer waits for both; the second finds the object
#: already refused and returns while both are still held -- "close()
#: returned" means "freed" only for the first. The calls are then let out
#: one at a time, and the waiting closer must outlive the first leave. The
#: core aborts if anything frees under either call.
PROBE2 = (
    _PROBE_HEAD
    + """
def call(tag):
    res[tag] = w.work()
ts = [threading.Thread(target=call, args=("a1",)),
      threading.Thread(target=call, args=("a2",))]
for t in ts: t.start()
until(lambda: count("ENTERED") == 2, "both calls in the kernel")
cs = [threading.Thread(target=w.{close}) for _ in range(2)]
for t in cs: t.start()
def returned():
    return sum(not t.is_alive() for t in cs)
fast = True
try:
    until(lambda: returned() >= 1, "a closer returning", limit=10.0)
except SystemExit:
    fast = False
fast = fast and returned() == 1 and count("LEFT") == 0
go(1)
until(lambda: count("LEFT") == 1, "the first call leaving")
last = returned() == (1 if fast else 0)
go(2)
for t in ts + cs: t.join()
print(res["a1"], res["a2"], "first-returns" if fast else "no-fast",
      "last-waits" if last else "no-wait")
"""
)


def _run_probe(
    proj: Path,
    imp: str,
    make: str,
    close: str,
    ping: str,
    probe: str = PROBE,
) -> str:
    script = proj / "probe.py"
    script.write_text(
        probe.format(imp=imp, make=make, close=close, ping=ping),
        encoding="utf-8",
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
    got = _run_probe(proj, imp, "W(k=0)", close, "get_k")
    assert got == "7 RuntimeError waited", got
    if variant == "standalone":
        # The count at 2 and two closers: only the last leave wakes the
        # waiter, and the second closer does not wait (review of #2275).
        got = _run_probe(proj, imp, "W(k=0)", close, "get_k", PROBE2)
        assert got == "7 7 first-returns last-waits", got


_SLOW_H = """\
#ifndef SLOW_H
#define SLOW_H
typedef struct slow slow_t;
slow_t *slow_open(int k);
void slow_close(slow_t *s);
int slow_work(slow_t *s);
int slow_peek(slow_t *s);
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
int slow_peek(slow_t *s) { return s->k; }
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
                },
                # Holds the GIL: what the probe polls for the refusal.
                {"name": "peek", "fn": "slow_peek", "returns": "int"},
            ],
        },
        tmp_path,
    )
    C.save(tmp_path, cfg)
    apply_run(tmp_path)
    # Compiled to `tmp_path/slow<EXT_SUFFIX>`, which the probe imports.
    _compile_import(tmp_path, "slow", _SLOW_H, _SLOW_C)
    got = _run_probe(tmp_path, "import slow", "slow.Slow(0)", "close", "peek")
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


def test_a_body_holding_the_gil_is_named_once(tmp_path):
    """A body that holds the GIL has nothing to count: `apply` says it
    holds the GIL (gh-2259) and not, as well, that it does not count."""
    _ok("new", "p", "--no-c-prefix", cwd=tmp_path)
    proj = tmp_path / "p"
    _ok("module", "dsp", cwd=proj)
    _ok("object", "mx", "--module", "dsp", cwd=proj)
    _ok("method", "mx", "w", "--return-type", "int", cwd=proj)
    frag = proj / "objects" / "mx.toml"
    text = frag.read_text(encoding="utf-8")
    assert text.count('name = "w"\n') == 1, text
    frag.write_text(
        text.replace('name = "w"\n', 'name = "w"\nnogil = true\n', 1),
        encoding="utf-8",
    )
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stderr
    said = r.stdout + r.stderr
    assert "holding the GIL" in said, said
    assert "does not count" not in said, said
