"""gh-2346: an object that lends its pointer as a capsule parks it at close.

The composer and handle fixes (review 5634567388) have a third member: an
OBJECT that publishes `_capsule` (gh-788 gap 4) -- doppler's `Telemetry`,
lent with `retain = true` to every object's `set_telemetry`, and its
`FrameDesc`. A consumer that keeps the capsule keeps the object alive, not its
state, and the generated `destroy()` freed the state anyway: attach, destroy,
read was the same heap-use-after-free.

Objects already had the deferral, for views (gh-2187): a lending object's
`destroy()` parks its state in `_jm_parked` and `tp_dealloc` frees it once the
last holder lets go. `_borrow.lends` is the one place that decides which
objects lend, and it now answers `"capsule"` for one that publishes a capsule,
so the object kind reuses that park -- no third copy of the deferral.

The gates:

1. the predicate and the rendered teardown (park, dealloc, prose);
2. an object that lends nothing, or lends views, renders as before;
3. the handle's close() docstring states the trade (review's Should);
4. built under AddressSanitizer: attach, producer destroy(), consumer read is
   clean; with the capsule clause removed from the predicate it is a
   heap-use-after-free.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import asan_env, default_cc
from _jmrun import run_cli

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _borrow  # noqa: E402
from just_makeit._context import _destroy  # noqa: E402

_CAP = {"name": "_capsule", "type": "capsule", "capsule": "p.tt"}

#: Every spelling of a producer property. The getter lends `self->handle` for
#: ANY property carrying a `capsule` key, so the predicate must answer for
#: each -- keyed on `type == "capsule"` it missed the other two, and their
#: destroy() freed under a kept capsule (review on c18e8770).
_SPELLINGS = {
    "type-capsule": 'type = "capsule", ',
    "typeless": "",
    "typed-pointer": 'type = "tt_state_t *", ',
}
_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None


# -- 1. the predicate and the teardown -----------------------------------------


class TestThePredicate:
    def test_a_capsule_lends(self):
        assert _borrow.lends_from([], [_CAP], []) == "capsule"

    @pytest.mark.parametrize(
        "prop",
        [
            {"name": "_capsule", "capsule": "p.tt"},
            {"name": "_capsule", "type": "tt_state_t *", "capsule": "p.tt"},
        ],
        ids=["typeless", "typed-pointer"],
    )
    def test_every_spelling_the_getter_lends_for_lends(self, prop):
        """The getter keys on the `capsule` key, not the type; so must
        the predicate, or the object frees under the borrower."""
        assert _borrow.lends_from([], [prop], []) == "capsule"

    def test_views_still_answer_true(self):
        """An object lending views AND a capsule keeps its views answer, so
        its prose and teardown are the ones it always had."""
        view = {"name": "b", "buf_field": True}
        assert _borrow.lends_from([], [view], []) is True
        assert _borrow.lends_from([], [view, _CAP], []) is True

    def test_nothing_lent_is_false(self):
        assert (
            _borrow.lends_from([], [{"name": "g", "type": "double"}], [])
            is False
        )


def _ctx(lends):
    return _destroy.make_destroy_ctx(
        "tt", "TtObj", None, [], csym="tt", lends=lends
    )


class TestTheTeardown:
    def test_a_capsule_lender_parks_its_state(self):
        ctx = _ctx("capsule")
        assert "self->_jm_parked = self->handle;" in ctx["destroy_method_body"]
        assert "tt_destroy(self->handle);" not in ctx["destroy_method_body"]

    def test_dealloc_frees_what_destroy_parked(self):
        assert (
            "tt_destroy(self->_jm_parked);"
            in _ctx("capsule")["destroy_dealloc_call"]
        )

    def test_its_prose_names_the_capsule(self):
        doc = _ctx("capsule")["pyi_destroy_methods"]
        assert "``_capsule`` was handed to" in doc
        assert "gh-2346" in doc

    def test_a_fallible_destroy_on_a_capsule_lender_is_refused(self):
        """A status has no caller in tp_dealloc (the gh-2187 rule)."""
        from just_makeit._report import Refusal

        with pytest.raises(Refusal, match="its pointer as a capsule"):
            _destroy.make_destroy_ctx(
                "tt",
                "TtObj",
                {"returns": "int"},
                [],
                csym="tt",
                lends="capsule",
            )


# -- 2. nothing else moves -------------------------------------------------------


class TestNothingElseMoves:
    def test_a_view_lender_renders_as_it_did(self):
        """`lends=True` is what a view-lending object always passed; its
        render must not depend on whether a capsule answer exists."""
        views = _ctx(True)
        assert "Views this object handed out" in views["pyi_destroy_methods"]
        assert "_capsule" not in views["pyi_destroy_methods"]

    def test_an_object_lending_nothing_renders_as_it_did(self):
        plain = _ctx(False)
        assert "_jm_parked" not in plain["destroy_method_body"]
        assert "_jm_parked" not in plain["destroy_fields"]


# -- 3. the handle's close() states the trade -----------------------------------


class TestTheHandleCloseDoc:
    @staticmethod
    def _cfg(capsule: bool):
        from test_handle_codegen import _writer_cfg

        cfg = _writer_cfg()
        if capsule:
            cfg["module"]["wfm_writer"]["capsule"] = "p.wfm.writer"
        return cfg

    def test_both_faces_say_close_waits_for_the_borrower(self):
        from just_makeit import _handle

        cfg = self._cfg(True)
        doc = _handle.close_doc(cfg, "wfm_writer")
        assert "is not released here" in doc
        assert "is not raised" in doc
        assert f'"{doc}"' in _handle.render_ext(cfg, "wfm_writer")
        # The stub reflows a long docstring; the words are the same text.
        pyi = _handle.render_pyi(cfg, "wfm_writer")
        body = pyi[pyi.index("def close(self) -> None:") :]
        body = body[body.index('"""') + 3 :]
        body = body[: body.index('"""')]
        assert " ".join(body.split()) == doc

    def test_a_handle_lending_nothing_keeps_its_doc(self):
        from just_makeit import _handle

        assert _handle.close_doc(self._cfg(False), "wfm_writer") == (
            _handle.CLOSE_DOC
        )


# -- 4. built, under AddressSanitizer -------------------------------------------

#: The consumer: keep the producer's state, and READ it.
_POOL_C = {
    "pool_attach": "    state->held = t;\n    return 0;\n",
    "pool_read": "    return state->held ? state->held->k : -1;\n",
}

_PROBE = """\
import sys
sys.path.insert(0, "src")
from p import Pool, Tt
pool = Pool(k=0)
t = Tt(k=7)
pool.attach(t)
t.destroy()
print("read after destroy", pool.read())
pool.attach(None)
del t
print("released")
"""


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


def _implement(path: Path, fn: str, body: str) -> None:
    import re

    text = path.read_text(encoding="utf-8")
    head = re.search(rf"\n{fn} ?\([^)]*\)\n{{\n", text)
    assert head, f"{fn} not found in {path}"
    end = text.index("\n}\n", head.end())
    path.write_text(
        text[: head.end()] + body.rstrip("\n") + text[end:], encoding="utf-8"
    )


def _cmake(proj: Path, *args: str) -> None:
    r = subprocess.run(
        ["cmake", *args],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert r.returncode == 0, (r.stdout + r.stderr)[-4000:]


def _project(tmp_path: Path, asan: bool, spelling: str) -> Path:
    """A producer `Tt` publishing `_capsule`, and a consumer `Pool` whose
    `attach` keeps it (`retain`) and whose `read` reads the kept state."""
    _ok("new", "p", "--no-c-prefix", cwd=tmp_path)
    proj = tmp_path / "p"
    _ok("object", "tt", "--state", "k:int:0", cwd=proj)
    tt = proj / "objects" / "tt.toml"
    tt.write_text(
        tt.read_text(encoding="utf-8")
        + '\n[[tt.properties]]\nname = "_capsule"\n'
        + _SPELLINGS[spelling].replace(", ", "\n")
        + 'capsule = "p.tt"\n',
        encoding="utf-8",
    )
    _ok("object", "pool", "--state", "k:int:0", cwd=proj)
    frag = proj / "objects" / "pool.toml"
    frag.write_text(
        frag.read_text(encoding="utf-8")
        + '\n[[pool.methods]]\nname = "attach"\nreturn_type = "int"\n'
        "status_return = true\n"
        'params = [{ name = "t", type = "tt_state_t *", capsule = "p.tt", '
        'header = "p/tt/tt_core.h", retain = true }]\n'
        '\n[[pool.methods]]\nname = "read"\nreturn_type = "int"\n',
        encoding="utf-8",
    )
    _ok("apply", cwd=proj)
    hdr = proj / "native/inc/p/pool/pool_core.h"
    text = hdr.read_text(encoding="utf-8")
    assert text.count("    int k;\n") == 1, text
    hdr.write_text(
        text.replace("    int k;\n", "    int k;\n    tt_state_t *held;\n"),
        encoding="utf-8",
    )
    core = proj / "native/src/pool/pool_core.c"
    for fn, body in _POOL_C.items():
        _implement(core, fn, body)
    flags = (
        [
            "-DCMAKE_C_FLAGS=-fsanitize=address -fno-omit-frame-pointer",
            "-DCMAKE_SHARED_LINKER_FLAGS=-fsanitize=address",
        ]
        if asan
        else []
    )
    _cmake(
        proj,
        "-B",
        "build",
        "-S",
        ".",
        f"-DPython3_EXECUTABLE={sys.executable}",
        *flags,
    )
    _cmake(proj, "--build", "build", "--parallel", "4")
    return proj


def _probe(proj: Path, env: "dict | None" = None):
    script = proj / "probe.py"
    script.write_text(_PROBE, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=proj,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_a_kept_object_is_readable_after_destroy(tmp_path):
    out = _probe(_project(tmp_path, asan=False, spelling="type-capsule"))
    assert out.returncode == 0, (out.returncode, out.stderr[-2000:])
    assert out.stdout.split("\n")[:-1] == [
        "read after destroy 7",
        "released",
    ], out.stdout


@pytest.mark.skipif(
    _NO_TOOLCHAIN or not asan_env(default_cc()),
    reason="no cmake / C compiler / AddressSanitizer runtime",
)
@pytest.mark.parametrize("spelling", list(_SPELLINGS))
def test_a_kept_object_outlives_its_destroy_under_asan(tmp_path, spelling):
    """Freed in destroy(), as before the fix, ASan reports
    `heap-use-after-free` in pool_read; parked, the read is of live
    memory and the free waits for the consumer to let go."""
    proj = _project(tmp_path, asan=True, spelling=spelling)
    env = {**os.environ, **asan_env(default_cc())}
    out = _probe(proj, env)
    assert "AddressSanitizer" not in out.stderr, out.stderr[-3000:]
    assert out.returncode == 0, (out.returncode, out.stderr[-2000:])
    assert out.stdout.split("\n")[:-1] == [
        "read after destroy 7",
        "released",
    ], out.stdout
