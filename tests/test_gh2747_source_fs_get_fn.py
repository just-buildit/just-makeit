"""A composer source's ``fs`` reads through ``get_fn``, a segment
``get_fn`` sees the segment's sources, and the source ``fs`` honors
``default_raw`` (gh-2747).

doppler's ``ofdm`` source reads an ``fs`` at a face's default as unset
and becomes the channel bandwidth. Rendering was right everywhere, but
two read-backs stated the default:

- ``Synth(type="ofdm").fs``: the source type's ``fs`` took no ``get_fn``
  (gh-2710 covered every source FIELD, and ``fs`` is not one).
- ``Segment(type="ofdm").fs``: a segment field's ``get_fn`` snapshot was
  built from the segment's scalars only, its sources ``NULL, 0``, so the
  resolver could not see the ``ofdm`` source.

Decisions:

1. ``[module.X.source] fs_get_fn = "<fn>"``, declared with gh-2710's
   scalar prototype ``double fn(const <source struct> *, double fs)`` in
   the bridge header. It is the ``get_fn`` of ``source_fs``'s row -- the
   one declaration of the source's ``fs`` -- so the getter reads it
   through the same ``_read_acc``. Only ``Synth.fs``'s read changes; the
   setter, the constructor, the stored member (what ``bridge_fn`` and the
   inline Segment's forward read) keep the stored value. A source built
   by the Composer's read-back is a ``Synth`` too, so its ``.fs`` reads
   resolved through the same getter.
2. The snapshot fills ``sources_member`` / ``count_member`` through
   ``_<Seg>_sources_c``, the conversion the Composer now reads its
   segments' sources through too -- one spelling. A list item that is not
   the source type is a ``TypeError`` naming its index in both: the
   Composer cast it blind before this, which was undefined behavior.
3. ``source_fs`` reads ``default_raw`` through ``_types.parse_seed``, the
   one reading of an init param's ``default`` beside ``default_raw``.

GATE: compiled, with resolvers that make an ``ofdm`` source's default
``fs`` its bandwidth: ``Synth.fs``, ``Segment.fs`` (single, multi, and an
``ofdm`` source among others) and the Composer's read-back read resolved,
every other source reads the stored value, and the setter and
``to_json`` keep the stored one. ``default_raw`` renders and builds.
"""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest

import _cbuild as cbuild
from _compilers import WINDOWS_CFLAGS_GAP
from _jmrun import run_cli

from just_makeit import _composer
from just_makeit import _config as C
from just_makeit import _csym
from just_makeit import _keys
from just_makeit._report import Refusal

sys.path.insert(0, str(Path(__file__).parent))
import test_gh2678_source_fs_one_declaration as g2678  # noqa: E402
import test_gh2710_composer_field_get_fn as base  # noqa: E402
import test_gh2717_segment_flat_get_fn as flat  # noqa: E402

PKG = base.PKG


def _module() -> dict:
    """gh-2710's composer with an ``ofdm`` flag on its source, the source
    ``fs`` and the segment ``fs`` each read through a resolver, and the
    segment ``fs`` defaulting to 1.0 (the source type's is 1e6), as
    doppler's Segment and Synth do."""
    m = copy.deepcopy(base._module())
    m["source"]["fields"].append(
        {"name": "ofdm", "type": "int", "default": "0"}
    )
    m["source"]["fs_get_fn"] = "win_src_fs"
    for f in m["segment"]["fields"]:
        if f["name"] == "fs":
            f.update(default="1.0", get_fn="win_seg_fs")
    return m


def _cfg(m: "dict | None" = None) -> dict:
    return {"project": {"name": PKG}, "module": {"win": m or _module()}}


class TestTheKey:
    def test_recognised_and_written_back(self) -> None:
        cfg = _cfg()
        assert "fs_get_fn" in _keys.COMPOSER_SOURCE_KEYS
        assert _keys.unknown_keys(cfg) == []
        assert 'fs_get_fn = "win_src_fs"' in C._dump(cfg)

    def test_an_author_named_key(self) -> None:
        """jm derives no part of the name (gh-1694)."""
        assert "fs_get_fn" in _csym.AUTHOR_NAMED_KEYS

    def test_it_is_the_get_fn_of_the_one_fs_row(self) -> None:
        assert _composer.source_fs(_cfg(), "win")["get_fn"] == "win_src_fs"


class TestSynthFs:
    def test_declared_in_the_bridge(self) -> None:
        h = _composer.render_bridge_h(_cfg(), "win")
        assert "double win_src_fs(const wsrc_t *, double);" in h
        assert "win_src_fs" in _composer.seam_fns(_cfg(), "win")

    def test_only_the_read_calls_it(self) -> None:
        src = _composer.render_source_type(_cfg(), "win")
        get = src.split("Src_get_fs(", 1)[1].split("\n}\n", 1)[0]
        assert "return PyFloat_FromDouble(win_src_fs(&self->src, " in get
        assert src.count("win_src_fs(") == 1
        # The setter and the constructor keep the stored member.
        assert "self->fs = PyFloat_AsDouble(value);" in src
        assert "    self->fs = fs;\n" in src
        assert "win_src_fs" not in _composer.render_json_funcs(_cfg(), "win")

    def test_undeclared_reads_the_member(self) -> None:
        m = _module()
        del m["source"]["fs_get_fn"]
        src = _composer.render_source_type(_cfg(m), "win")
        assert "return PyFloat_FromDouble(self->fs);" in src
        assert "win_src_fs" not in _composer.render_bridge_h(_cfg(m), "win")


class TestTheSnapshotCarriesTheSources:
    def test_the_getter_fills_them_and_frees_them(self) -> None:
        seg = _composer.render_segment_type(_cfg(), "win")
        get = seg.split("Seg_get_fs(", 1)[1].split("\n}\n", 1)[0]
        order = [
            "_Seg_sources_c(self->sources, &_ns);",
            "_seg.sources = _srcs;",
            "_seg.n_sources = _ns;",
            "double _gv = win_seg_fs(&_seg, self->fs);",
            "free(_srcs);",
            "return PyFloat_FromDouble(_gv);",
        ]
        at = [get.index(s) for s in order]
        assert at == sorted(at), get
        # An exception from the conversion propagates, nothing leaks.
        assert "if (!_srcs)\n        return NULL;" in get

    def test_the_composer_reads_them_through_the_same_function(
        self,
    ) -> None:
        ext = _composer.render_ext(_cfg(), "win")
        assert ext.count("_Seg_sources_c(PyObject *sources") == 1
        comp = _composer.render_composer_type(_cfg(), "win")
        assert "_Seg_sources_c(seg->sources, &ns);" in comp
        # The conversion refuses a foreign element instead of casting it.
        seg = _composer.render_segment_type(_cfg(), "win")
        assert "if (!PyObject_TypeCheck(it, &SrcType))" in seg


class TestDefaultRaw:
    def _cfg(self, p: dict) -> dict:
        return g2678._cfg([{"name": "fs", "type": "double", **p}])

    def test_default_raw_is_the_default(self) -> None:
        cfg = self._cfg({"default_raw": "G_FS"})
        assert _composer.source_fs(cfg, "m")["default"] == "G_FS"
        assert "    double fs = G_FS;\n" in _composer.render_source_type(
            cfg, "m"
        )
        doc = _composer.type_doc_lines(cfg, "m")["Src"]
        assert any(ln.strip() == "fs : float, default G_FS" for ln in doc)

    def test_neither_is_still_refused(self) -> None:
        with pytest.raises(Refusal, match="`default_raw`"):
            _composer.source_fs(self._cfg({"required": True}), "m")


# -- the compiled proof ------------------------------------------------------

_HEADER = base._HEADER.replace(
    "  size_t window;\n} wsrc_t;",
    "  size_t window;\n  int ofdm;\n} wsrc_t;",
)
assert _HEADER != base._HEADER

#: The resolvers: an `ofdm` source at a face's default `fs` runs at its
#: bandwidth, as doppler's `dp_wfm_source_ofdm_defaults` decides; anything
#: else reads the value it was given.
_RESOLVE = (
    base._RESOLVE
    + """
double
win_src_fs (const wsrc_t *src, double fs)
{
  if (src->ofdm && fs == 1e6)
    return src->bw;
  return fs;
}

double
win_seg_fs (const wseg_t *seg, double fs)
{
  if (fs != 1.0)
    return fs;
  for (size_t k = 0; k < seg->n_sources; k++)
    if (seg->sources[k].ofdm)
      return seg->sources[k].bw;
  return fs;
}
"""
)

_DRIVER = """\
import json, sys
sys.path.insert(0, "src")
from winp.win.win import Src, Seg, Win

# Each line is one claim of gh-2747.
# 1. Synth.fs reads resolved; any other source reads the stored value.
s = Src(bw=20e6, ofdm=1)
assert s.fs == 20e6, ("Synth.fs resolves", s.fs)
assert Src(bw=20e6).fs == 1e6, "not ofdm: the stored default"
assert Src(bw=20e6, ofdm=1, fs=40e6).fs == 40e6, "a given fs is kept"
# The member is the stored value: the setter stores what it is given, and
# a later input change re-resolves from it rather than from a frozen 20e6.
s.fs = 8e6
assert s.fs == 8e6, s.fs
s.fs = 1e6
assert s.fs == 20e6, s.fs
s.ofdm = 0
assert s.fs == 1e6, ("the stored fs stayed raw", s.fs)

# 2. Segment.fs reads resolved via its sources -- single...
seg = Seg(bw=20e6, ofdm=1)
assert seg.fs == 20e6, ("single", seg.fs)
assert Seg(bw=20e6).fs == 1.0, "not ofdm: the stored default"
assert Seg(bw=20e6, ofdm=1, fs=40e6).fs == 40e6, "a given fs is kept"
# ...multi, and an ofdm source among others.
assert Seg.sum(Src(bw=5e6), Src(bw=6e6)).fs == 1.0
mix = Seg.sum(Src(bw=5e6), Src(bw=20e6, ofdm=1), Src(bw=7e6), count=4000)
assert mix.fs == 20e6, ("an ofdm source among others", mix.fs)
# The other get_fn field still reads from the same snapshot.
assert mix.span == 4000 / 1.0, mix.span

# 3. The record keeps the stored fs; the composer's read-back resolves.
w = Win([mix])
doc = json.loads(w.to_json())
assert doc["segments"][0]["fs"] == 1.0, doc["segments"][0]
back = w.segments[0]
assert back.fs == 20e6, ("Composer.segments[0].fs", back.fs)
assert Win.from_json(w.to_json()).segments[0].fs == 20e6

# 4. No sources: the resolver sees a count of 0 and reads the stored fs.
empty = Seg.sum(Src(bw=5e6, ofdm=1))
assert empty.fs == 5e6, empty.fs
empty.sources.clear()
assert empty.fs == 1.0, ("no sources", empty.fs)

# 4b. No __init__ ran, so `sources` is NULL: a count of 0, not a crash.
class S2(Seg):
    def __init__(self):
        pass
assert S2().fs == 0.0, ("a subclass skipping __init__", S2().fs)
assert Seg.__new__(Seg).fs == 0.0, "Seg.__new__ alone"

# 4c. A source read back through a Segment or the Composer is a Src, so its
# `.fs` reads through fs_get_fn too -- at the stored fs it was handed: the
# segment's (gh-2725, and the Composer's read-back). At the source type's
# default (1e6) an ofdm source resolves; at the segment's 1.0 it does not,
# since this resolver treats only 1e6 as the source's unset value.
assert Seg(bw=20e6, ofdm=1, fs=1e6).sources[0].fs == 20e6
assert Seg(bw=20e6, ofdm=1).sources[0].fs == 1.0
w1 = Win([Seg.sum(Src(bw=20e6, ofdm=1), Src(bw=5e6), fs=1e6)])
assert w1.segments[0].sources[0].fs == 20e6, w1.segments[0].sources[0].fs
assert w1.segments[0].sources[1].fs == 1e6, "not ofdm: stored"

# 5. A foreign element is refused by name, in the read and the Composer.
bad = Seg.sum(Src(bw=5e6))
bad.sources.append(5)
for face in (lambda: bad.fs, lambda: Win([bad])):
    try:
        face()
    except TypeError as e:
        assert "Seg.sources[1] is not a Src" in str(e), e
    else:
        raise AssertionError("a foreign source was read")
print("GH2747 OK")
"""


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    if base._NO_TOOLCHAIN:
        pytest.skip("no cmake / C compiler")
    # gh-2751: this file is in the Windows leg (it imports _cbuild for the
    # default_raw build), and gh-2717's cmake fixture is not clang-cl's.
    if cbuild.is_msvc(cbuild.compiler_for() or ""):
        pytest.skip(WINDOWS_CFLAGS_GAP)
    return flat.build(
        tmp_path_factory.mktemp("gh2747"),
        _module(),
        header=_HEADER,
        resolve=_RESOLVE,
    )


@pytest.mark.slow
@pytest.mark.skipif(base._NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_every_face_reads_the_resolved_fs(project: Path) -> None:
    driver = project / "drive_gh2747.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(driver)],
        cwd=project,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0 and "GH2747 OK" in r.stdout, r.stdout + r.stderr


# -- default_raw, built ------------------------------------------------------

_RAW_FS = "CLIP_FS"
_RAW_VALUE = 4e6


@pytest.mark.slow
def test_a_default_raw_fs_builds_and_is_the_default(tmp_path: Path) -> None:
    """gh-2678's playlist, its clip's ``fs`` given as ``default_raw`` naming
    a constant of the backing header: the source type is built with the
    constant's value, and ``apply`` does not refuse it."""
    cc = cbuild.compiler_for()
    if cc is None:
        pytest.skip("no C compiler")
    assert run_cli("new", "gp", "--no-c-prefix", cwd=tmp_path).returncode == 0
    proj = tmp_path / "gp"
    r = run_cli(
        "object",
        "clip",
        "--arg-type",
        "void",
        "--return-type",
        "float _Complex",
        "--init-param",
        f"fs:double:{g2678._OBJ_FS_DEFAULT}",
        cwd=proj,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    frag = proj / "objects" / "clip.toml"
    text = frag.read_text(encoding="utf-8")
    anchor = f'default = "{g2678._OBJ_FS_DEFAULT}"\n'
    assert text.count(anchor) == 1, text
    frag.write_text(
        text.replace(anchor, f'default_raw = "{_RAW_FS}"\n'),
        encoding="utf-8",
    )
    # The constant lives where the object's own binding sees it too.
    core_h = proj / g2678._layout("native/inc/<<P>>clip/clip_core.h", proj)
    h = core_h.read_text(encoding="utf-8")
    guard = h.index("#define", h.index("#ifndef"))
    eol = h.index("\n", guard) + 1
    core_h.write_text(
        h[:eol] + f"#define {_RAW_FS} {_RAW_VALUE!r}\n" + h[eol:],
        encoding="utf-8",
    )
    (proj / "modules").mkdir(exist_ok=True)
    (proj / "modules" / "playlist.toml").write_text(
        g2678._layout(g2678._COMPOSER, proj).replace(
            "<<SEG_DOC>>", g2678._SEG_FS_DOC
        ),
        encoding="utf-8",
    )
    for rel, body in g2678._BACKINGS.items():
        path = proj / g2678._layout(rel, proj)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(g2678._layout(body, proj), encoding="utf-8")
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    ext = proj / "native" / "src" / "playlist" / "playlist_ext.c"
    assert f"    double fs = {_RAW_FS};\n" in ext.read_text(encoding="utf-8")
    mod = g2678._import(proj)
    assert mod.Clip().fs == _RAW_VALUE, mod.Clip().fs
