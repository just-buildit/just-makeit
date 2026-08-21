"""gh-1076: the manifest's create() against the one the header declares.

`jm status --check` compares the manifest against the files jm **owns**, and
every one of them agrees with the others *by construction* — the stub, the
binding and the aggregator all render from one `init_params` list. The only
file that can disagree is the hand-written one, and `_core.c` is sacred.

So a manifest declaring one `float[]` param could sit against a C constructor
taking `(size_t num_taps, const float *h)` indefinitely. doppler carried that,
and it was found by reviewing an unrelated jm change rather than by any gate.

**The gap was not uniform, which is what let it survive**, and the measurement
below is sharper than the issue's. Reordering a hand-written `create()`:

* on a **standalone** object → `_core.h` is manifest-owned, the whole-file
  diff catches it, `jm status` reports `STALE`;
* on a **module** object → the same file, the same edit, `OK — up to date`.

`TestTheGapWasLayoutDependent` asserts both halves are closed *and* keeps the
standalone half honest, because a fix that only taught the module layout would
leave the pair reporting the same divergence under two different names.

Why the header and not the `_core.c`: a definition that disagrees with its own
header will not compile, so checking the header puts the definition under the
compiler's gate for free — no C parsing, and no reading of a sacred file. And
jm *injects* that declaration, so this is jm verifying what it wrote rather
than learning anything new about the project.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _ctorsig  # noqa: E402
from just_makeit import _status  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _project(tmp_path: Path, module: str | None) -> Path:
    root = tmp_path / "d76"
    _quiet(new_run, "d76", root)
    if module:
        _quiet(module_run, root, module)
    _quiet(
        object_run,
        root,
        "hb",
        module=module,
        arg_type="float",
        return_type="float",
        init_params=[("h", "float[]", "")],
    )
    return root


def _header(root: Path) -> Path:
    return root / "native" / "inc" / "hb" / "hb_core.h"


def _reorder(root: Path) -> None:
    """Swap the two C parameters jm renders for one `float[]` init param.

    The edit is the real one: a hand-written constructor that took the length
    first. It changes no generated file, which is exactly why nothing saw it.
    """
    h = _header(root)
    text = h.read_text(encoding="utf-8")
    old = "hb_state_t *hb_create(const float *h, size_t h_len);"
    assert old in text, text
    h.write_text(
        text.replace(
            old, "hb_state_t *hb_create(size_t h_len, const float *h);", 1
        ),
        encoding="utf-8",
    )


def _status_text(root: Path, **kw) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _status.run(root, **kw)
    return buf.getvalue()


class TestTheDetector:
    @pytest.mark.parametrize(
        "module", [None, "dsp"], ids=["standalone", "module"]
    )
    def test_a_matching_header_is_not_a_finding(self, tmp_path, module):
        root = _project(tmp_path, module)
        assert _ctorsig.drift(root, C.load(root)) == []

    @pytest.mark.parametrize(
        "module", [None, "dsp"], ids=["standalone", "module"]
    )
    def test_a_reordered_header_is(self, tmp_path, module):
        root = _project(tmp_path, module)
        _reorder(root)
        (d,) = _ctorsig.drift(root, C.load(root))
        assert d.component == "hb"
        assert d.declared == "size_t h_len, const float *h"
        assert d.rendered == "const float *h, size_t h_len"

    def test_it_names_both_sides(self, tmp_path):
        """jm cannot say which one is stale, so it must show both.

        A finding that named only the manifest would send the author to
        change the side that was right half the time.
        """
        root = _project(tmp_path, "dsp")
        _reorder(root)
        (d,) = _ctorsig.drift(root, C.load(root))
        text = d.describe()
        assert "size_t h_len, const float *h" in text
        assert "const float *h, size_t h_len" in text
        assert "which side is stale" in text


class TestSpellingIsNotDrift:
    """Only a real difference may fire.

    A false positive here sends someone to edit a constructor that is
    correct, and the two spellings below are both ordinary C.
    """

    @pytest.mark.parametrize(
        "spelling",
        [
            "const float* h, size_t h_len",
            "const  float  *  h ,  size_t   h_len",
            "const float *h,size_t h_len",
        ],
    )
    def test_whitespace_and_star_placement(self, tmp_path, spelling):
        root = _project(tmp_path, "dsp")
        h = _header(root)
        h.write_text(
            h.read_text(encoding="utf-8").replace(
                "hb_create(const float *h, size_t h_len)",
                f"hb_create({spelling})",
                1,
            ),
            encoding="utf-8",
        )
        assert _ctorsig.drift(root, C.load(root)) == []

    def test_order_is_not_normalised(self):
        """The one thing that must NOT be normalised away."""
        a = _ctorsig._norm("const float *h, size_t h_len")
        b = _ctorsig._norm("size_t h_len, const float *h")
        assert a != b


class TestUnreadableIsNotClean:
    """ "I could not tell" must not be spelled like "nothing is wrong".

    The gh-1033 shape, one module over, and the reason `declared_params`
    returns `None` rather than `""`.
    """

    def test_a_missing_header_returns_none(self, tmp_path):
        assert _ctorsig.declared_params(tmp_path, "hb", "hb_create") is None

    def test_a_header_without_the_declaration_returns_none(self, tmp_path):
        root = _project(tmp_path, "dsp")
        h = _header(root)
        h.write_text(
            h.read_text(encoding="utf-8").replace(
                "hb_state_t *hb_create(const float *h, size_t h_len);", "", 1
            ),
            encoding="utf-8",
        )
        assert _ctorsig.declared_params(root, "hb", "hb_create") is None
        # ...and no finding is invented from the absence.
        assert _ctorsig.drift(root, C.load(root)) == []

    def test_none_is_distinct_from_a_void_parameter_list(self, tmp_path):
        """A `create(void)` is a real answer; an unreadable header is not."""
        root = _project(tmp_path, "dsp")
        h = _header(root)
        h.write_text(
            h.read_text(encoding="utf-8").replace(
                "hb_create(const float *h, size_t h_len)",
                "hb_create(void)",
                1,
            ),
            encoding="utf-8",
        )
        assert _ctorsig.declared_params(root, "hb", "hb_create") == "void"


class TestTheGapWasLayoutDependent:
    """The finding the issue did not have: one edit, two answers.

    Both halves must now fail. Asserting only the module half would let a fix
    stand that closed the new gap and broke the old one — and the standalone
    half is the *only* thing that made the divergence visible anywhere.
    """

    @pytest.mark.parametrize(
        "module", [None, "dsp"], ids=["standalone", "module"]
    )
    def test_status_check_fails(self, tmp_path, module):
        root = _project(tmp_path, module)
        _reorder(root)
        assert _status.run(root, check=True) > 0

    @pytest.mark.parametrize(
        "module", [None, "dsp"], ids=["standalone", "module"]
    )
    def test_an_untouched_project_still_passes(self, tmp_path, module):
        root = _project(tmp_path, module)
        assert _status.run(root, check=True) == 0

    def test_the_module_layout_used_to_say_OK(self, tmp_path):
        """Names the exact regression: `OK — up to date` over a broken tree.

        Kept separate from the exit code because the sentence is what a
        reader takes away, and gh-823 established that a finding which fails
        the gate while the summary still reads `OK` is only half reported.
        """
        root = _project(tmp_path, "dsp")
        _reorder(root)
        out = _status_text(root)
        assert "OK — up to date" not in out
        assert "CTOR" in out


class TestItReachesEveryFace:
    def test_the_listing_shows_both_signatures(self, tmp_path):
        root = _project(tmp_path, "dsp")
        _reorder(root)
        out = _status_text(root)
        assert "hb_create(size_t h_len, const float *h)" in out
        assert "hb_create(const float *h, size_t h_len)" in out

    def test_check_mode_still_names_the_component(self, tmp_path):
        """`--check` suppresses the per-file listing, but not this.

        A reader who sees only `1 ctor-drift (!)` has no way to learn which
        side to move, and the exit code has already told them something is
        wrong.
        """
        root = _project(tmp_path, "dsp")
        _reorder(root)
        out = _status_text(root, check=True)
        assert "ctor-drift" in out
        assert "hb_create" in out

    def test_json_carries_both_sides(self, tmp_path):
        """A CI consumer must not have to re-derive jm's type renderer to
        find out which side moved."""
        root = _project(tmp_path, "dsp")
        _reorder(root)
        payload = json.loads(_status_text(root, as_json=True))
        (d,) = payload["ctor_signature_drift"]
        assert d["component"] == "hb"
        assert d["header"] == "size_t h_len, const float *h"
        assert d["manifest"] == "const float *h, size_t h_len"

    def test_json_is_empty_on_a_clean_tree(self, tmp_path):
        root = _project(tmp_path, "dsp")
        payload = json.loads(_status_text(root, as_json=True))
        assert payload["ctor_signature_drift"] == []


class TestItIsNotSuppressible:
    """Same rule as the gh-442 DRIFT it sits beside.

    jm cannot know which side the author meant, and while the two disagree
    `jm regenerate` writes a `create()` that does not compile. There is no
    reading of `status_allow` under which that is intended.
    """

    def test_status_allow_does_not_clear_it(self, tmp_path):
        root = _project(tmp_path, "dsp")
        _reorder(root)
        manifest = root / "just-makeit.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                "[project]",
                '[project]\nstatus_allow = ["native/inc/hb/hb_core.h"]',
                1,
            ),
            encoding="utf-8",
        )
        assert _status.run(root, check=True) > 0
