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

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
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

    def test_declaring_it_adds_the_getter_and_its_row_and_nothing_else(self):
        """Byte for byte: remove the two pieces and the declared render IS
        the undeclared one, so the key cannot move anything beside them."""
        fn, row = _composer._capsule_getset_c(
            _cfg(), "wc", "Composer", "ComposerObject"
        )
        assert fn and row
        declared = _composer.render_ext(_cfg(), "wc")
        assert declared.count(fn) == 1 and declared.count(row) == 1
        stripped = declared.replace(fn, "", 1).replace(row, "", 1)
        assert stripped == _composer.render_ext(_cfg(key=""), "wc")

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
        " int destroyed; } ComposerObject;\n"
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

#: The consumer's two methods: keep the composer, and say whether it is kept.
#: The example project carries the `studio_` C prefix.
_POOL_C = {
    "studio_pool_attach": "    state->held = t;\n    return 0;\n",
    "studio_pool_peek": "    return state->held ? 1 : 0;\n",
}

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


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_a_consumer_keeps_the_composer(tmp_path):
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
        '\n[[pool.methods]]\nname = "peek"\nreturn_type = "int"\n',
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
    build = subprocess.run(
        ["cmake", "--build", "build", "--parallel", "4"],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if build.returncode != 0:
        # A new object needs a fresh configure to be picked up.
        cfgr = subprocess.run(
            [
                "cmake",
                "-B",
                "build",
                "-S",
                ".",
                f"-DPython3_EXECUTABLE={sys.executable}",
            ],
            cwd=proj,
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert cfgr.returncode == 0, cfgr.stderr[-3000:]
        build = subprocess.run(
            ["cmake", "--build", "build", "--parallel", "4"],
            cwd=proj,
            capture_output=True,
            text=True,
            timeout=900,
        )
    assert build.returncode == 0, (build.stdout + build.stderr)[-4000:]
    script = proj / "probe.py"
    script.write_text(_PROBE, encoding="utf-8")
    out = subprocess.run(
        [sys.executable, str(script)],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, (out.returncode, out.stderr[-2000:])
    assert out.stdout.split("\n")[:-1] == _EXPECT, out.stdout
