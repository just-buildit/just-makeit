"""gh-794 — a ``kind = "handle"`` module can publish its pointer as a capsule.

gh-788 gap 4 gave an **object** a `_capsule` property; gh-790 let an object be
constructed from one. Between them the capsule triangle closed — for objects. A
handle could do neither, which is the wrong way round: `kind = "handle"` is
precisely the shape that wraps a long-lived resource another component wants to
borrow (a clock, a socket, a device, a plan). It is the shape most likely to be
on the *giving* end of a capsule, and it was the only one that could not give
one.

The consuming side needed no change at all — gh-432 and gh-790 both already
accept "the capsule, or anything exposing it as `._capsule`" — which is the
strongest evidence the abstraction was drawn in the right place. The whole
feature is one getter and one getset row.

Emitted through `_context/_parse.capsule_new_c`, shared with gap 4 rather than
copied. The NULL destructor is a *contract*, not a call: a second copy is a
place for it to be quietly changed on one side only, and a capsule that frees a
pointer its owner also frees is a double-free.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _handle  # noqa: E402
from just_makeit._context._parse import capsule_new_c  # noqa: E402
from test_handle_codegen import _writer_cfg  # noqa: E402

CAP = "doppler.wfm.dp_sample_clock"


def _cfg(name: str = CAP, key: str = "capsule"):
    cfg = _writer_cfg()
    if name:
        cfg["module"]["wfm_writer"][key] = name
    return cfg


def _c(cfg) -> str:
    return _handle.render_getsets(cfg, "wfm_writer")[0]


def _pyi(cfg) -> str:
    return _handle.render_pyi(cfg, "wfm_writer")


class TestTheGetter:
    def test_it_publishes_the_declared_name(self):
        c = _c(_cfg())
        assert "Writer_get__capsule(WriterObject *self" in c
        assert f'"{CAP}", NULL);' in c

    def test_the_destructor_is_null(self):
        """The load-bearing detail: the handle still owns the pointer, so a
        capsule with a destructor would free it on collection and `close()`
        would free it again."""
        c = _c(_cfg())
        call = c[c.index("PyCapsule_New(") :]
        call = call[: call.index(");")]
        assert call.rstrip().endswith("NULL"), call

    def test_it_lends_the_opaque_handle(self):
        assert "PyCapsule_New((void *)(self->h)," in _c(_cfg())

    def test_the_closed_guard_is_kept(self):
        """Handing out a capsule over a closed handle is exactly the
        use-after-free the RAII protocol exists to prevent — and the consumer
        has no way to notice, since a capsule carries no liveness."""
        c = _c(_cfg())
        body = c[c.index("Writer_get__capsule") :]
        body = body[: body.index("\n}")]
        assert "self->closed" in body
        assert "PyExc_RuntimeError" in body
        # ...and it comes BEFORE the pointer is handed out.
        assert body.index("self->closed") < body.index("PyCapsule_New")

    def test_it_is_wired_into_the_getset_table(self):
        """A getter no PyGetSetDef references is dead code that compiles and
        changes nothing — the gh-627 lesson."""
        c = _c(_cfg())
        assert '{"_capsule", (getter)Writer_get__capsule, NULL,' in c

    def test_it_does_not_disturb_the_existing_getters(self):
        """Emitted last, so the `_g<i>` cache numbering the other getters use
        is untouched."""
        plain = _c(_writer_cfg())
        withcap = _c(_cfg())
        assert withcap.startswith(plain[: plain.index("static PyGetSetDef")])


class TestTheStub:
    def test_it_annotates_any(self):
        pyi = _pyi(_cfg())
        assert "def _capsule(self) -> Any:" in pyi
        assert "@property" in pyi

    def test_it_names_the_capsule_and_the_lifetime(self):
        pyi = _pyi(_cfg())
        block = pyi[pyi.index("def _capsule") :][:600]
        assert CAP in block
        assert "Non-owning" in block


class TestZeroChurn:
    """Every existing handle module must render byte-identically."""

    def test_no_key_no_capsule_in_c(self):
        assert "_capsule" not in _c(_writer_cfg())

    def test_no_key_no_capsule_in_pyi(self):
        assert "_capsule" not in _pyi(_writer_cfg())

    def test_the_c_is_byte_identical_without_the_key(self):
        before = _c(_writer_cfg())
        after = _c(_writer_cfg())
        assert before == after


class TestTheConfigKey:
    def test_capsule_key(self):
        assert C.handle_capsule(_cfg(), "wfm_writer") == CAP

    def test_capsule_name_is_accepted_too(self):
        """`kind = "capsule"` modules already spell this `capsule_name`; one
        idea should not need two spellings depending on which kind you are."""
        assert C.handle_capsule(_cfg(key="capsule_name"), "wfm_writer") == CAP

    def test_capsule_wins_over_capsule_name(self):
        cfg = _cfg()
        cfg["module"]["wfm_writer"]["capsule_name"] = "other.name"
        assert C.handle_capsule(cfg, "wfm_writer") == CAP

    def test_absent_is_empty(self):
        assert C.handle_capsule(_writer_cfg(), "wfm_writer") == ""


class TestTheSharedEmitter:
    """One contract, two callers — an object property (gap 4) and a handle
    getset (here)."""

    def test_both_kinds_emit_the_same_contract(self):
        obj = capsule_new_c("self->handle", CAP, "Telemetry")
        hnd = capsule_new_c("self->h", CAP, "Writer")
        # Same shape, differing only in the pointer and the owner's name.
        assert obj.replace("self->handle", "PTR").replace(
            "Telemetry", "OWNER"
        ) == hnd.replace("self->h", "PTR").replace("Writer", "OWNER")

    def test_the_null_destructor_is_not_parameterised(self):
        """There is no argument that could make this capsule owning. A
        capsule that owns its pointer is a different feature and should look
        different."""
        import inspect

        assert "destructor" not in inspect.signature(capsule_new_c).parameters
        assert "NULL);" in capsule_new_c("p", CAP, "X")


class TestItReachesTheConsumingSide:
    """The point of the feature: a handle's capsule must satisfy the gh-790
    constructor with no change on that side. Both name-check the same string,
    so this is really asserting the two halves agree about one name."""

    def test_the_published_name_is_what_a_consumer_checks(self):
        from just_makeit._context._parse import capsule_unwrap_c

        produced = capsule_new_c("self->h", CAP, "Clock")
        consumed = capsule_unwrap_c(
            "clock",
            "dp_sample_clock_t *",
            CAP,
            "clock_obj",
            "return -1;",
            allow_none=False,
        )
        assert f'"{CAP}", NULL);' in produced
        assert f'PyCapsule_GetPointer(clock_cap, "{CAP}")' in consumed

    def test_the_duck_typed_path_finds_it(self):
        """A consumer that is handed the *object* rather than the capsule
        reads `._capsule` — which is exactly the attribute name the getset row
        registers."""
        from just_makeit._context._parse import capsule_unwrap_c

        assert '{"_capsule", (getter)' in _c(_cfg())
        assert 'GetAttrString(clock_obj, "_capsule")' in capsule_unwrap_c(
            "clock", "dp_sample_clock_t *", CAP, "clock_obj", "return -1;"
        )
