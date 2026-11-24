"""A composer field's Python setter writes through the project's C
(gh-2753), the mirror of gh-2710's ``get_fn``.

A source or segment field's setter stored the converted value in its own
member, so a field that is half of a pair could not keep the pair whole.
doppler's ofdm source gives a rate as ``constellation`` and ``code_rate``,
both ``auto`` by default: ``s.code_rate = "3/4"`` on a default source
stored half a pair, which the object then refuses to render.

``set_fn = "<fn>"`` on a ``[[module.X.source.fields]]`` or
``[[module.X.segment.fields]]`` row names the project's writer,
``const char *fn(<struct> *, <type> value)``, declared once in
``<cname>_bridge.h`` beside the other seams. The setter converts and
checks first (an enum's name to its index included), then calls ``fn``
instead of storing: ``NULL`` means written, siblings included; a sentence
is raised as ``ValueError`` and the struct is left as it was. The
constructor, ``from_json`` and the record do not call it.

Decisions:

1. Refused at load (and again at render, through the same
   :func:`_composer.set_fn`) on a ``bytes``, ``complex``, owned-pointer or
   ranged field: none has one value to hand over, and a ranged field's
   setter writing through ``fn`` for a scalar and around it for a
   ``(lo, hi)`` pair would keep the pair whole only some of the time.
2. A Segment holds its scalars as ``self-><name>``, not in a struct, so
   its setter builds the snapshot a segment ``get_fn`` reads (scalars and
   sources), hands it to ``fn``, and on ``NULL`` copies every scalar back
   through the same ``_segment_copy_c`` spelling, the other way round. On
   a sentence nothing is copied back.

GATE: a compiled composer implementing doppler's pair case: setting one
half while the other is ``auto`` fixes the other at its resolved value, a
third field refused beside a given pair raises and leaves both halves as
they were, and neither the constructor nor ``from_json`` calls the hook.
A segment field's hook sees the segment's sources and fixes a sibling.
"""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli

from just_makeit import _composer
from just_makeit import _config as C
from just_makeit import _csym
from just_makeit import _keys
from just_makeit._report import Refusal

sys.path.insert(0, str(Path(__file__).parent))
import test_gh2710_composer_field_get_fn as base  # noqa: E402
import test_gh2717_segment_flat_get_fn as flat  # noqa: E402

PKG = base.PKG

#: The pair's halves share one enum; index 0 is "not given".
_ENUMS = [{"name": "ab", "values": ["auto", "x", "y", "z"]}]


def _module() -> dict:
    """gh-2710's composer with doppler's pair on its source -- ``a`` and
    ``b``, both ``auto`` -- and ``wlan``, refused beside a given pair, each
    written through a ``set_fn``; and a segment ``count`` written through
    one that fixes its sibling ``span``."""
    m = copy.deepcopy(base._module())
    m["source"]["fields"] += [
        {
            "name": "a",
            "type": "int",
            "enum": "ab",
            "default": "auto",
            "set_fn": "win_set_a",
        },
        {
            "name": "b",
            "type": "int",
            "enum": "ab",
            "default": "auto",
            "set_fn": "win_set_b",
        },
        {
            "name": "wlan",
            "type": "int",
            "default": "0",
            "set_fn": "win_set_wlan",
        },
    ]
    for f in m["segment"]["fields"]:
        if f["name"] == "count":
            f["set_fn"] = "win_seg_set_count"
    return m


def _cfg(m: "dict | None" = None) -> dict:
    return {
        "project": {"name": PKG},
        "enum": copy.deepcopy(_ENUMS),
        "module": {"win": m or _module()},
    }


def _body(text: str, fn: str) -> str:
    """The body of the generated C function *fn* in *text*."""
    return text.split(f"{fn}(", 1)[1].split("\n}\n", 1)[0]


class TestTheKey:
    def test_recognised_and_written_back(self) -> None:
        cfg = _cfg()
        assert "set_fn" in _keys.COMPOSER_FIELD_KEYS
        assert _keys.unknown_keys(cfg) == []
        text = C._dump(cfg)
        for fn in ("win_set_a", "win_set_b", "win_seg_set_count"):
            assert f'set_fn = "{fn}"' in text

    def test_an_author_named_key(self) -> None:
        """jm derives no part of the name (gh-1694)."""
        assert "set_fn" in _csym.AUTHOR_NAMED_KEYS
        assert "win_set_a" in _composer.seam_fns(_cfg(), "win")


class TestTheSeam:
    def test_each_prototype_is_declared_in_the_bridge(self) -> None:
        h = _composer.render_bridge_h(_cfg(), "win")
        assert "const char *win_set_a(wsrc_t *, int);" in h
        assert "const char *win_set_wlan(wsrc_t *, int);" in h
        assert "const char *win_seg_set_count(wseg_t *, size_t);" in h

    def test_one_function_as_get_fn_and_set_fn_is_refused(self) -> None:
        """gh-1739: one name, two prototypes."""
        m = _module()
        m["source"]["fields"][1]["set_fn"] = "win_window"
        with pytest.raises(Refusal, match="gh-1739"):
            _composer.render_bridge_h(_cfg(m), "win")


class TestTheSourceSetter:
    def test_an_enum_calls_it_after_the_index_and_stores_nothing(
        self,
    ) -> None:
        src = _composer.render_source_type(_cfg(), "win")
        body = _body(src, "Src_set_a")
        order = [
            "_enum_index(_enum_ab, s);",
            "const char *_why = win_set_a(&self->src, _i);",
            "PyErr_SetString(PyExc_ValueError, _why);",
            "return 0;",
        ]
        at = [body.index(s) for s in order]
        assert at == sorted(at), body
        assert "self->src.a =" not in body

    def test_a_scalar_calls_it_after_the_range_check(self) -> None:
        src = _composer.render_source_type(_cfg(), "win")
        body = _body(src, "Src_set_wlan")
        assert body.index('PyArg_Parse(value, "i", &_v)') < body.index(
            "win_set_wlan("
        )
        assert "win_set_wlan(&self->src, _v);" in body
        assert "self->src.wlan =" not in body

    def test_only_the_setter_calls_it(self) -> None:
        cfg = _cfg()
        src = _composer.render_source_type(cfg, "win")
        for fn in ("win_set_a", "win_set_b", "win_set_wlan"):
            assert src.count(f"{fn}(") == 1, fn
        assert "win_set_" not in _composer.render_json_funcs(cfg, "win")
        assert "win_set_" not in _composer.render_composer_type(cfg, "win")

    def test_undeclared_stores_the_member(self) -> None:
        m = _module()
        del m["source"]["fields"][-1]["set_fn"]
        src = _composer.render_source_type(_cfg(m), "win")
        assert "    self->src.wlan = _v;\n" in src


class TestTheSegmentSetter:
    def test_snapshot_call_then_copy_back(self) -> None:
        seg = _composer.render_segment_type(_cfg(), "win")
        body = _body(seg, "Seg_set_count")
        order = [
            "_seg.count = self->count;",
            "_Seg_sources_c(self->sources, &_ns);",
            "if (!_srcs)\n        return -1;",
            "_seg.sources = _srcs;",
            "const char *_why = win_seg_set_count(&_seg, _v);",
            "free(_srcs);",
            "PyErr_SetString(PyExc_ValueError, _why);",
            "self->count = _seg.count;",
            "self->span = _seg.span;",
            "return 0;",
        ]
        at = [body.index(s) for s in order]
        assert at == sorted(at), body
        assert seg.count("win_seg_set_count(") == 1


class TestRefusals:
    @pytest.mark.parametrize(
        "field,kind",
        [
            ({"name": "bits", "type": "uint8_t*", "bytes": True}, "bytes"),
            (
                {"name": "iq", "type": "float _Complex*", "complex": True},
                "complex",
            ),
            (
                {
                    "name": "frame",
                    "type": "frame_t *",
                    "copy_fn": "fc",
                    "free_fn": "ff",
                    "parse_fn": "fp",
                    "format_fn": "ft",
                },
                "owned-pointer",
            ),
        ],
        ids=["bytes", "complex", "owned_pointer"],
    )
    def test_a_field_with_no_single_value(
        self, field: dict, kind: str
    ) -> None:
        m = _module()
        m["source"]["fields"].append({**field, "set_fn": "nope"})
        cfg = _cfg(m)
        match = f"{kind}` field has no single value|{kind} field has no"
        errs = _composer.set_fn_errors(cfg)
        assert len(errs) == 1 and "gh-2753" in errs[0], errs
        assert kind in errs[0], errs
        with pytest.raises(Refusal, match=match):
            _composer.render_source_type(cfg, "win")
        with pytest.raises(Refusal, match=match):
            _composer.render_bridge_h(cfg, "win")

    @pytest.mark.parametrize("where", ["source", "segment"])
    def test_a_ranged_field(self, where: str) -> None:
        m = _module()
        name = "bw" if where == "source" else "span"
        for f in m[where]["fields"]:
            if f["name"] == name:
                f.pop("get_fn", None)
                f["set_fn"] = "nope"
        m[where]["ranged"] = [{"name": name, "flag": "R_X"}]
        cfg = _cfg(m)
        errs = _composer.set_fn_errors(cfg)
        assert len(errs) == 1 and "a ranged field" in errs[0], errs
        render = getattr(_composer, f"render_{where}_type")
        with pytest.raises(Refusal, match="a ranged field"):
            render(cfg, "win")

    def test_refused_when_the_manifest_loads(self, tmp_path: Path) -> None:
        """Not only at render: `load` stops it before anything is written,
        naming the field and why."""
        assert run_cli("new", PKG, cwd=tmp_path).returncode == 0
        proj = tmp_path / PKG
        cfg = C.load(proj)
        m = _module()
        m["source"]["ranged"] = [{"name": "wlan", "flag": "R_W"}]
        cfg.setdefault("module", {})["win"] = m
        cfg["enum"] = copy.deepcopy(_ENUMS)
        C.save(proj, cfg)
        with pytest.raises(
            Refusal, match="composer `win` source field 'wlan'.*ranged"
        ):
            C.load(proj)
        r = run_cli("apply", cwd=proj)
        assert r.returncode != 0
        assert "composer `win` source field 'wlan'" in r.stdout + r.stderr

    def test_the_refusal_names_the_module_and_the_table(self) -> None:
        m = _module()
        for f in m["segment"]["fields"]:
            if f["name"] == "span":
                f.pop("get_fn")
                f["set_fn"] = "nope"
        m["segment"]["ranged"] = [{"name": "span", "flag": "R_S"}]
        (err,) = _composer.set_fn_errors(_cfg(m))
        assert err.startswith("composer `win` segment field 'span': "), err

    def test_a_segment_owned_pointer_gets_gh1711s_refusal(self) -> None:
        """A segment field with owned-pointer keys belongs in
        ``source.fields``; that is what to say, not that ``set_fn`` has no
        single value to hand it."""
        m = _module()
        m["segment"]["fields"].append(
            {
                "name": "frame",
                "type": "frame_t *",
                "copy_fn": "fc",
                "free_fn": "ff",
                "parse_fn": "fp",
                "format_fn": "ft",
                "set_fn": "nope",
            }
        )
        (err,) = _composer.set_fn_errors(_cfg(m))
        assert "gh-1711" in err and "source.fields" in err, err
        assert "gh-2753" not in err, err


# -- the compiled proof ------------------------------------------------------

_HEADER = base._HEADER.replace(
    "  size_t window;\n} wsrc_t;",
    "  size_t window;\n  int a;\n  int b;\n  int wlan;\n} wsrc_t;",
)
assert _HEADER != base._HEADER

#: doppler's pair case. A half's `auto` (0) reads as a resolved value
#: (`x` for `a`, `y` for `b`); giving one half while the other is `auto`
#: fixes the other at it. `wlan` is refused beside a given pair, and a
#: given half is refused beside `wlan`. Every refusal writes nothing.
_RESOLVE = (
    base._RESOLVE
    + """
#define AB_AUTO 0
#define A_RESOLVED 1 /* "x" */
#define B_RESOLVED 2 /* "y" */

const char *
win_set_a (wsrc_t *s, int v)
{
  if (s->wlan && v != AB_AUTO)
    return "a is fixed by wlan";
  if (v != AB_AUTO && s->b == AB_AUTO)
    s->b = B_RESOLVED;
  s->a = v;
  return NULL;
}

const char *
win_set_b (wsrc_t *s, int v)
{
  if (s->wlan && v != AB_AUTO)
    return "b is fixed by wlan";
  if (v != AB_AUTO && s->a == AB_AUTO)
    s->a = A_RESOLVED;
  s->b = v;
  return NULL;
}

const char *
win_set_wlan (wsrc_t *s, int v)
{
  if (v && (s->a != AB_AUTO || s->b != AB_AUTO))
    return "wlan is refused beside a given pair";
  s->wlan = v;
  return NULL;
}

/* A segment's count: refused at 0, and refused with no sources (which the
 * snapshot must carry); a new count resets the span it fixed. */
const char *
win_seg_set_count (wseg_t *g, size_t v)
{
  if (v == 0)
    return "count must be positive";
  if (g->n_sources == 0)
    return "a segment with no sources has no count";
  g->count = v;
  g->span  = SPAN_UNSET;
  return NULL;
}
"""
)

_DRIVER = """\
import json, sys
sys.path.insert(0, "src")
from winp.win.win import Src, Seg, Win

def refused(set_, why):
    try:
        set_()
    except ValueError as e:
        assert str(e) == why, (str(e), why)
    else:
        raise AssertionError("not refused: " + why)

# Each block is one claim of gh-2753.
# 1. Setting one half while the other is auto fixes the other.
s = Src()
assert (s.a, s.b) == ("auto", "auto")
s.b = "z"
assert (s.a, s.b) == ("x", "z"), ("a fixed at its resolved value", s.a)
s = Src()
s.a = "z"
assert (s.a, s.b) == ("z", "y"), ("b fixed at its resolved value", s.b)
# A half given beside a given pair moves only itself.
s.b = "x"
assert (s.a, s.b) == ("z", "x"), (s.a, s.b)

# 2. Refused beside a given pair: ValueError with the C sentence, and the
# struct is unchanged -- both halves and the field itself read back.
refused(lambda: setattr(s, "wlan", 1), "wlan is refused beside a given pair")
assert (s.a, s.b, s.wlan) == ("z", "x", 0), (s.a, s.b, s.wlan)
w = Src()
w.wlan = 1
refused(lambda: setattr(w, "a", "y"), "a is fixed by wlan")
assert (w.a, w.b, w.wlan) == ("auto", "auto", 1), (w.a, w.b, w.wlan)

# 3. The conversion runs first: a bad name or type never reaches the hook.
refused(lambda: setattr(s, "a", "q"),
        "invalid a 'q' (choices: auto, x, y, z)")
assert (s.a, s.b) == ("z", "x")
try:
    s.wlan = "one"
except TypeError:
    pass
else:
    raise AssertionError("a str wlan reached the hook")

# 4. The constructor does not call it: half a pair stays half, and a
# combination the hook refuses is accepted.
c = Src(a="y")
assert (c.a, c.b) == ("y", "auto"), ("ctor stores as given", c.a, c.b)
cw = Src(a="y", wlan=1)
assert (cw.a, cw.b, cw.wlan) == ("y", "auto", 1)

# 5. from_json does not call it either: the record round-trips as stored.
seg = Seg.sum(Src(a="y", wlan=1), count=50)
text = Win([seg]).to_json()
so = json.loads(text)["segments"][0]["sources"][0]
assert (so["a"], so["b"], so["wlan"]) == ("y", "auto", 1), so
back = Win.from_json(text).segments[0].sources[0]
assert (back.a, back.b, back.wlan) == ("y", "auto", 1)
assert Win.from_json(text).to_json() == text

# 6. A segment field: the hook sees the segment's sources and fixes a
# sibling, which is copied back; a refusal leaves the segment untouched.
seg = Seg.sum(Src(), fs=100.0, count=50)
seg.span = 2.5
seg.count = 10
assert seg.count == 10 and seg.span == 10 / 100.0, (seg.count, seg.span)
seg.span = 7.0
refused(lambda: setattr(seg, "count", 0), "count must be positive")
assert (seg.count, seg.span) == (10, 7.0), (seg.count, seg.span)
seg.sources.clear()
refused(lambda: setattr(seg, "count", 5),
        "a segment with no sources has no count")
assert seg.count == 10
# ...and its constructor does not call it.
assert Seg.sum(Src(), count=0).count == 0
seg = Seg.sum(Src(), count=0)
back = Win.from_json(Win([seg]).to_json()).segments[0]
assert back.count == 0, back.count
print("SET_FN OK")
"""


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    if base._NO_TOOLCHAIN:
        pytest.skip("no cmake / C compiler")
    return flat.build(
        tmp_path_factory.mktemp("gh2753"),
        _module(),
        header=_HEADER,
        resolve=_RESOLVE,
        enums=copy.deepcopy(_ENUMS),
    )


@pytest.mark.slow
@pytest.mark.skipif(base._NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_the_setter_writes_through_the_hook(project: Path) -> None:
    driver = project / "drive_gh2753.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(driver)],
        cwd=project,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0 and "SET_FN OK" in r.stdout, r.stdout + r.stderr
