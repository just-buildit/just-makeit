"""gh-588: an object's state struct can stay out of the public header.

jm always published `<comp>_state_t` as a *complete* type, so adopting the
object kind for a resource-ish component meant exporting every member as API.
doppler's reader struct holds a `FILE *`, a scratch buffer and a decoded-keyword
array — none of it public interface. The handle kind never had this problem,
which is the divergence #525 (finding 8) reported.

`opaque_state` forward-declares instead:

    typedef struct rdr_state rdr_state_t;          /* header */
    struct rdr_state { … };                        /* _core.c */

Mechanically this works because the generated binding only ever handles the
state through a pointer. The one thing that cannot is the `static inline
<comp>_step()` that lives in the header and dereferences it — hence the
`no_step` requirement, which jm rejects up front rather than leaving to an
incomplete-type error in code the user never wrote.

Note what was *already* possible and is not what this changes: a property
without `field = true` has always been accessor-backed
(`<comp>_get_<name>(self->handle)`), so properties never forced the struct open.
"""

import io
import contextlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402


def _q(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


@pytest.fixture()
def proj(tmp_path):
    d = tmp_path / "p"
    _q(new_run, "p", d, [], [])
    return d


def _mk(proj, name="rdr", **kw):
    opts = dict(no_state=True, no_step=True, opaque_state=True)
    opts.update(kw)
    _q(object_run, proj, name, None, **opts)
    return (
        (proj / "native" / "inc" / name / f"{name}_core.h").read_text(),
        (proj / "native" / "src" / name / f"{name}_core.c").read_text(),
    )


class TestOpaqueRender:
    def test_header_forward_declares_only(self, proj):
        h, _ = _mk(proj)
        assert "typedef struct rdr_state rdr_state_t;" in h
        # the complete form is gone — that is the whole point
        assert "typedef struct {" not in h

    def test_definition_moves_to_core_c(self, proj):
        _, c = _mk(proj)
        assert "struct rdr_state {" in c
        assert "/* <<IMPLEMENT: add fields >> */" in c

    def test_lifecycle_still_takes_the_typedef(self, proj):
        """Pointers only — which is why an incomplete type is fine."""
        h, _ = _mk(proj)
        assert "rdr_state_t *rdr_create(" in h
        assert "void rdr_destroy(rdr_state_t *state);" in h


class TestDefaultUnchanged:
    def test_without_the_flag_the_struct_is_public(self, proj):
        h, c = _mk(proj, opaque_state=False)
        assert "typedef struct {" in h
        assert "} rdr_state_t;" in h
        assert "struct rdr_state {" not in c


class TestValidation:
    """Incoherent combinations are jm diagnostics, not compiler errors."""

    def test_requires_no_step(self, proj, capsys):
        with pytest.raises(SystemExit):
            object_run(proj, "rdr", None, no_state=True, opaque_state=True)
        err = capsys.readouterr().err
        assert "--opaque-state requires --no-step" in err
        assert "static inline" in err  # says WHY

    def test_rejects_declared_state(self, proj, capsys):
        with pytest.raises(SystemExit):
            object_run(
                proj,
                "rdr",
                None,
                state_vars=[("k", "double", "0.0")],
                no_step=True,
                opaque_state=True,
            )
        assert "cannot be combined with --state" in capsys.readouterr().err


class TestManifestRoundTrip:
    def test_key_persists_and_survives_apply(self, proj):
        """`jm apply` regenerates the header; losing the key would silently
        republish the struct."""
        _mk(proj)
        assert C.is_opaque_state(C.load(proj), "rdr") is True
        _q(apply_run, proj)
        h = (proj / "native" / "inc" / "rdr" / "rdr_core.h").read_text()
        assert "typedef struct rdr_state rdr_state_t;" in h

    def test_key_survives_the_dump_serializer(self, proj):
        """_dump writes brand-new fragments and backs split-objects/upgrade;
        an unknown key is dropped there silently (the gh-580 lesson)."""
        _mk(proj)
        text = C._dump({"rdr": C.load(proj)["rdr"]})
        assert 'opaque_state = "true"' in text
        assert C.tomllib.loads(text)["rdr"]["opaque_state"] == "true"

    def test_absent_key_is_not_written(self, proj):
        """Opt-in: a normal object's manifest is untouched."""
        _mk(proj, opaque_state=False)
        assert "opaque_state" not in C._dump({"rdr": C.load(proj)["rdr"]})


class TestPropertiesStayAccessorBacked:
    def test_property_getter_never_touches_the_struct(self, proj):
        """The part that already worked, pinned so it keeps working: an
        accessor-backed property is what makes an opaque struct usable."""
        _mk(proj)
        _q(property_run, proj, "rdr", "nsamples", None, "size_t", False)
        ext = (proj / "native" / "src" / "rdr" / "rdr_ext.c").read_text()
        assert "rdr_get_nsamples(self->handle)" in ext
        assert "self->handle->" not in ext
