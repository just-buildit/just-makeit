"""gh-2354: an init_param ``object = "<composer>"`` can name a composer.

gh-2346 let a composer module publish a ``_capsule`` and a consumer METHOD
param take it (``capsule = "..."``, ``retain = true``). The other consuming
path, an ``init_param`` with ``object = "<composer>"`` -- the sugar over the
capsule path -- could not name one: ``resolve_object_ref`` knew components,
views and handles, and refused a composer module as 'not a valid target'. An
object could not take a Composer at construction.

``resolve_composer_object_ref`` is the handle resolver's sibling
(``resolve_handle_object_ref``, gh-1227): every slot READ from the module's
own declarations. One table, ``_MODULE_REF_KINDS``, is read by both
``resolve_object_ref`` and ``object_ref_import``, so a kind cannot resolve and
not import. The constructor needs no ``retain``: a borrowed constructor
pointer already holds a strong reference to the Python owner (gh-790).

The gate: the resolver and its refusals; the ``.pyi`` import, which names the
FILE that holds the class (a composer's directory has no ``__init__.py``
re-exporting it); and a compiled consumer built over the bundled
``composer_seams`` project, driven with the right object, a wrong one, and a
closed composer.
"""

from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli
from test_gh2346_composer_capsule import (
    CAP,
    _cfg,
    _cmake,
    _implement,
    _run,
    _same_second_as_the_build,
)

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _config as C  # noqa: E402

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None


class TestTheResolver:
    def test_a_composer_resolves_to_its_state_and_capsule(self):
        cfg = _cfg()
        ctype, capsule, header, cls = C.resolve_object_ref(cfg, "wc")
        assert ctype == "wfm_compose_state_t *", ctype
        assert capsule == CAP, capsule
        assert header.endswith("wfm_compose_core.h"), header
        assert cls == "Composer", cls

    def test_a_c_prefix_moves_the_state_type_with_the_producer(self):
        """A `backing` that names a jm component refers to its API, whose
        symbols jm derives (gh-1685); one that does not is the author's
        hand-written core, used as written. The consumer's prototype must say
        the producer's spelling either way, or it names a type that does not
        exist."""
        cfg = _cfg()
        cfg["project"]["c_prefix"] = "zz"
        assert C.resolve_object_ref(cfg, "wc")[0] == "wfm_compose_state_t *"
        cfg["wfm_compose"] = {}  # `backing` now names a jm component
        assert C.resolve_object_ref(cfg, "wc")[0] == "zz_wfm_compose_state_t *"

    def test_the_class_suffix_is_checked(self):
        cfg = _cfg()
        assert C.resolve_object_ref(cfg, "wc.Composer")[3] == "Composer"
        with pytest.raises(ValueError) as exc:
            C.resolve_object_ref(cfg, "wc.Nope")
        assert "generates the class 'Composer'" in str(exc.value)

    def test_a_composer_that_publishes_nothing_is_refused_with_the_fix(self):
        with pytest.raises(ValueError) as exc:
            C.resolve_object_ref(_cfg(key=""), "wc")
        msg = str(exc.value)
        assert "publishes no capsule" in msg and 'capsule = "<name>"' in msg

    def test_a_composer_with_no_backing_is_refused(self):
        cfg = _cfg()
        del cfg["module"]["wc"]["backing"]
        with pytest.raises(ValueError) as exc:
            C.resolve_object_ref(cfg, "wc")
        assert "no `backing`" in str(exc.value)

    def test_the_other_kinds_still_say_what_they_may_name(self):
        cfg = _cfg()
        cfg["module"]["cap"] = {"kind": "capsule", "backing": "ddcr"}
        with pytest.raises(ValueError) as exc:
            C.resolve_object_ref(cfg, "cap")
        msg = str(exc.value)
        assert 'kind = "handle" module' in msg
        assert 'kind = "composer" module' in msg

    def test_resolver_and_importer_read_one_table(self):
        """A kind cannot be resolvable and not importable."""
        assert set(C._MODULE_REF_KINDS) == {"handle", "composer"}
        for kind in C._MODULE_REF_KINDS:
            assert C._MODULE_REF_KINDS[kind].__name__.startswith("resolve_")


class TestTheStubImport:
    def test_the_import_names_the_file_that_holds_the_class(self):
        """`Mix` is in `<package>/<leaf>.pyi`; its package has no
        `__init__.py`. `from .wc import Composer` named a namespace package
        that does not hold it."""
        assert (
            C.object_ref_import(_cfg(), "wc") == "from .wc.wc import Composer"
        )

    def test_a_declared_package_moves_the_import(self):
        cfg = _cfg()
        cfg["module"]["wc"]["package"] = "scenes"
        assert (
            C.object_ref_import(cfg, "wc") == "from .scenes.wc import Composer"
        )


_CONSUMER_C = {
    "studio_holder_create": (
        "    studio_holder_state_t *obj = calloc(1, sizeof(*obj));\n"
        "    if (!obj)\n        return NULL;\n"
        "    obj->held = m;\n"
        "    return obj;\n"
    ),
    "studio_holder_peek": "    return state->held ? 1 : 0;\n",
    "studio_holder_count": (
        "    if (!state->held)\n        return -1;\n"
        "    return (int)((const volatile unsigned char *)state->held)[0];\n"
    ),
}

_PROBE = """\
import gc, sys
sys.path.insert(0, "src")
from studio.playlist.playlist import Mix, Track
from studio import Holder
rc = sys.getrefcount
m = Mix([Track(dur=4), Track(dur=2)])
base = rc(m)
h = Holder(m)
print("kept the object", rc(m) - base, h.peek())
before = h.count()
cap = m._capsule
h2 = Holder(cap)
print("took the capsule", h2.peek(), h2.count() == before)
for what in (3, object()):
    try:
        Holder(what)
    except TypeError as e:
        print("refused", type(what).__name__,
              "m must be the studio.playlist.mix capsule" in str(e))
try:
    Holder(None)
except TypeError as e:
    print("refused None", "required" in str(e))
m.close()
print("read after close", h.count() == before)
del h, h2, cap
gc.collect()
print("after release", rc(m) - base)
"""

_EXPECT = [
    "kept the object 1 1",
    "took the capsule 1 True",
    "refused int True",
    "refused object True",
    "refused None True",
    "read after close True",
    "after release 0",
]


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_an_object_takes_a_composer_at_construction(tmp_path):
    """The bundled composer project, given a capsule, and an object
    `Holder(m: Mix)` that keeps the composer's state across calls."""
    sys.path.insert(
        0, str(SRC / "just_makeit" / "examples" / "composer_seams")
    )
    seams = importlib.import_module("test")
    try:
        seams.run(tmp_path)
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

    def ok(*a: str) -> None:
        r = run_cli(*a, cwd=proj)
        assert r.returncode == 0, (a, r.stdout, r.stderr)

    # The key only reaches the composer's binding through `apply`.
    ok("apply")
    ext = (proj / "native/src/playlist/playlist_ext.c").read_text(
        encoding="utf-8"
    )
    assert "_get__capsule" in ext, "apply did not publish the capsule"
    # The declaration under test: the composer, NAMED, not spelled out.
    ok("object", "holder", "--init-param", "m:object:playlist")
    ok("method", "holder", "peek", "--return-type", "int")
    ok("method", "holder", "count", "--return-type", "int")
    hdr = next(proj.glob("native/inc/**/holder/holder_core.h"))
    htext = hdr.read_text(encoding="utf-8")
    assert "playlist_state_t *m" in htext, htext
    # The sacred struct keeps the borrowed pointer.
    assert htext.count("    float gain;\n") == 1, htext
    hdr.write_text(
        htext.replace(
            "    float gain;\n",
            "    float gain;\n    playlist_state_t *held;\n",
        ),
        encoding="utf-8",
    )
    core = proj / "native/src/holder/holder_core.c"
    for fn, body in _CONSUMER_C.items():
        _implement(core, fn, body)
    pyi = (proj / "src/studio/holder.pyi").read_text(encoding="utf-8")
    assert "from .playlist.playlist import Mix" in pyi, pyi
    assert "def __init__(self, m: Mix)" in pyi, pyi
    _same_second_as_the_build(proj)
    _cmake(
        proj,
        "-B",
        "build",
        "-S",
        ".",
        f"-DPython3_EXECUTABLE={sys.executable}",
        timeout=600,
    )
    _cmake(proj, "--build", "build", "--parallel", "4", "--clean-first")
    out = _run(proj, _PROBE)
    assert out.returncode == 0, (out.returncode, out.stderr[-2500:])
    assert out.stdout.split("\n")[:-1] == _EXPECT, out.stdout
