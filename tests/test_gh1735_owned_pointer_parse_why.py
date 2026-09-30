"""gh-1735: an owned pointer's ``parse_fn`` can name why it refused.

gh-1711 gave a composer source an owned-pointer field whose text form goes
through the host's ``parse_fn``, declared ``T *parse_fn(const char *)``. A
refusal could only raise ``frame: <parse_fn> refused the text``, while
doppler's reader already names the cause through a ``const char **why``.

``parse_why = true`` is the owned pointer's form of gh-1706's
``from_json_why``: the bridge header declares the reason-naming signature,
every face that reads text passes the reason's address, and a refusal
reports the sentence -- raised by the constructor keyword, the setter and
the generic ``from_json`` / ``from_file`` through the one emitter
(``_diagnostics.reason_raise_c``), printed by the c-face CLI. A reader that
refuses without a sentence keeps the old message, a field without the key
renders byte-identically, and a non-bool value is refused at load with
gh-1722's check.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _composer  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from test_gh1711_composer_owned_pointer import (  # noqa: E402
    COMPOSER_TOML,
    FRAME,
    MOD,
    PLAYLIST_C,
    PLAYLIST_H,
    _cli,
    _ok,
    _run,
    _with,
    build_project,
)

WHY_FRAME = {**FRAME, "parse_why": True}


def _render(cfg: dict) -> "dict[str, str]":
    """Each emitter an owned pointer's text reaches, by face. The JSON
    record and the CLI are enabled so both are rendered at all."""
    mod = cfg["module"][MOD]
    mod["json"] = {"enabled": True}
    mod["cli"] = {"enabled": True, "name": "wfm_cli"}
    return {
        "ext": _composer.render_ext(cfg, MOD),
        "json": _composer.render_json_funcs(cfg, MOD),
        "cli": _composer.render_cli(cfg, MOD),
        "bridge": _composer.render_bridge_h(cfg, MOD),
    }


# -- the rendering -----------------------------------------------------------


class TestEveryFacePassesTheReason:
    @pytest.fixture
    def out(self) -> "dict[str, str]":
        return _render(_with(**WHY_FRAME))

    def test_the_bridge_header_declares_the_reason_naming_reader(
        self, out: "dict[str, str]"
    ) -> None:
        h = out["bridge"]
        assert (
            "wfm_frame_desc_t *frame_parse(const char *, const char **why);"
            in h
        ), h
        assert "frame_parse(const char *);" not in h

    def test_the_python_faces_pass_and_raise_it(
        self, out: "dict[str, str]"
    ) -> None:
        """The constructor keyword and the setter share `_attach_frame`, so
        one call site covers both; the e2e below drives each."""
        body = out["ext"].split("_attach_frame(", 1)[1]
        body = body.split("\n}\n", 1)[0]
        assert "const char *_why = NULL;" in body
        assert "_jm_new = frame_parse(_jm_s, &_why);" in body
        assert "PyErr_SetString(PyExc_ValueError, _why);" in body
        # ...and the generic text is still the raise when there is none.
        assert '"frame: frame_parse refused the text"' in body

    def test_the_record_threads_it_from_the_factories(
        self, out: "dict[str, str]"
    ) -> None:
        j = out["json"]
        assert "src->frame = _s ? frame_parse(_s, why) : NULL;" in j
        assert (
            "_json_parse_source(const cJSON *so, wfm_source_t *src, "
            "const char **why)" in j
        )
        assert "_json_parse_source(so, &srcs[k], why)" in j
        assert "_from_root(cJSON *root, const char **why)" in j
        # from_json and from_file each own the local and hand it on.
        assert j.count("const char *_why = NULL;") == 2
        assert j.count("_from_root(root, &_why);") == 2
        assert j.count("_wrap_state((PyTypeObject *)cls, st, _why);") == 2
        assert "PyErr_SetString(PyExc_ValueError, why);" in j

    def test_the_cli_passes_and_prints_it(self, out: "dict[str, str]") -> None:
        c = out["cli"]
        assert "src.frame = frame_parse(frame, &_why);" in c
        assert 'fprintf(stderr, "bad --frame %s: %s\\n", frame,' in c
        assert '_why ? _why : "frame_parse refused the text"' in c


class TestWithoutTheSwitch:
    def test_output_is_unchanged(self) -> None:
        """No key and ``false`` render the same text, and it is gh-1711's:
        a one-argument reader and no reason threaded anywhere."""
        plain = _render(_with(**FRAME))
        assert plain == _render(_with(**FRAME, parse_why=False))
        allc = "\n".join(plain.values())
        assert "frame_parse(const char *);" in plain["bridge"]
        assert "frame_parse(_jm_s);" in allc
        assert "frame_parse(_s) : NULL;" in allc
        assert "src.frame = frame_parse(frame);" in allc
        assert "const char **why" not in plain["json"]
        assert "_why" not in plain["json"]


class TestTheKey:
    def test_it_survives_a_save_as_a_bool(self, tmp_path: Path) -> None:
        C.save(tmp_path, _with(**WHY_FRAME))
        text = (tmp_path / "just-makeit.toml").read_text(encoding="utf-8")
        assert "parse_why = true" in text
        rows = C.load(tmp_path)["module"][MOD]["source"]["fields"]
        assert rows[-1] == WHY_FRAME

    def test_a_function_name_is_refused_at_load(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """gh-1722's check: a name is truthy, and would render a
        two-argument call to the one-argument reader it names."""
        C.save(tmp_path, _with(**{**FRAME, "parse_why": "frame_parse"}))
        with pytest.raises(SystemExit):
            C.load(tmp_path)
        err = capsys.readouterr().err
        assert (
            f"[[module.{MOD}.source.fields]] frame: "
            "parse_why = 'frame_parse' must be true or false"
        ) in err, err
        assert "Name the function in parse_fn." in err


# -- end to end --------------------------------------------------------------

#: gh-1711's host, with the reason-naming reader: an unreadable text refuses
#: with no sentence, a negative `n` names itself.
_H = PLAYLIST_H.replace(
    "desc_t *desc_parse (const char *text);",
    "desc_t *desc_parse (const char *text, const char **why);",
)
_C = PLAYLIST_C.replace(
    "desc_parse (const char *text)\n{",
    "desc_parse (const char *text, const char **why)\n{",
).replace(
    "  if (d.n < 0)\n    return NULL;",
    '  if (d.n < 0)\n    {\n      *why = "n must be non-negative";\n'
    "      return NULL;\n    }",
)
_TOML = COMPOSER_TOML.replace(
    'parse_fn = "desc_parse"\n', 'parse_fn = "desc_parse"\nparse_why = true\n'
)

DRIVE = """\
import gc
import json
import os
import sys
import tempfile

sys.path.insert(0, "src")

from studio.playlist.playlist import Clip, Mix, Track


def live():
    gc.collect()
    return Clip().live


def refused(fn):
    try:
        fn()
    except ValueError as e:
        return str(e)
    raise AssertionError("expected a ValueError")


REASON = "n must be non-negative"
c = Clip(gain=1.0, frame='{"n": 3}')

# The setter raises the host's sentence, and the source is untouched.
def setter(text):
    c.frame = text
assert refused(lambda: setter('{"n": -1}')) == REASON
assert c.frame == '{"n": 3}'
# A refusal with no sentence keeps the generic message.
assert refused(lambda: setter("nope")) == "frame: desc_parse refused the text"

# The constructor keyword is the same bind.
assert refused(lambda: Clip(frame="n=-2")) == REASON

# The generic record, from text and from a file.
m = Mix(Track.sum(c, dur=1))
doc = json.loads(m.to_json())
doc["segments"][0]["sources"][0]["frame"] = "n=-5"
bad = json.dumps(doc)
assert refused(lambda: Mix.from_json(bad)) == REASON
fd, path = tempfile.mkstemp(suffix=".json")
with os.fdopen(fd, "w") as fh:
    fh.write(bad)
try:
    assert refused(lambda: Mix.from_file(path)) == REASON
finally:
    os.unlink(path)
doc["segments"][0]["sources"][0]["frame"] = "nope"
assert refused(lambda: Mix.from_json(json.dumps(doc))) == (
    "invalid composer spec"
)

m.close()
del c, m
assert live() == 0, live()
print("parse_why: PASSED")
"""


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    return build_project(
        tmp_path_factory.mktemp("g1735") / "studio", _H, _C, _TOML
    )


def test_every_python_face_raises_the_hosts_sentence(project: Path) -> None:
    out = _ok(_run([sys.executable, "-c", DRIVE], project))
    assert "parse_why: PASSED" in out


def test_the_cli_prints_the_hosts_sentence(project: Path) -> None:
    r: subprocess.CompletedProcess = _run(
        [str(_cli(project)), "--frame", "n=-3"], project
    )
    assert r.returncode == 2, r.stderr
    assert "bad --frame n=-3: n must be non-negative" in r.stderr, r.stderr
    r = _run([str(_cli(project)), "--frame", "nope"], project)
    assert r.returncode == 2, r.stderr
    assert "bad --frame nope: desc_parse refused the text" in r.stderr


def test_the_host_reads_the_bridge_prototype(project: Path) -> None:
    """The generated header and the host's agree, or the build above would
    have failed: the bridge declares the two-argument reader."""
    h = next(project.rglob("playlist_bridge.h")).read_text(encoding="utf-8")
    assert "const char **why);" in h
