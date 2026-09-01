"""gh-1235: a capsule producer can declare what its pointer IS.

gh-1224 argued that the capsule *name* must be read from the producer rather
than derived, because the producer owns that string. It then derived the C type
the capsule points at, from the component id -- `<comp>_state_t *`.

That holds for one producer and only one. A `type = "capsule"` property
publishes `expr or "self->handle"`, and `self->handle` IS the object's
`<comp>_state_t *`. Declare an `expr` reaching a member and the capsule carried
one pointer while the consumer's `create()` was generated taking another: a
silent type confusion across exactly the ABI boundary the capsule triangle
exists to protect. gh-1234 reported it; gh-1237 refused the case rather than
answering it wrongly, because the producer had no way to say what it was.

This is the way. The type was never unknowable -- only undeclared.

Why not `ctype`
---------------
`PROPERTY_KEYS` already has it, and on a property it is a **legacy synonym for
`type`** (`p.get("type") or p.get("ctype", "size_t")`, in four files). A
capsule property's `type` slot is already spent on the word `capsule`, so
reusing `ctype` would be one key answering two questions -- the trap this repo
has paid for four times (`feedback-flag-standing-in`).
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _script  # noqa: E402
from just_makeit._keys import PROPERTY_KEYS  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402
from just_makeit._split_objects import run as split_run  # noqa: E402

CAP = "dsp.nco.desc"
CTYPE = "const dp_nco_desc_t *"


def _project(tmp_path: Path, **prop_kw: object) -> Path:
    root = tmp_path / "dsp"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("dsp", root, ["nco"], [("cap", "size_t", "16")])
        property_run(
            root,
            "nco",
            "_capsule",
            None,
            "capsule",
            False,
            capsule=CAP,
            **prop_kw,
        )
    return root


class TestTheProducerDeclaresIt:
    def test_the_resolver_reads_the_declared_type(self, tmp_path: Path):
        cfg = C.load(_project(tmp_path, capsule_type=CTYPE))
        ctype, cap, _hdr, _cls = C.resolve_object_ref(cfg, "nco")
        assert ctype == CTYPE
        assert cap == CAP

    def test_it_wins_over_the_inferred_state_pointer(self, tmp_path: Path):
        """The whole point. Without it the consumer's `create()` took
        `nco_state_t *` while the capsule carried something else."""
        cfg = C.load(_project(tmp_path, capsule_type=CTYPE))
        assert C.resolve_object_ref(cfg, "nco")[0] != "nco_state_t *"

    def test_the_default_producer_is_unchanged(self, tmp_path: Path):
        """Undeclared still means `self->handle`, which IS the state pointer.
        Requiring the key would break every producer that shipped before it."""
        cfg = C.load(_project(tmp_path))
        assert C.resolve_object_ref(cfg, "nco")[0] == "nco_state_t *"

    def test_it_unblocks_the_expr_case_gh1234_refused(self, tmp_path: Path):
        """gh-1237 refuses an `expr`-publishing producer because jm cannot
        name the pointer. Declaring the type is what makes that case work --
        so the refusal must not survive the declaration."""
        root = _project(tmp_path, capsule_type=CTYPE, expr="&self->handle->d")
        cfg = C.load(root)
        assert C.resolve_object_ref(cfg, "nco")[0] == CTYPE

    def test_the_refusal_still_fires_without_it(self, tmp_path: Path):
        cfg = C.load(_project(tmp_path, expr="&self->handle->d"))
        with pytest.raises(ValueError) as exc:
            C.resolve_object_ref(cfg, "nco")
        msg = str(exc.value)
        assert "&self->handle->d" in msg
        # ...and it now names the key that fixes it, not just the workaround.
        assert "--capsule-type" in msg
        assert "gh-1235" in msg


class TestItSurvivesEveryFace:
    """Three faces lose a key independently, and each is invisible from the
    others -- gh-1242 was the dumper, this file found the replay."""

    def test_a_plain_save_keeps_it(self, tmp_path: Path):
        cfg = C.load(_project(tmp_path, capsule_type=CTYPE))
        C.save(tmp_path / "dsp", cfg)
        assert (
            C.properties(C.load(tmp_path / "dsp"), "nco")[0]["capsule_type"]
            == CTYPE
        )

    def test_split_objects_keeps_it(self, tmp_path: Path):
        root = _project(tmp_path, capsule_type=CTYPE)
        with contextlib.redirect_stdout(io.StringIO()):
            split_run(root)
        assert C.resolve_object_ref(C.load(root), "nco")[0] == CTYPE

    def test_jm_script_replays_it(self, tmp_path: Path):
        root = _project(tmp_path, capsule_type=CTYPE)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _script.run(root)
        out = buf.getvalue()
        assert f'--capsule-type "{CTYPE}"' in out

    def test_jm_script_replays_the_capsule_name_too(self, tmp_path: Path):
        """Measured while adding `--capsule-type`: `_property_flags` emitted
        NEITHER, so `jm script` reproduced a capsule property as a plain one --
        a producer that publishes nothing. gh-1242's defect, one face over."""
        root = _project(tmp_path)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _script.run(root)
        assert f"--capsule {CAP}" in buf.getvalue()


class TestTheKeyIsWellFormed:
    def test_it_is_a_registered_property_key(self):
        assert "capsule_type" in PROPERTY_KEYS

    def test_it_is_not_ctype(self):
        """`ctype` is a legacy synonym for `type` on a property, and a capsule
        property's `type` is already the word `capsule`. Borrowing it would be
        one key answering two questions -- four instances so far."""
        assert "ctype" in PROPERTY_KEYS
        p = {"name": "x", "type": "capsule", "ctype": "should_be_ignored"}
        assert (p.get("type") or p.get("ctype", "size_t")) == "capsule"

    def test_the_cli_refuses_it_without_a_capsule(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """On its own it describes a pointer nothing publishes."""
        from just_makeit._cli import main

        root = _project(tmp_path)
        monkeypatch.chdir(root)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "just-makeit",
                "property",
                "nco",
                "p",
                "--type",
                "double",
                "--capsule-type",
                "int *",
            ],
        )
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code != 0
        assert "needs --capsule" in err.getvalue()
