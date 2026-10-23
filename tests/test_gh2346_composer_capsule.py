"""gh-2346: a composer module publishes its state as a capsule.

The capsule triangle reached objects (gh-788 gap 4) and handles (gh-794), and
not composers. So no other module could be handed a `Composer`: doppler's
`Writer.annotate(scene)`, which keeps the scene and builds a capture's
annotations from its segments at close, could not be declared. Worse, a
composer ACCEPTED `capsule_name` -- a shared module key -- and did nothing with
it, the gh-1913 class.

The consuming side needed no change: a method param with `capsule` (gh-432)
and `retain` (gh-2327) already takes "the capsule, or anything exposing it as
`._capsule`". The feature is one getter, one getset row and one stub member,
read through the one reader a handle uses (`_config.published_capsule`) and
emitted through the one producer every kind shares
(`_context._parse.capsule_new_c`).

The gates, cheapest first:

1. the manifest: both spellings are read, the key is recognised;
2. the rendered getter: the destroyed guard before the lend, the row, the
   stub, and nothing at all for a composer that declares none;
3. compiled: the getter builds against the shared lend helper;
4. built end to end: the bundled `composer_seams` project, given the key and
   a consumer object whose method keeps a `Mix`, at runtime keeps the composer
   alive whether passed the object or its capsule, and refuses a closed one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc, find_compiler
from _jmrun import run_cli

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _coerce  # noqa: E402
from just_makeit import _composer  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit import _keys  # noqa: E402

CAP = "p.wc.mix"
_CC = default_cc()
_NO_TOOLCHAIN = shutil.which("cmake") is None or _CC is None


def _cfg(key: str = "capsule", name: str = CAP) -> dict:
    """A minimal composer module (test_gh1126's fixture), with the key."""
    cfg = {
        "project": {"name": "p"},
        "module": {
            "wc": {
                "kind": "composer",
                "backing": "wfm_compose",
                "header": "wfm/wfm.h",
                "composes": ["seg"],
                "segment": {
                    "struct": "wfm_seg_t",
                    "type_name": "Segment",
                    "fields": [{"name": "n", "type": "size_t"}],
                },
                "source": {
                    "object": "synth",
                    "struct": "wfm_src_t",
                    "type_name": "Synth",
                    "fields": [{"name": "fs", "type": "double"}],
                },
            }
        },
    }
    if key:
        cfg["module"]["wc"][key] = name
    return cfg


# -- 1. the manifest ----------------------------------------------------------


class TestTheManifest:
    @pytest.mark.parametrize("key", ["capsule", "capsule_name"])
    def test_both_spellings_are_read(self, key):
        """`capsule_name` was accepted on a composer and read by nothing."""
        assert C.published_capsule(_cfg(key=key), "wc") == CAP

    def test_an_undeclared_composer_publishes_nothing(self):
        assert C.published_capsule(_cfg(key=""), "wc") == ""

    @pytest.mark.parametrize("key", ["capsule", "capsule_name"])
    def test_the_key_is_recognised(self, key):
        assert [u.message() for u in _keys.unknown_keys(_cfg(key=key))] == []


# -- 2. what is rendered ------------------------------------------------------


class TestTheGetter:
    def test_it_lends_the_state_under_the_declared_name(self):
        ext = _composer.render_ext(_cfg(), "wc")
        assert "Composer_get__capsule(ComposerObject *self" in ext
        assert "jm_capsule_lend((void *)(self->state)," in ext
        assert f'"{CAP}", (PyObject *)self);' in ext

    def test_a_closed_composer_is_refused_before_the_lend(self):
        """A capsule carries no liveness: lending a closed composer's state
        hands the consumer a freed pointer it cannot detect."""
        ext = _composer.render_ext(_cfg(), "wc")
        fn = ext[ext.index("Composer_get__capsule(ComposerObject *self") :]
        guard = fn.index("if (self->destroyed || !self->state)")
        assert guard < fn.index("jm_capsule_lend(")

    def test_it_has_a_getset_row(self):
        ext = _composer.render_ext(_cfg(), "wc")
        table = ext[ext.index("static PyGetSetDef Composer_getset[]") :]
        table = table[: table.index("};")]
        assert '{"_capsule", (getter)Composer_get__capsule, NULL,' in table

    def test_the_stub_carries_it(self):
        pyi = _composer.render_pyi(_cfg(), "wc")
        assert "    def _capsule(self) -> Any:" in pyi
        assert f"Borrowed `{CAP}` capsule for this composer." in pyi


class TestZeroChurn:
    def test_an_undeclared_composer_renders_no_capsule(self):
        ext = _composer.render_ext(_cfg(key=""), "wc")
        assert "get__capsule" not in ext
        assert '{"_capsule"' not in ext

    def test_nor_does_its_stub(self):
        assert "_capsule" not in _composer.render_pyi(_cfg(key=""), "wc")

    def test_declaring_it_adds_its_pieces_and_nothing_else(self):
        """Byte for byte: take the declared pieces back out and the declared
        render IS the undeclared one, so the key cannot move anything else.

        The pieces: the getter and its row, and the gh-2346 deferral -- the
        `lent` field, close() leaving a lent state to dealloc, and dealloc
        freeing it."""
        fn, row = _composer._capsule_getset_c(
            _cfg(), "wc", "Composer", "ComposerObject"
        )
        assert fn and row
        declared = _composer.render_ext(_cfg(), "wc")
        back = [
            (fn, ""),
            (row, ""),
            (
                "    int                lent; /* gh-2346: the state was lent */\n",
                "",
            ),
            (
                "        /* gh-2346: lent -> a borrower may still read it; dealloc\n"
                "           frees it once the last one lets go. */\n"
                "        if (!self->lent)\n"
                "            wfm_compose_destroy(self->state);\n",
                "        wfm_compose_destroy(self->state);\n",
            ),
            (
                "    if (self->state && (!self->destroyed || self->lent))\n",
                "    if (!self->destroyed && self->state)\n",
            ),
        ]
        for piece, plain in back:
            assert declared.count(piece) == 1, piece
            declared = declared.replace(piece, plain, 1)
        assert declared == _composer.render_ext(_cfg(key=""), "wc")

    def test_nor_anything_in_the_build(self):
        assert _composer.render_cmake(_cfg(), "wc") == _composer.render_cmake(
            _cfg(key=""), "wc"
        )


# -- 3. compiled ----------------------------------------------------------------


@pytest.mark.skipif(_CC is None, reason="no C compiler available")
def test_the_getter_compiles_against_the_lend_helper(tmp_path):
    """Narrow by design, as test_gh1126's compile gate is: the getter, the
    shared helper it calls, and a struct with the fields it reads."""
    ext = _composer.render_ext(_cfg(), "wc")
    i = ext.index("static PyObject *\nComposer_get__capsule(")
    getter = ext[i : ext.index("\n}\n", i) + 3]
    src = tmp_path / "probe.c"
    src.write_text(
        "#define PY_SSIZE_T_CLEAN\n#include <Python.h>\n"
        "typedef struct wfm_compose_state wfm_compose_state_t;\n"
        "typedef struct { PyObject_HEAD wfm_compose_state_t *state;"
        " int destroyed; int lent; } ComposerObject;\n"
        f"{_coerce.CAPSULE_LEND_C}\n{getter}\n"
        "PyObject *probe(ComposerObject *s) "
        "{ return Composer_get__capsule(s, NULL); }\n"
    )
    r = subprocess.run(
        [
            _CC,
            "-c",
            "-std=c11",
            "-Werror=implicit-function-declaration",
            "-Werror=int-conversion",
            f"-I{__import__('sysconfig').get_paths()['include']}",
            str(src),
            "-o",
            str(tmp_path / "probe.o"),
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr


# -- 4. built end to end --------------------------------------------------------

#: The consumer's methods: keep the composer, say whether it is kept, and
#: READ the kept state -- the read a use after free is made of. The example
#: project carries the `studio_` C prefix.
_POOL_C = {
    "studio_pool_attach": "    state->held = t;\n    return 0;\n",
    "studio_pool_peek": "    return state->held ? 1 : 0;\n",
    "studio_pool_count": (
        "    if (!state->held)\n        return -1;\n"
        "    return (int)((const volatile unsigned char *)state->held)[0];\n"
    ),
}

#: The review's sequence (5634567388): the consumer keeps the composer, the
#: composer is closed, the consumer reads what it kept. doppler's
#: `Writer.annotate(scene)` then `w.close()` after `with Composer(...)` exits
#: is exactly this. A close() that freed the state here left the consumer
#: reading freed memory; ASan names it.
_PROBE_AFTER_CLOSE = """\
import sys
sys.path.insert(0, "src")
from studio.playlist.playlist import Mix, Track
from studio import Pool
pool = Pool(k=0)
m = Mix([Track(dur=4), Track(dur=2)])
pool.attach(m)
before = pool.count()
m.close()
print("read after close", pool.count() == before)
pool.attach(None)
del m
print("released")
"""

_PROBE = """\
import gc, sys
sys.path.insert(0, "src")
from studio.playlist.playlist import Mix, Track
from studio import Pool
rc = sys.getrefcount
pool = Pool(k=0)
m = Mix([Track(dur=4)])
base = rc(m)
pool.attach(m)
print("kept the object", rc(m) - base, pool.peek())
pool.attach(None)
print("detached", rc(m) - base, pool.peek())
cap = m._capsule
pool.attach(cap)
del cap
gc.collect()
print("kept through its capsule", rc(m) - base, pool.peek())
pool.attach(None)
try:
    pool.attach(Track(dur=1))  # no `_capsule`: a method param refuses it
except (TypeError, AttributeError):
    print("refused a non-composer")
print("still kept nothing", pool.peek())
try:
    m._capsule = None
except AttributeError:
    print("read-only")
m.close()
try:
    m._capsule
except RuntimeError:
    print("refused a closed composer")
"""

_EXPECT = [
    "kept the object 1 1",
    "detached 0 0",
    "kept through its capsule 1 1",
    "refused a non-composer",
    "still kept nothing 0",
    "read-only",
    "refused a closed composer",
]


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


def _implement(path: Path, fn: str, body: str) -> None:
    """Replace the scaffolded body of *fn* in *path* with *body*.

    The space before the parenthesis is optional: a GNU-formatted project
    writes `fn (`, a fresh scaffold like this one writes `fn(`."""
    import re

    text = path.read_text(encoding="utf-8")
    head = re.search(rf"\n{fn} ?\([^)]*\)\n{{\n", text)
    assert head, f"{fn} not found in {path}"
    end = text.index("\n}\n", head.end())
    path.write_text(
        text[: head.end()] + body.rstrip("\n") + text[end:], encoding="utf-8"
    )


def _asan_runtime() -> str:
    """The AddressSanitizer runtime to preload into Python, or "".

    The extension is instrumented, the interpreter is not, so the runtime
    has to be in the process before the extension loads.
    """
    gcc = find_compiler("gcc")
    if gcc is None:
        return ""
    lib = subprocess.run(
        [gcc, "-print-file-name=libasan.so"], capture_output=True, text=True
    ).stdout.strip()
    return lib if lib and Path(lib).is_file() else ""


def _cmake(proj: Path, *args: str, timeout: int = 900) -> None:
    r = subprocess.run(
        ["cmake", *args],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert r.returncode == 0, (r.stdout + r.stderr)[-4000:]


def _pool_project(tmp_path: Path, asan: bool = False) -> Path:
    """The bundled composer_seams project, built, given a capsule and a
    consumer object (`Pool`) whose `attach` keeps the `Mix` (`retain`).

    *asan* reconfigures it with AddressSanitizer before the second build.
    """
    sys.path.insert(
        0, str(SRC / "just_makeit" / "examples" / "composer_seams")
    )
    import importlib

    seams = importlib.import_module("test")
    try:
        seams.run(tmp_path)  # the bundled composer project, built
    finally:
        sys.path.pop(0)
        sys.modules.pop("test", None)
    proj = tmp_path / "studio"
    manifest = proj / "just-makeit.toml"
    text = manifest.read_text(encoding="utf-8")
    head = '[module.playlist]\nkind = "composer"\n'
    assert head in text
    manifest.write_text(
        text.replace(head, head + 'capsule = "studio.playlist.mix"\n', 1),
        encoding="utf-8",
    )
    _ok("object", "pool", "--state", "k:int:0", cwd=proj)
    # Wherever the layout put the object's table: a split fragment, or the
    # manifest itself (the example project is not split).
    frag = proj / "objects" / "pool.toml"
    if not frag.exists():
        frag = manifest
    frag.write_text(
        frag.read_text(encoding="utf-8")
        + '\n[[pool.methods]]\nname = "attach"\nreturn_type = "int"\n'
        "status_return = true\n"
        'params = [{ name = "t", type = "playlist_state_t *", '
        'capsule = "studio.playlist.mix", '
        'header = "studio/playlist/playlist_core.h", retain = true }]\n'
        '\n[[pool.methods]]\nname = "peek"\nreturn_type = "int"\n'
        '\n[[pool.methods]]\nname = "count"\nreturn_type = "int"\n',
        encoding="utf-8",
    )
    _ok("apply", cwd=proj)
    hdr = next(proj.glob("native/inc/**/pool/pool_core.h"))
    text = hdr.read_text(encoding="utf-8")
    assert text.count("    int k;\n") == 1, text
    hdr.write_text(
        text.replace(
            "    int k;\n", "    int k;\n    playlist_state_t *held;\n"
        ),
        encoding="utf-8",
    )
    core = proj / "native/src/pool/pool_core.c"
    for fn, body in _POOL_C.items():
        _implement(core, fn, body)
    # A new object needs a fresh configure to be picked up; ASan needs one
    # for its flags.
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
        timeout=600,
    )
    _cmake(proj, "--build", "build", "--parallel", "4")
    return proj


def _run(proj: Path, probe: str, env: "dict | None" = None):
    script = proj / "probe.py"
    script.write_text(probe, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=proj,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_a_consumer_keeps_the_composer(tmp_path):
    proj = _pool_project(tmp_path)
    out = _run(proj, _PROBE)
    assert out.returncode == 0, (out.returncode, out.stderr[-2000:])
    assert out.stdout.split("\n")[:-1] == _EXPECT, out.stdout


@pytest.mark.skipif(
    _NO_TOOLCHAIN or not _asan_runtime(),
    reason="no cmake / C compiler / AddressSanitizer runtime",
)
def test_a_kept_composer_outlives_its_close_under_asan(tmp_path):
    """The review's use after free (5634567388), under AddressSanitizer.

    close() on a composer whose state was lent marks it closed and leaves
    the free to dealloc; the consumer's retained reference keeps dealloc
    away, so its read after close() is of live memory. Freed in close(), as
    before the fix, ASan reports `heap-use-after-free` in studio_pool_count.
    """
    proj = _pool_project(tmp_path, asan=True)
    env = dict(os.environ)
    env["LD_PRELOAD"] = _asan_runtime()
    env["ASAN_OPTIONS"] = "detect_leaks=0"
    out = _run(proj, _PROBE_AFTER_CLOSE, env)
    assert "AddressSanitizer" not in out.stderr, out.stderr[-3000:]
    assert out.returncode == 0, (out.returncode, out.stderr[-2000:])
    assert out.stdout.split("\n")[:-1] == [
        "read after close True",
        "released",
    ], out.stdout


# -- 5. the same close pairing on a handle (gh-794) ---------------------------


class TestAHandleDefersItsCloseToo:
    """A gh-794 handle capsule plus a `retain` consumer is the same pairing:
    close() tore the handle down under a consumer still holding it. The fix
    is the same one, and the lend is recorded in the same shared body
    (`capsule_new_c`)."""

    @staticmethod
    def _ext(capsule: bool = True) -> str:
        from just_makeit import _handle
        from test_handle_codegen import _writer_cfg

        cfg = _writer_cfg()
        if capsule:
            cfg["module"]["wfm_writer"]["capsule"] = "p.wfm.writer"
        return _handle.render_ext(cfg, "wfm_writer")

    def test_the_lend_is_recorded(self):
        ext = self._ext()
        getter = ext[ext.index("Writer_get__capsule(WriterObject *self") :]
        assert getter.index("self->lent = 1;") < getter.index(
            "jm_capsule_lend("
        )

    def test_close_leaves_a_lent_handle_to_dealloc(self):
        ext = self._ext()
        close = ext[ext.index("Writer_close(WriterObject *self") :]
        close = close[: close.index("Py_RETURN_NONE;")]
        lent = close.index("if (self->lent) {")
        assert lent < close.index("wfm_writer_close(self->h);")
        assert "} else {" in close

    def test_dealloc_frees_what_close_left(self):
        ext = self._ext()
        assert "if (self->h && (!self->closed || self->lent)) {" in ext

    def test_a_second_init_of_a_lent_handle_is_refused(self):
        """The teardown before a rebuild would free the lent pointer, and
        there is one slot, so the old handle cannot wait for dealloc."""
        ext = self._ext()
        init = ext[ext.index("Writer_init(WriterObject *self") :]
        refuse = init.index("if (self->lent) {")
        assert refuse < init.index("wfm_writer_close(self->h);")
        assert "a second __init__ would free it under the borrower" in init

    def test_an_undeclared_handle_renders_none_of_it(self):
        ext = self._ext(capsule=False)
        assert "self->lent" not in ext
        assert "int       lent;" not in ext
