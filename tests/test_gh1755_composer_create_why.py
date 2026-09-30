"""gh-1755 / gh-1758: a composer's create is the author's, and can name why
it refused.

gh-1758: ``[module.X] create_fn`` was an accepted composer key that nothing
read -- every face called ``<backing>_create``. It is now honoured: set, it
names the function every face builds the state with, exactly as written (an
author-named key, never prefixed); unset, the ``<backing>_create`` default
is unchanged.

gh-1755: the generated ``Composer([...])`` turned any NULL from that create
into a fixed ``ValueError("<create> failed")``, while the host already knew
the sentence -- the bridge's ``bridge_error_fn`` (gh-1307) raised it for
``Synth(...).steps()`` over the very same source. ``[module.X] create_why =
true`` is the constructor's form of gh-1706's ``from_json_why``: a switch
saying the create function -- ``create_fn`` or the default -- takes a
trailing ``const char **why``. doppler writes ``create_fn =
"dp_wfm_compose_create_why"`` and ``create_why = true``.

Every face that builds a composer from segments goes through the one helper,
``_composer._create_call``: the ``Composer`` constructor and the generic
``from_json`` / ``from_file`` record raise the sentence through
``_diagnostics.reason_raise_c``; the c-face CLI prints it. A refusal with no
sentence keeps each face's old message, a module with neither key renders
byte-identically, and a non-bool ``create_why`` is refused at load by
gh-1722's check.
"""

from __future__ import annotations

import copy
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _composer  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit import _csym  # noqa: E402
from test_composer_codegen import _cfg  # noqa: E402
from test_gh1711_composer_owned_pointer import (  # noqa: E402
    COMPOSER_TOML,
    FRAME,
    MOD,
    PLAYLIST_C,
    PLAYLIST_H,
    _cli,
    _ok,
    _run,
    build_project,
)


def _mod_cfg(**keys) -> dict:
    """The shared composer fixture with *keys* on ``[module.X]``, and the
    JSON record and CLI enabled so every face that calls create renders."""
    cfg = copy.deepcopy(_cfg())
    mod = cfg["module"][MOD]
    mod.update(keys)
    mod["json"] = {"enabled": True}
    mod["cli"] = {"enabled": True, "name": "wfm_cli"}
    return cfg


def _render(cfg: dict) -> "dict[str, str]":
    """Every composer emitter, by face -- the byte-identity check compares
    all of them, so a change leaking into one it should not is seen."""
    return {
        "ext": _composer.render_ext(cfg, MOD),
        "json": _composer.render_json_funcs(cfg, MOD),
        "cli": _composer.render_cli(cfg, MOD),
        "bridge": _composer.render_bridge_h(cfg, MOD),
        "pyi": _composer.render_pyi(cfg, MOD),
        "cmake": _composer.render_cmake(cfg, MOD),
    }


# -- create_fn is honoured (gh-1758) ------------------------------------------


class TestCreateFnIsHonoured:
    @pytest.fixture
    def out(self) -> "dict[str, str]":
        return _render(_mod_cfg(create_fn="wfm_make"))

    def test_every_face_calls_it(self, out: "dict[str, str]") -> None:
        assert (
            "self->state = wfm_make(segs, n, repeat, continuous);"
            in out["ext"]
        )
        assert (
            'PyErr_SetString(PyExc_ValueError, "wfm_make failed");'
            in out["ext"]
        )
        assert (
            "wfm_compose_state_t *st = wfm_make(segs, n, repeat, continuous);"
            in out["json"]
        )
        assert "c = wfm_make(&seg, 1, repeat, continuous);" in out["cli"]

    def test_no_face_calls_the_default(self, out: "dict[str, str]") -> None:
        assert "wfm_compose_create(" not in "\n".join(out.values())

    def test_it_is_used_as_written_under_a_prefix(self) -> None:
        """An author-named key: a prefix moves the backing's derived API
        (a component backing, gh-1685), never the name the author wrote."""
        cfg = _mod_cfg(create_fn="wfm_make")
        cfg["project"]["c_prefix"] = "zz"
        cfg["wfm_compose"] = {}  # `backing` now names a jm component
        ext = _composer.render_ext(cfg, MOD)
        assert "self->state = wfm_make(segs, n, repeat, continuous);" in ext
        assert "zz_wfm_compose_destroy(" in ext
        assert "create_fn" in _csym.AUTHOR_NAMED_KEYS

    def test_a_prefix_respell_leaves_the_key_alone(self) -> None:
        """`jm upgrade` respells the manifest's C through `respell_manifest`;
        the ``create_fn`` value is the author's name, and stays."""
        text = (
            '[module.mix]\nkind = "composer"\nbacking = "mix"\n'
            'create_fn = "mix_make"\n'
        )
        names = {"mix_make": "zz_mix_make", "mix_create": "zz_mix_create"}
        assert _csym.respell_manifest(text, names, {}) == text


# -- create_why passes the reason (gh-1755) -----------------------------------


class TestEveryFacePassesTheReason:
    @pytest.fixture(params=["default", "named"])
    def case(self, request) -> "tuple[str, dict[str, str]]":
        if request.param == "default":
            return "wfm_compose_create", _render(_mod_cfg(create_why=True))
        return "wfm_make_why", _render(
            _mod_cfg(create_fn="wfm_make_why", create_why=True)
        )

    def test_the_constructor_passes_and_raises_it(self, case) -> None:
        fn, out = case
        call = (
            "    const char *_why = NULL;\n"
            f"    self->state = {fn}(segs, n, repeat, continuous, &_why);\n"
        )
        assert out["ext"].count(call) == 1, "no reason-passing create call"
        after = out["ext"].split(call, 1)[1]
        raise_ = after.split("if (!self->state) {", 1)[1].split("}", 1)[0]
        assert "PyErr_SetString(PyExc_ValueError, _why);" in raise_
        # ...and the fixed text is still the raise when there is none.
        assert f'"{fn} failed"' in raise_

    def test_the_record_threads_it_from_the_factories(self, case) -> None:
        fn, out = case
        j = out["json"]
        assert "_from_root(cJSON *root, const char **why)" in j
        assert f"{fn}(segs, n, repeat, continuous, why);" in j
        assert j.count("_from_root(root, &_why);") == 2
        assert j.count("_wrap_state((PyTypeObject *)cls, st, _why);") == 2
        assert "PyErr_SetString(PyExc_ValueError, why);" in j
        assert '"invalid composer spec"' in j

    def test_the_cli_passes_and_prints_it(self, case) -> None:
        fn, out = case
        c = out["cli"]
        assert "const char *why = NULL;" in c
        assert f"c = {fn}(&seg, 1, repeat, continuous, &why);" in c
        assert 'why ? why : "failed to build composer"' in c

    def test_parse_why_and_create_why_share_one_record_parameter(
        self,
    ) -> None:
        """Both switches thread the record's ``why``; one parameter, not
        two, whichever is on."""
        cfg = _mod_cfg(create_why=True)
        cfg["module"][MOD]["source"]["fields"].append(
            {**FRAME, "parse_why": True}
        )
        j = _render(cfg)["json"]
        assert "_from_root(cJSON *root, const char **why)" in j
        assert "wfm_compose_create(segs, n, repeat, continuous, why);" in j
        assert "frame_parse(_s, why)" in j


class TestWithoutTheKeys:
    def test_output_is_unchanged(self) -> None:
        """No keys, ``create_why = false`` and ``create_fn`` spelling the
        default all render the same text: the four-argument default create
        with the fixed message."""
        plain = _render(_mod_cfg())
        assert plain == _render(_mod_cfg(create_why=False))
        assert plain == _render(_mod_cfg(create_fn="wfm_compose_create"))
        assert (
            "self->state = wfm_compose_create(segs, n, repeat, continuous);"
            in plain["ext"]
        )
        assert (
            'PyErr_SetString(PyExc_ValueError, "wfm_compose_create failed");'
            in plain["ext"]
        )
        assert (
            "c = wfm_compose_create(&seg, 1, repeat, continuous);"
            in plain["cli"]
        )
        assert "const char **why" not in plain["json"]
        assert "why" not in plain["cli"]


class TestTheKeys:
    def test_they_survive_a_save(self, tmp_path: Path) -> None:
        C.save(tmp_path, _mod_cfg(create_fn="wfm_make", create_why=True))
        text = (tmp_path / "just-makeit.toml").read_text(encoding="utf-8")
        assert "create_why = true" in text, text
        assert 'create_fn = "wfm_make"' in text, text
        mod = C.load(tmp_path)["module"][MOD]
        assert mod["create_why"] is True
        assert mod["create_fn"] == "wfm_make"

    def test_a_function_name_is_refused_at_load(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """gh-1722's check: a name is truthy, and would render a call
        whose trailing argument the named function may not take."""
        C.save(tmp_path, _mod_cfg(create_why="wfm_make_why"))
        with pytest.raises(SystemExit):
            C.load(tmp_path)
        err = capsys.readouterr().err
        assert (
            f"[module.{MOD}] create_why = 'wfm_make_why' must be true or false"
        ) in err, err
        assert "Name the function in create_fn." in err, err


# -- end to end --------------------------------------------------------------

#: gh-1711's host with three constructors, each refusing differently, so the
#: message says which one jm called:
#:
#: - the default ``playlist_create`` refuses only a gain above 1000;
#: - ``playlist_make`` also refuses a negative gain, silently;
#: - ``playlist_make_why`` names the negative gain, and refuses above 1000
#:   with no sentence.
_EXTRA_H = """\
playlist_state_t *playlist_make (const track_t *tracks, size_t n,
                                 int repeat, int continuous);
playlist_state_t *playlist_make_why (const track_t *tracks, size_t n,
                                     int repeat, int continuous,
                                     const char **why);
size_t playlist_execute"""
_H = PLAYLIST_H.replace("size_t playlist_execute", _EXTRA_H, 1)
_CREATE = (
    "playlist_state_t *\n"
    "playlist_create (const track_t *tracks, size_t n, int repeat, "
    "int continuous)\n{\n"
)
_C = PLAYLIST_C.replace(
    _CREATE,
    "static playlist_state_t *\n"
    "playlist_build (const track_t *tracks, size_t n, int repeat, "
    "int continuous)\n{\n",
) + (
    """
/* 1: a gain above 1000; -1: a negative gain; 0: accepted. */
static int
playlist_check (const track_t *tracks, size_t n)
{
  for (size_t i = 0; i < n; i++)
    for (size_t k = 0; k < tracks[i].n_sources; k++)
      {
        if (tracks[i].sources[k].gain > 1000.0)
          return 1;
        if (tracks[i].sources[k].gain < 0.0)
          return -1;
      }
  return 0;
}

"""
    + _CREATE
    + """\
  if (playlist_check (tracks, n) > 0)
    return NULL;
  return playlist_build (tracks, n, repeat, continuous);
}

playlist_state_t *
playlist_make (const track_t *tracks, size_t n, int repeat, int continuous)
{
  if (playlist_check (tracks, n) != 0)
    return NULL;
  return playlist_build (tracks, n, repeat, continuous);
}

playlist_state_t *
playlist_make_why (const track_t *tracks, size_t n, int repeat,
                   int continuous, const char **why)
{
  int bad = playlist_check (tracks, n);
  if (bad < 0)
    *why = "gain must be non-negative";
  if (bad != 0)
    return NULL;
  return playlist_build (tracks, n, repeat, continuous);
}
"""
)
assert _C.count("playlist_build (tracks") == 3 and _H != PLAYLIST_H


def _toml(**keys: str) -> str:
    lines = "".join(f"{k} = {v}\n" for k, v in keys.items())
    out = COMPOSER_TOML.replace(
        'backing = "playlist"\n', f'backing = "playlist"\n{lines}', 1
    )
    assert out != COMPOSER_TOML or not keys
    return out


#: What each project's faces must say, by mode: the default create, the
#: author's silent ``create_fn``, and the author's reason-naming one.
DRIVE = """\
import json
import os
import sys
import tempfile

sys.path.insert(0, "src")

from studio.playlist.playlist import Clip, Mix, Track

MODE = os.environ["JM_MODE"]
FN = {
    "default": "playlist_create",
    "named": "playlist_make",
    "named_why": "playlist_make_why",
}[MODE]
REASON = "gain must be non-negative"


def refused(fn):
    try:
        fn()
    except ValueError as e:
        return str(e)
    raise AssertionError("expected a ValueError")


def mix(gain):
    return Mix(Track.sum(Clip(gain=gain), dur=1))


# Every constructor refuses a gain above 1000, silently: the fixed text,
# naming the function jm called.
assert refused(lambda: mix(2000.0)) == f"{FN} failed"
# A negative gain: only the author's creates refuse it, and only the
# reason-naming one says why.
if MODE == "default":
    mix(-1.0).close()
else:
    want = REASON if MODE == "named_why" else f"{FN} failed"
    assert refused(lambda: mix(-1.0)) == want

m = mix(1.0)
doc = json.loads(m.to_json())
m.close()

# The generic record, from text and from a file.
doc["segments"][0]["sources"][0]["gain"] = -1.0
bad = json.dumps(doc)
fd, path = tempfile.mkstemp(suffix=".json")
with os.fdopen(fd, "w") as fh:
    fh.write(bad)
try:
    if MODE == "default":
        Mix.from_json(bad).close()
        Mix.from_file(path).close()
    else:
        want = REASON if MODE == "named_why" else "invalid composer spec"
        assert refused(lambda: Mix.from_json(bad)) == want
        assert refused(lambda: Mix.from_file(path)) == want
finally:
    os.unlink(path)
doc["segments"][0]["sources"][0]["gain"] = 2000.0
assert refused(lambda: Mix.from_json(json.dumps(doc))) == (
    "invalid composer spec"
)
print("create: PASSED")
"""

_MODES = {
    "default": {},
    "named": {"create_fn": '"playlist_make"'},
    "named_why": {"create_fn": '"playlist_make_why"', "create_why": "true"},
}


@pytest.fixture(scope="module", params=list(_MODES))
def project(request, tmp_path_factory) -> "tuple[str, Path]":
    mode = request.param
    root = build_project(
        tmp_path_factory.mktemp(f"g1755{mode}") / "studio",
        _H,
        _C,
        _toml(**_MODES[mode]),
    )
    return mode, root


def test_every_python_face_calls_the_declared_create(project) -> None:
    mode, root = project
    r = subprocess.run(
        [sys.executable, "-c", DRIVE],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=600,
        env={
            **os.environ,
            "PYTHONPATH": str(root / "src"),
            "JM_MODE": mode,
        },
    )
    assert "create: PASSED" in _ok(r)


def test_the_cli_calls_it_and_prints_the_reason(project) -> None:
    mode, root = project
    r = _run([str(_cli(root)), "--gain", "2000", "--out", os.devnull], root)
    assert r.returncode == 1, r.stderr
    assert "failed to build composer" in r.stderr, r.stderr
    r = _run([str(_cli(root)), "--gain", "-1", "--out", os.devnull], root)
    if mode == "default":
        assert r.returncode == 0, r.stderr
        return
    assert r.returncode == 1, r.stderr
    want = (
        "gain must be non-negative"
        if mode == "named_why"
        else "failed to build composer"
    )
    assert want in r.stderr, r.stderr
