"""gh-1755: a composer's ``create`` can name why it refused.

The generated ``Composer([...])`` calls ``<backing>_create`` and turned any
NULL into a fixed ``ValueError("<backing>_create failed")``, while the host
already knew the sentence -- the bridge's ``bridge_error_fn`` (gh-1307) raised
it for ``Synth(...).steps()`` over the very same source.

``[module.X] create_why = true`` is the constructor's form of gh-1706's
``from_json_why``: jm calls ``<backing>_create_why``, the host's
reason-naming constructor ``<state_t> *(segs, n, repeat, continuous,
const char **why)``, beside the plain ``<backing>_create`` the host keeps.
Every face that builds a composer from segments goes through the one helper,
``_composer._create_call``: the ``Composer`` constructor and the generic
``from_json`` / ``from_file`` record raise the sentence through
``_diagnostics.reason_raise_c``; the c-face CLI prints it. A refusal with no
sentence keeps each face's old message, a module without the key renders
byte-identically, and a non-bool value is refused at load by gh-1722's check.
"""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _composer  # noqa: E402
from just_makeit import _config as C  # noqa: E402
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


# -- the rendering -----------------------------------------------------------


class TestEveryFacePassesTheReason:
    @pytest.fixture
    def out(self) -> "dict[str, str]":
        return _render(_mod_cfg(create_why=True))

    def test_the_constructor_passes_and_raises_it(
        self, out: "dict[str, str]"
    ) -> None:
        call = (
            "    const char *_why = NULL;\n"
            "    self->state = "
            "wfm_compose_create_why(segs, n, repeat, continuous, &_why);\n"
        )
        assert out["ext"].count(call) == 1, "no reason-naming create call"
        after = out["ext"].split(call, 1)[1]
        raise_ = after.split("if (!self->state) {", 1)[1].split("}", 1)[0]
        assert "PyErr_SetString(PyExc_ValueError, _why);" in raise_
        # ...and the fixed text is still the raise when there is none.
        assert '"wfm_compose_create failed"' in raise_

    def test_the_record_threads_it_from_the_factories(
        self, out: "dict[str, str]"
    ) -> None:
        j = out["json"]
        assert "_from_root(cJSON *root, const char **why)" in j
        assert "wfm_compose_create_why(segs, n, repeat, continuous, why);" in j
        assert j.count("_from_root(root, &_why);") == 2
        assert j.count("_wrap_state((PyTypeObject *)cls, st, _why);") == 2
        assert "PyErr_SetString(PyExc_ValueError, why);" in j
        assert '"invalid composer spec"' in j

    def test_the_cli_passes_and_prints_it(self, out: "dict[str, str]") -> None:
        c = out["cli"]
        assert "const char *why = NULL;" in c
        assert (
            "c = wfm_compose_create_why(&seg, 1, repeat, continuous, &why);"
            in c
        )
        assert 'why ? why : "failed to build composer"' in c

    def test_no_face_calls_the_plain_create(
        self, out: "dict[str, str]"
    ) -> None:
        allc = "\n".join(out.values())
        assert "wfm_compose_create(" not in allc

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
        assert "wfm_compose_create_why(segs, n, repeat, continuous, why);" in j
        assert "frame_parse(_s, why)" in j


class TestWithoutTheSwitch:
    def test_output_is_unchanged(self) -> None:
        """No key and ``false`` render the same text, and it calls the plain
        four-argument create with the fixed message."""
        plain = _render(_mod_cfg())
        assert plain == _render(_mod_cfg(create_why=False))
        assert "wfm_compose_create_why" not in "\n".join(plain.values())
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
            in (plain["cli"])
        )
        assert "const char **why" not in plain["json"]
        assert "why" not in plain["cli"]


class TestTheKey:
    def test_it_survives_a_save_as_a_bool(self, tmp_path: Path) -> None:
        C.save(tmp_path, _mod_cfg(create_why=True))
        text = (tmp_path / "just-makeit.toml").read_text(encoding="utf-8")
        assert "create_why = true" in text, text
        assert C.load(tmp_path)["module"][MOD]["create_why"] is True

    def test_a_function_name_is_refused_at_load(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """gh-1722's check: a name is truthy, and would render a call
        whose trailing argument the named function may not take."""
        C.save(tmp_path, _mod_cfg(create_why="wfm_compose_create_why"))
        with pytest.raises(SystemExit):
            C.load(tmp_path)
        err = capsys.readouterr().err
        assert (
            f"[module.{MOD}] create_why = 'wfm_compose_create_why' must be "
            "true or false"
        ) in err, err


# -- end to end --------------------------------------------------------------

#: gh-1711's host, with a reason-naming create beside the plain one (doppler's
#: shape): a negative gain names itself, a gain above 1000 refuses with no
#: sentence, and the plain create is a thin wrapper over the reason-naming
#: one, so both refuse the same configurations.
_H = PLAYLIST_H.replace(
    "size_t playlist_execute",
    "playlist_state_t *playlist_create_why (const track_t *tracks, size_t n,\n"
    "                                       int repeat, int continuous,\n"
    "                                       const char **why);\n"
    "size_t playlist_execute",
)
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
    "\nplaylist_state_t *\n"
    "playlist_create_why (const track_t *tracks, size_t n, int repeat,\n"
    "                     int continuous, const char **why)\n{\n"
    "  for (size_t i = 0; i < n; i++)\n"
    "    for (size_t k = 0; k < tracks[i].n_sources; k++)\n"
    "      {\n"
    "        if (tracks[i].sources[k].gain < 0.0)\n"
    "          {\n"
    '            *why = "gain must be non-negative";\n'
    "            return NULL;\n"
    "          }\n"
    "        if (tracks[i].sources[k].gain > 1000.0)\n"
    "          return NULL;\n"
    "      }\n"
    "  return playlist_build (tracks, n, repeat, continuous);\n"
    "}\n\n" + _CREATE + "  const char *why = NULL;\n"
    "  return playlist_create_why (tracks, n, repeat, continuous, &why);\n"
    "}\n"
)
assert _C != PLAYLIST_C and _H != PLAYLIST_H
_TOML = COMPOSER_TOML.replace(
    'backing = "playlist"\n', 'backing = "playlist"\ncreate_why = true\n'
)
assert _TOML != COMPOSER_TOML

DRIVE = """\
import json
import os
import sys
import tempfile

sys.path.insert(0, "src")

from studio.playlist.playlist import Clip, Mix, Track

WHY = os.environ["JM_EXPECT_WHY"] == "1"
REASON = "gain must be non-negative" if WHY else "playlist_create failed"


def refused(fn):
    try:
        fn()
    except ValueError as e:
        return str(e)
    raise AssertionError("expected a ValueError")


# The constructor: the host's sentence, or the fixed text with none.
assert refused(lambda: Mix(Track.sum(Clip(gain=-1.0), dur=1))) == REASON
assert refused(lambda: Mix(Track.sum(Clip(gain=2000.0), dur=1))) == (
    "playlist_create failed"
)
# An accepted one still builds.
m = Mix(Track.sum(Clip(gain=1.0), dur=1))

# The generic record, from text and from a file.
doc = json.loads(m.to_json())
doc["segments"][0]["sources"][0]["gain"] = -1.0
bad = json.dumps(doc)
rec = REASON if WHY else "invalid composer spec"
assert refused(lambda: Mix.from_json(bad)) == rec
fd, path = tempfile.mkstemp(suffix=".json")
with os.fdopen(fd, "w") as fh:
    fh.write(bad)
try:
    assert refused(lambda: Mix.from_file(path)) == rec
finally:
    os.unlink(path)
doc["segments"][0]["sources"][0]["gain"] = 2000.0
assert refused(lambda: Mix.from_json(json.dumps(doc))) == (
    "invalid composer spec"
)
m.close()
print("create_why: PASSED")
"""


def _drive(project: Path, why: bool) -> str:
    import os

    r = subprocess.run(
        [sys.executable, "-c", DRIVE],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=600,
        env={
            **os.environ,
            "PYTHONPATH": str(project / "src"),
            "JM_EXPECT_WHY": "1" if why else "0",
        },
    )
    return _ok(r)


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    return build_project(
        tmp_path_factory.mktemp("g1755") / "studio", _H, _C, _TOML
    )


@pytest.fixture(scope="module")
def plain_project(tmp_path_factory) -> Path:
    """The same host WITHOUT the key: the fixed message on every face."""
    return build_project(
        tmp_path_factory.mktemp("g1755plain") / "studio", _H, _C
    )


def test_every_python_face_raises_the_hosts_sentence(project: Path) -> None:
    assert "create_why: PASSED" in _drive(project, why=True)


def test_without_the_key_every_face_keeps_the_fixed_message(
    plain_project: Path,
) -> None:
    assert "create_why: PASSED" in _drive(plain_project, why=False)


def test_the_cli_prints_the_hosts_sentence(project: Path) -> None:
    r = _run([str(_cli(project)), "--gain", "-1"], project)
    assert r.returncode == 1, r.stderr
    assert "gain must be non-negative" in r.stderr, r.stderr
    r = _run([str(_cli(project)), "--gain", "2000"], project)
    assert r.returncode == 1, r.stderr
    assert "failed to build composer" in r.stderr, r.stderr


def test_without_the_key_the_cli_keeps_the_fixed_message(
    plain_project: Path,
) -> None:
    r = _run([str(_cli(plain_project)), "--gain", "-1"], plain_project)
    assert r.returncode == 1, r.stderr
    assert "failed to build composer" in r.stderr, r.stderr
