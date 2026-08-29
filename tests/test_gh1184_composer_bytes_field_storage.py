"""gh-1184: a composer source's owned array names its own C storage.

A `[[module.X.source.fields]]` entry with `bytes = true` had its C storage
**hardcoded** to two flat members of the source struct, `src.<name>` and
`src.n_<name>`. No manifest key expressed any other location, so a source field
could never live inside a nested struct — and a project whose C already has a
type for "a run of bits however produced" (doppler's `wfm_seq_t`, carrying
LITERAL / PN / GOLD and their generator parameters) had to flatten that type
into the source instead: ~10 flat fields per sequence, mirrored in the
manifest, twice in the JSON codec and again in the schema.

`c_ptr` / `c_len` name the members. Both default to today's spelling, so a
manifest that does not use them renders byte-identically — which matters,
because a composer's glue is overwritten wholesale and has no sacred fragment
to absorb churn.

One helper, because the copies had already drifted
--------------------------------------------------
The pair was written out at **nine** sites. gh-560 fixed two of them — the
Segment deep-copy — and the note it left ("rather than a hardcoded
`bits`/`n_bits`") was true of those two only. Still live when this was
written, and each one measured here:

* the JSON serializer emitted `src->bits` / `src->n_bits` for a field of ANY
  name;
* so did the JSON deserializer, so a round trip wrote one field's array into
  another field's members;
* so did the c-face CLI;
* the JSON teardown freed `[k].bits` — one hardcoded member: the wrong one for
  a renamed field, **none** for a source with several, and a member that does
  not exist at all for a source with no bytes field.

That last shape did not compile, which is the same failure gh-560 was filed
for, in a different emitter. So the fix is `buffer_members` and every site
calling it, not nine careful edits.

`const`, decided rather than fallen out
---------------------------------------
A relocated member is commonly `const`-qualified: the type it belongs to is
written for the *borrowing* consumer while the source is the owner. The two
sites that own the buffer — `_attach_bytes`, which takes `uint8_t **`, and
`free` — need a non-const lvalue, so they cast. The cast rides on `c_ptr`
rather than on a third key: it is part of what "I have taken over the storage
location" means, and gating it that way is what keeps the default output
unchanged. `test_the_relocated_expressions_compile` is the arm that makes that
a measurement instead of a claim.
"""

from __future__ import annotations

import copy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _composer  # noqa: E402
from test_composer_codegen import _cfg  # noqa: E402

_CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")


def _with_bits(**changes) -> dict:
    """The shared composer cfg with the `bits` source field edited."""
    cfg = copy.deepcopy(_cfg())
    for f in cfg["module"]["wfm_compose"]["source"]["fields"]:
        if f["name"] == "bits":
            f.update(changes)
    return cfg


#: doppler's shape: the field keeps a manifest name of its own and points at
#: two members of a nested struct, the way `wfm_seq_t` carries them.
RELOCATED = dict(name="sync", c_ptr="sync.bits", c_len="sync.len")


def _all_c(cfg: dict) -> str:
    """Every emitter that touches a source buffer, concatenated.

    Asserting against one renderer is how eight of the nine sites stayed wrong
    while a test about the ninth passed.
    """
    return "\n".join(
        (
            _composer.render_source_type(cfg, "wfm_compose"),
            _composer.render_ext(cfg, "wfm_compose"),
            _composer.render_json_funcs(cfg, "wfm_compose"),
            _composer.render_cli(cfg, "wfm_compose"),
        )
    )


class TestTheDefaultIsUnchanged:
    """The constraint that makes this safe to ship: a manifest without the new
    keys must render exactly as it did. A composer's glue is overwritten
    wholesale, so any churn here is a diff on every downstream apply."""

    def test_the_flat_members_are_still_the_default(self) -> None:
        c = _all_c(_cfg())
        assert "_attach_bytes(&self->src.bits, &self->src.n_bits, bits)" in c
        assert "free(self->src.bits);" in c
        assert "src->bits && src->n_bits" in c

    def test_nothing_is_cast_when_nothing_moved(self) -> None:
        """The cast is what `c_ptr` buys. Emitting it unconditionally would
        silence a genuine type error on every project that never asked."""
        c = _all_c(_cfg())
        assert "(uint8_t **)&self->src" not in c
        assert "free((void *)self->src" not in c


class TestTheOverrideReachesEverySite:
    """Nine sites, enumerated. The point of the helper is that this list is
    the same list in the code."""

    @pytest.fixture
    def c(self) -> str:
        return _all_c(_with_bits(**RELOCATED))

    def test_the_constructor(self, c: str) -> None:
        assert (
            "_attach_bytes((uint8_t **)&self->src.sync.bits, "
            "&self->src.sync.len, sync)"
        ) in c

    def test_the_getter(self, c: str) -> None:
        assert "if (self->src.sync.bits && self->src.sync.len)" in c
        assert "(const char *)self->src.sync.bits" in c

    def test_the_setter(self, c: str) -> None:
        assert (
            "_attach_bytes((uint8_t **)&self->src.sync.bits, "
            "&self->src.sync.len, value)"
        ) in c

    def test_the_dealloc_free(self, c: str) -> None:
        assert "free((void *)self->src.sync.bits);" in c

    def test_the_segment_deep_copy(self, c: str) -> None:
        assert "if (syn->src.sync.bits && syn->src.sync.len)" in c
        assert "memcpy(copy, syn->src.sync.bits, syn->src.sync.len);" in c
        assert "syn->src.sync.len = 0;" in c

    def test_the_json_serializer(self, c: str) -> None:
        assert "if (src->sync.bits && src->sync.len)" in c
        assert "cJSON_CreateNumber(src->sync.bits[bi])" in c

    def test_the_json_deserializer(self, c: str) -> None:
        assert "src->sync.bits = _buf;" in c
        assert "src->sync.len = _nb;" in c

    def test_the_json_teardown(self, c: str) -> None:
        assert "free((void *)segs[j].sources[k].sync.bits);" in c

    def test_the_c_face_cli(self, c: str) -> None:
        assert "src.sync.bits = _b; src.sync.len = _k;" in c

    def test_the_stub_still_types_it_as_bytes(self) -> None:
        """The Python face is unchanged — `c_ptr` is about C storage, and a
        key that quietly altered the published API would be a different
        feature."""
        pyi = _composer.render_pyi(_with_bits(**RELOCATED), "wfm_compose")
        assert "sync" in pyi
        assert "bytes | None" in pyi


class TestTheHardcodedPairThatSurvivedGh560:
    """A field merely RENAMED — no `c_ptr` at all — was already broken in four
    emitters, and gh-560's fix note said otherwise because it was written for
    the two it did reach. Renaming is the cheaper repro, so it is the one that
    goes in the gate."""

    @pytest.fixture
    def c(self) -> str:
        return _all_c(_with_bits(name="sync"))

    def test_the_json_serializer_no_longer_says_bits(self, c: str) -> None:
        assert "src->bits" not in c
        assert "if (src->sync && src->n_sync)" in c

    def test_the_json_deserializer_no_longer_says_bits(self, c: str) -> None:
        assert "src->n_bits = _nb;" not in c
        assert "src->sync = _buf;" in c

    def test_the_cli_no_longer_says_bits(self, c: str) -> None:
        assert "src.bits = _b;" not in c
        assert "src.sync = _b; src.n_sync = _k;" in c

    def test_the_teardown_frees_the_field_that_exists(self, c: str) -> None:
        assert "sources[k].bits);" not in c
        assert "free(segs[j].sources[k].sync);" in c


class TestTheOtherTwoTeardownShapes:
    """The teardown freed exactly one hardcoded member, so both of these were
    wrong in the same line — one silently, one fatally."""

    def test_every_bytes_field_is_freed(self) -> None:
        cfg = copy.deepcopy(_cfg())
        cfg["module"]["wfm_compose"]["source"]["fields"].append(
            {"name": "sync", "type": "uint8_t*", "bytes": True}
        )
        c = _composer.render_json_funcs(cfg, "wfm_compose")
        assert "free(segs[j].sources[k].bits);" in c
        assert "free(segs[j].sources[k].sync);" in c

    def test_a_source_with_no_bytes_field_frees_nothing(self) -> None:
        """It emitted `free(segs[j].sources[k].bits)` against a struct with no
        such member — a source that could not compile, which is gh-560's own
        symptom in the emitter its fix did not reach."""
        cfg = copy.deepcopy(_cfg())
        src = cfg["module"]["wfm_compose"]["source"]
        src["fields"] = [f for f in src["fields"] if not f.get("bytes")]
        c = _composer.render_json_funcs(cfg, "wfm_compose")
        assert "sources[k]." not in c
        assert "free(segs[j].sources);" in c

    def test_no_whitespace_only_lines_are_left_behind(self) -> None:
        """Collapsing the teardown must collapse its line too: a
        whitespace-only line in generated C is reported as jm's drift by the
        project's own formatter."""
        cfg = copy.deepcopy(_cfg())
        src = cfg["module"]["wfm_compose"]["source"]
        src["fields"] = [f for f in src["fields"] if not f.get("bytes")]
        c = _composer.render_json_funcs(cfg, "wfm_compose")
        offenders = [
            ln for ln in c.splitlines() if ln != ln.rstrip() and not ln.strip()
        ]
        assert offenders == [], offenders


def _compiles(tmp_path: Path, tag: str, call: str, free_line: str) -> int:
    """Compile *call* and *free_line* against a `wfm_seq_t`-shaped member.

    `bits` is `const uint8_t *` because the type it belongs to is written for
    the borrowing consumer; the source owns the buffer, so both of these need
    a non-const lvalue. Returns the compiler's exit status.
    """
    probe = tmp_path / f"probe_{tag}.c"
    probe.write_text(
        "#include <stdint.h>\n"
        "#include <stddef.h>\n"
        "#include <stdlib.h>\n"
        "typedef struct { const uint8_t *bits; size_t len; } seq_t;\n"
        "typedef struct { seq_t sync; } src_t;\n"
        "typedef struct { src_t src; } obj_t;\n"
        "static int _attach_bytes(uint8_t **d, size_t *n, void *o)\n"
        "{ (void)d; (void)n; (void)o; return 1; }\n"
        "int use(obj_t *self, void *value)\n"
        "{\n"
        f"    int ok = {call};\n"
        f"    {free_line}\n"
        "    return ok;\n"
        "}\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [
            _CC,
            "-c",
            "-std=c11",
            "-Werror=incompatible-pointer-types",
            "-Werror=int-conversion",
            str(probe),
            "-o",
            str(tmp_path / f"probe_{tag}.o"),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    ).returncode


@pytest.mark.skipif(_CC is None, reason="no C compiler available")
def test_the_relocated_expressions_compile(tmp_path: Path) -> None:
    """The `const` decision, measured.

    The issue's evidence is that the generated file *does not compile*, so a
    render-text assertion is not enough on its own — it can be self-consistent
    and still wrong about C. This takes jm's own emitted expressions
    (extracted from the render, not retyped) and compiles them against a
    `wfm_seq_t`-shaped member whose pointer is `const uint8_t *`, with the two
    diagnostics that would fire if the casts were missing promoted to errors.
    """
    c = _all_c(_with_bits(**RELOCATED))
    attach = [
        ln.strip()
        for ln in c.splitlines()
        if "_attach_bytes((uint8_t **)&self->src.sync.bits" in ln
        and "value" in ln
    ]
    free_line = [
        ln.strip()
        for ln in c.splitlines()
        if ln.strip() == "free((void *)self->src.sync.bits);"
    ]
    assert attach and free_line, (
        "the emitted expressions this compiles were not found — the test "
        "would otherwise pass by never reaching the compiler"
    )
    # The attach is the head of a multi-line return; take just the call.
    call = attach[0][attach[0].index("_attach_bytes") :].rstrip()
    assert _compiles(tmp_path, "cast", call, free_line[0]) == 0


@pytest.mark.skipif(_CC is None, reason="no C compiler available")
def test_the_uncast_form_would_not_compile(tmp_path: Path) -> None:
    """The arming check for the arm above.

    A compile that passes proves nothing unless the same compile fails without
    the thing under test. This is the expression jm emitted before gh-1184,
    against the same struct and the same flags — a hard `-Werror` on
    `const uint8_t **` where `uint8_t **` is wanted.
    """
    out = _compiles(
        tmp_path,
        "uncast",
        "_attach_bytes(&self->src.sync.bits, &self->src.sync.len, value)",
        "free(self->src.sync.bits);",
    )
    assert out != 0, "the -Werror flags are not doing anything"
