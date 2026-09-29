"""A composer field's doc is its struct member's Doxygen (gh-1703).

A ``source.fields`` / ``segment.fields`` row IS a member of the C struct the
composer wraps, and that member is documented in the header. Before this the
row's docstring came from the manifest ``doc`` alone, so each field had two
docs and nothing kept them equal -- doppler's 40 ``wfm_compose`` fields had
drifted (``freq``: "freq offset (Hz)" in the header, a different sentence in
the manifest).

Now the header is the SSOT and the manifest ``doc`` the fallback, on both
faces (the ``.pyi`` class docstring and the runtime getset doc), looked up in
the field's OWN struct only (gh-1300), and a disagreement is a ``status``
finding that fails ``--check``.

Every test builds a real project with ``jm new`` and ``jm apply`` and reads
the files apply wrote; the header lives where a composer's hand-written
backing does, in the project's ``native/inc``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from test_composer_apply import _project  # noqa: E402

from just_makeit import _config as C  # noqa: E402
from just_makeit import _incpath as INC  # noqa: E402
from just_makeit import _status  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402

_MOD = "wfm_compose"
_EXT = "native/src/wfm_compose/wfm_compose_ext.c"
_PYI = "src/proj/wfm/wfm_compose.pyi"

#: The backing header. `freq` carries a trailing doc, `bits` a leading block
#: (gh-1167's form), `type` none. The segment struct documents
#: `num_samples` and not `off_samples`.
_BACKING_H = """\
#ifndef WFM_COMPOSE_H
#define WFM_COMPOSE_H
#include <stddef.h>
#include <stdint.h>
#include "wfm/wfm_other.h"

typedef struct
{
  int type;
  double freq; /**< HEADERFREQ offset in Hz. */
  /** HEADERBITS the pattern, one bit per byte. */
  uint8_t *bits;
  size_t n_bits;
} wfm_source_t;

typedef struct
{
  wfm_source_t *sources;
  size_t n_sources;
  double fs;
  size_t num_samples; /**< HEADERNUM samples in the span. */
  size_t off_samples;
} wfm_segment_t;

#endif
"""

#: An included header whose unrelated struct documents members with the
#: same names as fields jm must NOT borrow them for (gh-1300).
_OTHER_H = """\
#ifndef WFM_OTHER_H
#define WFM_OTHER_H
#include <stddef.h>
typedef struct
{
  int type;            /**< STRANGERTYPE a ring's kind. */
  size_t off_samples;  /**< STRANGEROFF a ring's offset. */
} wfm_ring_t;
#endif
"""


def _field(cfg: dict, table: str, name: str) -> dict:
    fields = cfg["module"][_MOD][table]["fields"]
    return next(f for f in fields if f["name"] == name)


def _build(root: Path, docs: "dict[tuple[str, str], str]") -> None:
    """A composer project with the headers above and *docs* in the manifest,
    applied."""
    _project(root)
    cfg = C.load(root)
    for (table, name), doc in docs.items():
        _field(cfg, table, name)["doc"] = doc
    C.save(root, cfg)
    inc = INC.inc_dir(root) / "wfm"
    inc.mkdir(parents=True, exist_ok=True)
    (inc / "wfm_compose.h").write_text(_BACKING_H, encoding="utf-8")
    (inc / "wfm_other.h").write_text(_OTHER_H, encoding="utf-8")
    apply_run(root)


def _faces(root: Path) -> "tuple[str, str]":
    return (
        (root / _PYI).read_text(encoding="utf-8"),
        (root / _EXT).read_text(encoding="utf-8"),
    )


@pytest.fixture
def built(tmp_path: Path) -> Path:
    _build(
        tmp_path,
        {
            ("source", "freq"): "MANIFESTFREQ carrier frequency.",
            ("source", "type"): "MANIFESTTYPE the waveform kind.",
        },
    )
    return tmp_path


@pytest.mark.parametrize("face", ["pyi", "ext"])
def test_the_header_doc_wins_over_a_manifest_doc(built, face):
    text = dict(zip(("pyi", "ext"), _faces(built)))[face]
    assert "HEADERFREQ offset in Hz." in text
    assert "MANIFESTFREQ" not in text


@pytest.mark.parametrize("face", ["pyi", "ext"])
def test_a_leading_block_and_a_segment_member_are_read(built, face):
    """gh-1167's block form, and the SEGMENT table read from its own
    struct -- not only the source's."""
    text = dict(zip(("pyi", "ext"), _faces(built)))[face]
    assert "HEADERBITS the pattern" in text
    assert "HEADERNUM samples in the span." in text


@pytest.mark.parametrize("face", ["pyi", "ext"])
def test_the_manifest_doc_is_the_fallback(built, face):
    """`type` has no member doc in `wfm_source_t`, so its manifest `doc`
    renders -- and NOT the included ring struct's same-named member."""
    text = dict(zip(("pyi", "ext"), _faces(built)))[face]
    assert "MANIFESTTYPE the waveform kind." in text
    assert "STRANGERTYPE" not in text


@pytest.mark.parametrize("face", ["pyi", "ext"])
def test_another_structs_member_is_never_used(built, face):
    """`off_samples` is documented only on `wfm_ring_t.off_samples`, in a
    header the backing includes: gh-1300's stranger. It stays undocumented."""
    text = dict(zip(("pyi", "ext"), _faces(built)))[face]
    assert "STRANGEROFF" not in text


def test_status_reports_a_disagreement_and_check_fails(built, capsys):
    capsys.readouterr()
    n = _status.run(built, check=True)
    out = capsys.readouterr().out
    assert n >= 1, out
    where = "module.wfm_compose.source.fields.freq.doc"
    assert where in out, out
    assert "wfm_source_t.freq" in out, out
    assert "gh-1703" in out, out
    # The fallback is not a disagreement: `type` has no header doc.
    assert "fields.type.doc" not in out, out


def test_status_is_clean_when_the_docs_agree(tmp_path, capsys):
    """The same tree with the manifest restating the header (whitespace
    aside) and no other drift: no finding, `--check` passes."""
    _build(
        tmp_path,
        {
            ("source", "freq"): "HEADERFREQ  offset\n  in Hz.",
            ("source", "type"): "MANIFESTTYPE the waveform kind.",
        },
    )
    capsys.readouterr()
    n = _status.run(tmp_path, check=True)
    out = capsys.readouterr().out
    assert "fields.freq.doc" not in out, out
    assert n == 0, out
