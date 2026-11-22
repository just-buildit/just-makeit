"""A composer field's Python read goes through the project's C (gh-2710).

A source or segment field's getter read its struct member, so a stored
value whose default is a sentinel read back as the sentinel. doppler's
wfmgen ``window`` is a ``size_t`` defaulting to ``WFM_WINDOW_UNSET``
(``SIZE_MAX``), meaning "the standard's count, ``2 * fs / bandwidth``": the
renderer resolves it, and ``Segment(..., fs=40e6).window`` read
``18446744073709551615``.

``get_fn = "<fn>"`` on a ``[[module.X.source.fields]]`` or
``[[module.X.segment.fields]]`` row names the project's resolver,
``<type> fn(const <struct> *, double fs)``, declared once in
``<cname>_bridge.h`` beside the other seams. A source field's is handed the
source's ``fs``, a segment field's the segment's own. Only the Python read
changes: the setter, the constructor keyword, ``to_json``, ``from_json`` and
the stored member keep the stored value.

GATE: a compiled composer whose source field and segment field each hold a
sentinel and name a resolver that reads it from ``fs``: the Python read
returns the resolved value and follows ``fs``, while ``to_json``, the
setter and ``from_json`` carry the stored sentinel unchanged.
"""

from __future__ import annotations

import copy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc

from _jmrun import run_cli

from just_makeit import _composer
from just_makeit import _config as C
from just_makeit import _keys

sys.path.insert(0, str(Path(__file__).parent))
from test_composer_codegen import _cfg  # noqa: E402
from test_gh1711_composer_owned_pointer import CJSON_C, CJSON_H  # noqa: E402

MOD = "wfm_compose"


def _with(src: "dict | None" = None, seg: "dict | None" = None) -> dict:
    """The shared composer cfg with `freq` (source) and `num_samples`
    (segment) edited."""
    cfg = copy.deepcopy(_cfg())
    m = cfg["module"][MOD]
    for f in m["source"]["fields"]:
        if f["name"] == "freq":
            f.update(src or {})
    for f in m["segment"]["fields"]:
        if f["name"] == "num_samples":
            f.update(seg or {})
    return cfg


class TestTheKeyIsPartOfTheManifest:
    def test_recognised_on_source_and_segment_fields(self) -> None:
        cfg = _with({"get_fn": "src_freq"}, {"get_fn": "seg_n"})
        assert "get_fn" in _keys.COMPOSER_FIELD_KEYS
        assert _keys.unknown_keys(cfg) == []

    def test_written_back_by_the_manifest_writer(self) -> None:
        cfg = _with({"get_fn": "src_freq"}, {"get_fn": "seg_n"})
        text = C._dump(cfg)
        assert 'get_fn = "src_freq"' in text
        assert 'get_fn = "seg_n"' in text

    def test_an_author_named_key(self) -> None:
        """jm derives no part of the name, so a `c_prefix` respell must not
        read it as a derived symbol (gh-1694)."""
        from just_makeit import _csym

        assert "get_fn" in _csym.AUTHOR_NAMED_KEYS


class TestTheSeam:
    def test_each_prototype_is_declared_in_the_bridge(self) -> None:
        cfg = _with({"get_fn": "src_freq"}, {"get_fn": "seg_n"})
        h = _composer.render_bridge_h(cfg, MOD)
        assert "double src_freq(const wfm_source_t *, double);" in h
        assert "size_t seg_n(const wfm_segment_t *, double);" in h
        assert {"src_freq", "seg_n"} <= set(_composer.seam_fns(cfg, MOD))

    def test_the_ext_includes_the_bridge(self) -> None:
        ext = _composer.render_ext(_with({"get_fn": "src_freq"}), MOD)
        assert '#include "wfm_compose/wfm_compose_bridge.h"' in ext

    def test_undeclared_renders_no_bridge(self) -> None:
        assert _composer.render_bridge_h(_cfg(), MOD) == ""

    def test_only_the_read_calls_it(self) -> None:
        """The setter, the constructor and the JSON face keep the member."""
        cfg = _with({"get_fn": "src_freq"}, {"get_fn": "seg_n"})
        cfg["module"][MOD]["json"] = {"enabled": True}
        src = _composer.render_source_type(cfg, MOD)
        assert "PyFloat_FromDouble(src_freq(&self->src, fs))" in src
        assert src.count("src_freq(") == 1
        seg = _composer.render_segment_type(cfg, MOD)
        assert "seg_n(&_seg, self->fs)" in seg
        assert seg.count("seg_n(") == 1
        assert "_seg.off_samples = self->off_samples;" in seg
        assert "src_freq" not in _composer.render_json_funcs(cfg, MOD)
        assert "seg_n" not in _composer.render_json_funcs(cfg, MOD)

    def test_an_enum_field_decodes_the_result(self) -> None:
        cfg = copy.deepcopy(_cfg())
        for f in cfg["module"][MOD]["source"]["fields"]:
            if f["name"] == "snr_mode":
                f["get_fn"] = "src_mode"
        src = _composer.render_source_type(cfg, MOD)
        assert "long _v = (long)(src_mode(&self->src, fs));" in src
        h = _composer.render_bridge_h(cfg, MOD)
        assert "int src_mode(const wfm_source_t *, double);" in h

    def test_a_ranged_field_resolves_its_scalar_read(self) -> None:
        cfg = _with({"get_fn": "src_freq"})
        cfg["module"][MOD]["source"]["ranged"] = [
            {"name": "freq", "flag": "R_FREQ"}
        ]
        src = _composer.render_source_type(cfg, MOD)
        assert "if (self->src.ranged & R_FREQ)" in src
        assert "return PyFloat_FromDouble(src_freq(&self->src, fs));" in (src)


class TestRefusals:
    @pytest.mark.parametrize(
        "field",
        [
            {"name": "iq", "type": "float _Complex*", "complex": True},
            {
                "name": "frame",
                "type": "frame_t *",
                "copy_fn": "fc",
                "free_fn": "ff",
                "parse_fn": "fp",
                "format_fn": "ft",
            },
        ],
        ids=["complex", "owned_pointer"],
    )
    def test_a_field_with_no_scalar_read(self, field: dict) -> None:
        cfg = copy.deepcopy(_cfg())
        fields = cfg["module"][MOD]["source"]["fields"]
        fields[:] = [f for f in fields if f["name"] != field["name"]]
        fields.append({**field, "get_fn": "nope"})
        with pytest.raises(ValueError, match="has neither read"):
            _composer.render_bridge_h(cfg, MOD)
        with pytest.raises(ValueError, match="has neither read"):
            _composer.render_source_type(cfg, MOD)

    def test_a_segment_field_with_no_segment_fs(self) -> None:
        cfg = _with(seg={"get_fn": "seg_n"})
        segf = cfg["module"][MOD]["segment"]["fields"]
        segf[:] = [f for f in segf if f["name"] != "fs"]
        with pytest.raises(ValueError, match="declares no `fs` field"):
            _composer.render_bridge_h(cfg, MOD)

    def test_one_function_two_prototypes(self) -> None:
        """gh-1739: one name, two return types, is refused naming both."""
        cfg = _with({"get_fn": "both"}, {"get_fn": "both"})
        with pytest.raises(ValueError, match="gh-1739") as e:
            _composer.render_bridge_h(cfg, MOD)
        assert "source field `freq` (get_fn)" in str(e.value)
        assert "segment field `num_samples` (get_fn)" in str(e.value)

    def test_one_function_one_prototype_is_declared_once(self) -> None:
        cfg = copy.deepcopy(_cfg())
        for f in cfg["module"][MOD]["source"]["fields"]:
            if f["name"] in ("seed", "pn_poly"):
                f.update(type="uint32_t", get_fn="shared")
        h = _composer.render_bridge_h(cfg, MOD)
        assert h.count("uint32_t shared(const wfm_source_t *, double);") == 1


# -- the compiled proof ------------------------------------------------------

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

PKG = "winp"
SIZE_MAX = 2**64 - 1

_HEADER = """\
#ifndef WIN_CORE_H
#define WIN_CORE_H
#include <complex.h>
#include <stddef.h>
#include <stdint.h>

/* "the standard's count": 2 * fs / bw. */
#define WIN_UNSET SIZE_MAX
/* "count / fs": the segment's span in seconds. */
#define SPAN_UNSET (-1.0)

typedef struct
{
  double bw;
  size_t window;
} wsrc_t;

typedef struct
{
  wsrc_t *sources;
  size_t  n_sources;
  double  fs;
  size_t  count;
  double  span;
} wseg_t;

typedef struct win_state win_state_t;
win_state_t *win_create (const wseg_t *s, size_t n, int r, int c);
size_t win_execute (win_state_t *st, float _Complex *o, size_t m);
const wseg_t *win_segments (const win_state_t *st, size_t *n, int *r,
                            int *c);
void win_destroy (win_state_t *st);
#endif
"""

_SOURCE = f"""\
#include "{PKG}/win/win_core.h"
#include <stdlib.h>
#include <string.h>
struct win_state
{{
  wseg_t *segs;
  size_t  n;
}};
win_state_t *
win_create (const wseg_t *s, size_t n, int r, int c)
{{
  (void)r;
  (void)c;
  win_state_t *st = calloc (1, sizeof *st);
  st->segs        = calloc (n, sizeof *st->segs);
  for (size_t i = 0; i < n; i++)
    {{
      st->segs[i]         = s[i];
      st->segs[i].sources = calloc (s[i].n_sources, sizeof (wsrc_t));
      memcpy (st->segs[i].sources, s[i].sources,
              s[i].n_sources * sizeof (wsrc_t));
    }}
  st->n = n;
  return st;
}}
size_t
win_execute (win_state_t *st, float _Complex *o, size_t m)
{{
  (void)st;
  (void)o;
  (void)m;
  return 0;
}}
const wseg_t *
win_segments (const win_state_t *st, size_t *n, int *r, int *c)
{{
  *n = st->n;
  *r = 0;
  *c = 0;
  return st->segs;
}}
void
win_destroy (win_state_t *st)
{{
  for (size_t i = 0; i < st->n; i++)
    free (st->segs[i].sources);
  free (st->segs);
  free (st);
}}
"""

#: The project's resolvers, written against the prototypes jm declares --
#: including the bridge is what holds the definitions to them.
_RESOLVE = f"""\
#include "{PKG}/win/win_bridge.h"

size_t
win_window (const wsrc_t *src, double fs)
{{
  if (src->window != WIN_UNSET)
    return src->window;
  return (size_t)(2.0 * fs / src->bw);
}}

double
win_span (const wseg_t *seg, double fs)
{{
  if (seg->span != SPAN_UNSET)
    return seg->span;
  return (double)seg->count / fs;
}}
"""

_BACKING_CMAKE = """\
add_library(backing_core OBJECT win_core.c win_resolve.c cJSON.c)
target_include_directories(backing_core PUBLIC ${CMAKE_SOURCE_DIR}/native/inc
                                               ${CMAKE_CURRENT_SOURCE_DIR})
set_target_properties(backing_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
# The test's cJSON stand-in is not the code under test.
set_source_files_properties(cJSON.c PROPERTIES COMPILE_OPTIONS "-w")
"""


def _module() -> dict:
    return {
        "kind": "composer",
        "backing": "win",
        "source": {
            "struct": "wsrc_t",
            "type_name": "Src",
            "fields": [
                {"name": "bw", "type": "double", "default": "1e6"},
                {
                    "name": "window",
                    "type": "size_t",
                    "default": "WIN_UNSET",
                    "get_fn": "win_window",
                },
            ],
        },
        "segment": {
            "type_name": "Seg",
            "struct": "wseg_t",
            "sources": "multi",
            "fields": [
                {"name": "fs", "type": "double", "default": "1e6"},
                {"name": "count", "type": "size_t", "default": "100"},
                {
                    "name": "span",
                    "type": "double",
                    "default": "SPAN_UNSET",
                    "get_fn": "win_span",
                },
            ],
        },
        "oo": {"composer_type_name": "Win"},
        "json": {"enabled": True},
        "extra_link_libs": ["backing_core"],
    }


#: Each line is one claim of the issue; a failure names it.
_DRIVER = """\
import json, sys
sys.path.insert(0, "src")
from winp.win.win import Src, Seg, Win

SIZE_MAX = 2**64 - 1

# The source field: the read resolves the sentinel at the source's fs...
s = Src(bw=10e6, fs=40e6)
assert s.window == 8, s.window
s.fs = 80e6
assert s.window == 16, ("the read follows fs", s.window)

# ...while the record carries the stored sentinel.
seg = Seg.sum(s, fs=40e6, count=4000)
text = Win([seg]).to_json()
doc = json.loads(text)
so = doc["segments"][0]["sources"][0]
assert so["window"] in (SIZE_MAX, str(SIZE_MAX)), so

# A stored value reads as itself, and the setter stores what it is given.
s.window = 5
assert s.window == 5
s.window = SIZE_MAX
assert s.window == 16, ("the setter stores the sentinel", s.window)

# The segment field: resolved from the segment's own fs and its scalars.
assert seg.span == 4000 / 40e6, seg.span
seg.fs = 80e6
assert seg.span == 4000 / 80e6, ("the read follows the segment's fs", seg.span)
assert doc["segments"][0]["span"] == -1.0, doc["segments"][0]
seg.span = 2.5
assert seg.span == 2.5
seg.span = -1.0
assert seg.span == 4000 / 80e6

# from_json keeps the stored values, and to_json writes them back unchanged.
back = Win.from_json(text)
assert back.to_json() == text, (back.to_json(), text)
bseg = back.segments[0]
assert bseg.sources[0].window == 8, bseg.sources[0].window
assert bseg.span == 4000 / 40e6, bseg.span
print("GET_FN OK")
"""


def _run(cmd: list, cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (cmd, r.stdout[-4000:], r.stderr[-4000:])


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    if _NO_TOOLCHAIN:
        pytest.skip("no cmake / C compiler")
    tmp = tmp_path_factory.mktemp("gh2710")
    assert run_cli("new", PKG, cwd=tmp).returncode == 0
    proj = tmp / PKG
    inc = proj / "native" / "inc" / PKG / "win"
    inc.mkdir(parents=True, exist_ok=True)
    (inc / "win_core.h").write_text(_HEADER, encoding="utf-8")
    backing = proj / "native" / "src" / "backing"
    backing.mkdir(parents=True, exist_ok=True)
    (backing / "win_core.c").write_text(_SOURCE, encoding="utf-8")
    (backing / "win_resolve.c").write_text(_RESOLVE, encoding="utf-8")
    (backing / "cJSON.h").write_text(CJSON_H, encoding="utf-8")
    (backing / "cJSON.c").write_text(CJSON_C, encoding="utf-8")
    (backing / "CMakeLists.txt").write_text(_BACKING_CMAKE, encoding="utf-8")
    cfg = C.load(proj)
    cfg["project"]["c_deps"] = ["backing"]
    # The test's backing links into the extension only; no C consumer.
    cfg["project"]["status_allow"] = ["CMakeLists.txt:backing_core"]
    cfg.setdefault("module", {})["win"] = _module()
    C.save(proj, cfg)
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    # Warning-clean, so an undeclared call (implicit declaration) is red.
    _run(
        [
            "cmake",
            "-S",
            ".",
            "-B",
            "build",
            f"-DPython3_EXECUTABLE={sys.executable}",
            "-DCMAKE_C_FLAGS=-Wall -Wextra -Werror",
        ],
        proj,
    )
    _run(["cmake", "--build", "build", "--parallel", "4"], proj)
    return proj


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
class TestEndToEnd:
    def test_the_read_resolves_and_the_record_keeps_the_sentinel(
        self, project: Path
    ) -> None:
        driver = project / "drive_gh2710.py"
        driver.write_text(_DRIVER, encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(driver)],
            cwd=project,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0 and "GET_FN OK" in r.stdout, (
            r.stdout + r.stderr
        )

    def test_the_manifest_round_trips(self, project: Path) -> None:
        """`apply` again changes nothing, `status --check` is clean, and
        the key survives a save."""
        frag = project / "modules" / "win.toml"
        before = frag.read_text(encoding="utf-8")
        assert before.count("get_fn = ") == 2, before
        assert run_cli("apply", cwd=project).returncode == 0
        r = run_cli("status", "--check", cwd=project)
        assert r.returncode == 0, r.stdout + r.stderr
        assert frag.read_text(encoding="utf-8") == before
        cfg = C.load(project)
        C.save(project, cfg)
        assert frag.read_text(encoding="utf-8") == before
