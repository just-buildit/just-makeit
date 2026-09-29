"""A composer bit_pattern field reads a str through the project (gh-1709).

`coerce = "bit_pattern"` lets a `bytes` source field take `bytes`, a sequence
of ints or a `str`, and the `str` went through a grammar of jm's own: `0`/`1`
digits or `0x` hex. A project with a text form for a bit pattern then had two
grammars, and they disagreed in both directions, measured in doppler:

* jm refused what the project accepts: `Segment(payload="pn:7:3")` raised
  `ValueError: bit string must be 0/1 or '0x..' hex` while the project's CLI
  and scene file took the same text;
* jm accepted what the project refuses: `""` and `"0x"` became an empty
  pattern, where the project's reader says a hex prefix with no digits is a
  typo.

`coerce_str_fn = "<fn>"` names the project's reader, `size_t fn(const char
*text, uint8_t *out, size_t max_out, const char **why)`: out NULL sizes, a
second call fills, 0 is a refusal and `*why` its reason. jm routes a `str`
through it on every face that takes text for the field -- the constructor,
the property setter, a segment's single-source kwargs (which construct the
source) and the c-face CLI's flag -- and keeps `bytes` and int sequences as
they were. The key is per field, so a sibling field keeps jm's grammar.

The compiled test is the proof: a real project, a small host grammar in its
own C, and the Python and CLI faces driven against it.
"""

from __future__ import annotations

import contextlib
import copy
import io
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _composer, _config, _keys  # noqa: E402
from test_composer_codegen import _cfg  # noqa: E402

HOST = "wfm_bits_parse"


def _with_bits(**changes) -> dict:
    """The shared composer cfg with its `bits` field edited."""
    cfg = copy.deepcopy(_cfg())
    for f in cfg["module"]["wfm_compose"]["source"]["fields"]:
        if f["name"] == "bits":
            f.update(changes)
    return cfg


def _hosted() -> dict:
    return _with_bits(coerce="bit_pattern", coerce_str_fn=HOST)


class TestTheKeyIsPartOfTheManifest:
    def test_recognised_on_a_composer_field(self) -> None:
        assert "coerce_str_fn" in _keys.COMPOSER_FIELD_KEYS

    def test_written_back_by_the_manifest_writer(self) -> None:
        """A key the writer does not name is dropped on the next mutating
        command -- gh-1236's class."""
        f = {
            "name": "bits",
            "type": "uint8_t*",
            "bytes": True,
            "coerce": "bit_pattern",
            "coerce_str_fn": HOST,
        }
        assert f'coerce_str_fn = "{HOST}"' in _config._inline_field(f)

    def test_refused_on_a_field_that_does_not_coerce(self) -> None:
        """Read by nobody would be a silent no-op, so it is refused."""
        cfg = _with_bits(coerce_str_fn=HOST)
        with pytest.raises(ValueError, match='coerce = "bit_pattern"'):
            _composer.render_source_type(cfg, "wfm_compose")

    def test_refused_on_a_segment_field(self) -> None:
        """A segment field shares the vocabulary but has no bytes attach, so
        the key there would be read by nobody."""
        cfg = copy.deepcopy(_cfg())
        seg = cfg["module"]["wfm_compose"]["segment"]
        seg.setdefault("fields", []).append(
            {"name": "tag", "type": "double", "coerce_str_fn": HOST}
        )
        with pytest.raises(ValueError, match="segment field 'tag'"):
            _composer.render_bridge_h(cfg, "wfm_compose")


class TestTheGeneratedBinding:
    def test_constructor_and_setter_both_route_through_it(self) -> None:
        s = _composer.render_source_type(_hosted(), "wfm_compose")
        assert (
            "if (!_coerce_bits(&self->src.bits, &self->src.n_bits, bits))" in s
        )
        assert (
            "return _coerce_bits(&self->src.bits, &self->src.n_bits, value)"
            in s
        )
        assert f"size_t nb = {HOST}(s, NULL, 0, &why);" in s
        assert f"nb = {HOST}(s, buf, nb, &why);" in s
        assert "PyErr_SetString(PyExc_ValueError,\n" in s

    def test_the_prototype_is_a_published_seam(self) -> None:
        h = _composer.render_bridge_h(_hosted(), "wfm_compose")
        assert (
            f"size_t {HOST}(const char *, uint8_t *, size_t, const char **);"
            in h
        )
        assert HOST in _composer.seam_fns(_hosted(), "wfm_compose")

    def test_undeclared_renders_as_before(self) -> None:
        cfg = _with_bits(coerce="bit_pattern")
        s = _composer.render_source_type(cfg, "wfm_compose")
        assert "_coerce_bits" not in s
        assert "bit string must be 0/1 or '0x..' hex" in s
        assert _composer.render_bridge_h(cfg, "wfm_compose") == ""

    def test_the_cli_flag_reads_the_same_grammar(self) -> None:
        cfg = _hosted()
        cfg["module"]["wfm_compose"]["cli"] = {"enabled": True}
        c = _composer.render_cli(cfg, "wfm_compose")
        assert f"size_t _k = {HOST}(bits, NULL, 0, &_why);" in c
        assert '#include "wfm_compose/wfm_compose_bridge.h"' in c

    def test_the_cli_fills_the_include_slot_it_borrows(self) -> None:
        """The CLI pastes `jm app`'s sample-type block, which spells its
        include through the layout slot. Left unfilled, `apply` refused to
        write the file at all, so no composer CLI could be generated in a
        prefixed project -- found building this test's fixture."""
        cfg = _hosted()
        cfg["project"]["schema"] = "8"
        cfg["module"]["wfm_compose"]["cli"] = {"enabled": True}
        c = _composer.render_cli(cfg, "wfm_compose")
        assert "<<" not in c
        assert '#include "doppler/clib_common.h"' in c


# -- the compiled proof ------------------------------------------------------


def _no_toolchain() -> str | None:
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    return None


_SKIP = _no_toolchain()

#: The backing: a source with two pattern fields and a composer that emits,
#: per sample, how many payload bits its first source carries -- so the CLI's
#: output shows which bits it was handed.
_CORE_H = """\
#ifndef DECK_CORE_H
#define DECK_CORE_H

#include <complex.h>
#include <stddef.h>
#include <stdint.h>

typedef struct
{
  uint8_t *payload;
  size_t   n_payload;
  uint8_t *raw;
  size_t   n_raw;
} card_t;

typedef struct
{
  card_t *sources;
  size_t  n_sources;
  size_t  dur;
  double  fs;
} hand_t;

typedef struct deck_state deck_state_t;

deck_state_t *deck_create (const hand_t *hands, size_t n, int repeat,
                           int continuous);
size_t deck_execute (deck_state_t *state, float _Complex *out, size_t max);
const hand_t *deck_segments (const deck_state_t *state, size_t *n,
                             int *repeat, int *continuous);
deck_state_t *deck_from_file (const char *path);
void          deck_destroy (deck_state_t *state);

#endif
"""

_CORE_C = """\
#include "p/deck/deck_core.h"
#include <stdlib.h>

struct deck_state
{
  hand_t hand;
  size_t pos;
};

deck_state_t *
deck_create (const hand_t *hands, size_t n, int repeat, int continuous)
{
  (void)repeat;
  (void)continuous;
  if (!hands || n == 0)
    return NULL;
  deck_state_t *st = calloc (1, sizeof *st);
  if (!st)
    return NULL;
  st->hand           = hands[0];
  st->hand.sources   = NULL;
  st->hand.n_sources = 0;
  /* Keep only the first source's payload length: that is all execute()
   * reports, and it needs no deep copy of the buffers. */
  st->pos = hands[0].n_sources ? hands[0].sources[0].n_payload : 0;
  return st;
}

size_t
deck_execute (deck_state_t *state, float _Complex *out, size_t max)
{
  size_t n = max < state->hand.dur ? max : state->hand.dur;
  for (size_t i = 0; i < n; i++)
    out[i] = (float _Complex)state->pos;
  state->hand.dur -= n;
  return n;
}

const hand_t *
deck_segments (const deck_state_t *state, size_t *n, int *repeat,
               int *continuous)
{
  *n          = 0;
  *repeat     = 0;
  *continuous = 0;
  return &state->hand;
}

deck_state_t *
deck_from_file (const char *path)
{
  (void)path;
  return NULL;
}

void
deck_destroy (deck_state_t *state)
{
  free (state);
}
"""

#: The host grammar -- deliberately NOT jm's: `rep:B:N` is N copies of bit B,
#: and both "" and "0x" are refused with a reason.
_BITS_C = """\
#include "p/deck/deck_bridge.h"
#include <stdlib.h>
#include <string.h>

static int
hexval (char c)
{
  if (c >= '0' && c <= '9')
    return c - '0';
  if (c >= 'a' && c <= 'f')
    return c - 'a' + 10;
  if (c >= 'A' && c <= 'F')
    return c - 'A' + 10;
  return -1;
}

static size_t
refuse (const char **why, const char *reason)
{
  if (why)
    *why = reason;
  return 0;
}

size_t
card_bits (const char *text, uint8_t *out, size_t max_out, const char **why)
{
  size_t n = 0;
  if (!*text)
    return refuse (why, "host: the pattern is empty");
  if (text[0] == '0' && (text[1] == 'x' || text[1] == 'X'))
    {
      const char *d = text + 2;
      if (!*d)
        return refuse (why, "host: 0x with no digits");
      for (const char *c = d; *c; c++)
        if (hexval (*c) < 0)
          return refuse (why, "host: not a hex digit");
      n = 4 * strlen (d);
      if (out)
        {
          if (n > max_out)
            return refuse (why, "host: the output is too small");
          for (size_t i = 0; d[i]; i++)
            for (int b = 0; b < 4; b++)
              out[4 * i + b] = (uint8_t)((hexval (d[i]) >> (3 - b)) & 1);
        }
      return n;
    }
  if (!strncmp (text, "rep:", 4))
    {
      char *end = NULL;
      if ((text[4] != '0' && text[4] != '1') || text[5] != ':')
        return refuse (why, "host: rep:B:N wants a bit");
      n = (size_t)strtoul (text + 6, &end, 10);
      if (!n || *end)
        return refuse (why, "host: rep:B:N wants a count");
      if (out)
        {
          if (n > max_out)
            return refuse (why, "host: the output is too small");
          memset (out, text[4] - '0', n);
        }
      return n;
    }
  for (const char *c = text; *c; c++)
    if (*c != '0' && *c != '1')
      return refuse (why, "host: not a pattern");
  n = strlen (text);
  if (out)
    {
      if (n > max_out)
        return refuse (why, "host: the output is too small");
      for (size_t i = 0; i < n; i++)
        out[i] = (uint8_t)(text[i] - '0');
    }
  return n;
}
"""

_BACKING_CMAKE = """\
add_library(backing_core OBJECT deck_core.c deck_bits.c)
target_include_directories(backing_core PUBLIC ${CMAKE_SOURCE_DIR}/native/inc)
"""

#: `payload` names the host reader; `raw` coerces with jm's own grammar, so
#: the key is shown to be per field rather than per composer.
_MANIFEST = """
[module.deck]
kind = "composer"
backing = "deck"
extra_link_libs = ["backing_core"]

[module.deck.source]
struct = "card_t"
type_name = "Card"

[[module.deck.source.fields]]
name = "payload"
type = "uint8_t*"
bytes = true
coerce = "bit_pattern"
coerce_str_fn = "card_bits"

[[module.deck.source.fields]]
name = "raw"
type = "uint8_t*"
bytes = true
coerce = "bit_pattern"

[module.deck.segment]
type_name = "Hand"
struct = "hand_t"
sources = "multi"

[[module.deck.segment.fields]]
name = "dur"
type = "size_t"
default = "4"

[module.deck.oo]
composer_type_name = "Deck"

[module.deck.cli]
enabled = true
# The default name is the module's, which the extension target already
# holds.
name = "deckcli"
"""

#: Driven in a subprocess against the built extension. Each line is one
#: claim of the issue; a failure names it.
_DEMO = """\
import sys
sys.path.insert(0, "src")
import numpy as np
from p.deck.deck import Card, Hand


def refused(fn, want):
    try:
        fn()
    except ValueError as exc:
        assert str(exc) == want, (str(exc), want)
        return
    raise AssertionError(f"accepted; wanted ValueError({want!r})")


# The host's grammar reaches every face: constructor, setter, and a segment's
# single-source kwargs (which construct the source).
assert Card(payload="rep:1:5").payload == b"\\x01" * 5
assert Card(payload="0xA").payload == bytes([1, 0, 1, 0])
assert Card(payload="0110").payload == bytes([0, 1, 1, 0])
c = Card()
c.payload = "rep:0:3"
assert c.payload == b"\\x00" * 3
assert Hand(payload="rep:1:2").sources[0].payload == b"\\x01\\x01"

# ...and so do its refusals, with its own reason.
refused(lambda: Card(payload=""), "host: the pattern is empty")
refused(lambda: Card(payload="0x"), "host: 0x with no digits")
refused(lambda: Hand(payload="0x"), "host: 0x with no digits")


def assign_0x():
    c.payload = "0x"


refused(assign_0x, "host: 0x with no digits")
assert c.payload == b"\\x00" * 3, "a refused str must not clear the field"

# bytes and int sequences are taken exactly as before.
assert Card(payload=b"\\x01\\x00").payload == b"\\x01\\x00"
assert Card(payload=[1, 0, 7]).payload == b"\\x01\\x00\\x01"
assert Card(payload=np.array([0, 1], dtype=np.uint8)).payload == b"\\x00\\x01"
assert Card(payload=None).payload is None

# The sibling field did not name the key: jm's grammar, unchanged.
assert Card(raw="0101").raw == bytes([0, 1, 0, 1])
refused(lambda: Card(raw="rep:1:5"), "bit string must be 0/1 or '0x..' hex")
print("DEMO OK")
"""


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _run(args, cwd: Path, ok: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=900
    )
    if ok:
        assert r.returncode == 0, (
            f"{args}\nstdout:\n{r.stdout[-4000:]}\nstderr:\n{r.stderr[-4000:]}"
        )
    return r


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """A real composer project whose `payload` names a host reader."""
    if _SKIP:
        pytest.skip(_SKIP)
    from just_makeit import _incpath as INC
    from just_makeit._apply import run as jm_apply
    from just_makeit._new import run as jm_new

    root = tmp_path_factory.mktemp("gh1709") / "p"
    _silent(jm_new, "p", root)
    inc = INC.header_root(root) / "deck"
    inc.mkdir(parents=True, exist_ok=True)
    (inc / "deck_core.h").write_text(_CORE_H, encoding="utf-8")
    backing = root / "native" / "src" / "backing"
    backing.mkdir(parents=True, exist_ok=True)
    (backing / "deck_core.c").write_text(_CORE_C, encoding="utf-8")
    (backing / "deck_bits.c").write_text(_BITS_C, encoding="utf-8")
    (backing / "CMakeLists.txt").write_text(_BACKING_CMAKE, encoding="utf-8")

    manifest = root / "just-makeit.toml"
    text = manifest.read_text(encoding="utf-8")
    assert "[project]\n" in text
    text = text.replace("[project]\n", '[project]\nc_deps = ["backing"]\n', 1)
    manifest.write_text(text + _MANIFEST, encoding="utf-8")
    _silent(jm_apply, root)

    _run(
        [
            "cmake",
            "-B",
            "build",
            "-S",
            ".",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        root,
    )
    _run(["cmake", "--build", "build", "--parallel", "4"], root)
    return root


@pytest.mark.slow
@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
class TestAHostGrammarEndToEnd:
    def test_the_python_faces(self, project: Path) -> None:
        demo = project / "demo.py"
        demo.write_text(textwrap.dedent(_DEMO), encoding="utf-8")
        r = _run([sys.executable, str(demo)], project, ok=False)
        assert r.returncode == 0 and "DEMO OK" in r.stdout, r.stdout + r.stderr

    def _cli(self, project: Path) -> Path:
        exe = [
            p
            for p in (project / "build").rglob("deckcli*")
            if p.is_file() and p.name in ("deckcli", "deckcli.exe")
        ]
        assert exe, "the c-face CLI was not built"
        return exe[0]

    def test_the_cli_flag_accepts_the_host_grammar(self, project: Path):
        """Each sample is the payload's bit count: rep:1:5 is five bits,
        which jm's own reader would have refused."""
        r = _run(
            [
                str(self._cli(project)),
                "--payload",
                "rep:1:5",
                "--file-type",
                "csv",
            ],
            project,
        )
        first = r.stdout.splitlines()[0]
        assert float(first.split(",")[0]) == 5.0, r.stdout

    def test_the_cli_flag_refuses_with_the_host_reason(self, project: Path):
        r = _run(
            [str(self._cli(project)), "--payload", "0x"], project, ok=False
        )
        assert r.returncode == 2, (r.returncode, r.stderr)
        assert "bad --payload: host: 0x with no digits" in r.stderr
