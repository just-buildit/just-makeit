"""gh-1066: a manifest key must reach every artefact jm renders from it.

`no_ctor = true` narrows `create()`. It reached the sacred header and not the
binding compiled against it, so a project that used it did not build -- and
`jm status --check` reported `OK - up to date`, because it compares each file
against its own re-render and both were self-consistently wrong.

Two independent defects compounded, and each is checked separately below:

* **the manifest round-trip.** `_init`'s save (the standalone-object path)
  passed `controllable_names_` and stopped one line short of `opaque_fields_`
  and `no_ctor_names_`. Both were RENDERED from and then not persisted, so the
  generated C had them and the manifest did not -- and everything that
  rebuilds from the manifest, `jm apply`'s replay above all, rebuilt without.
  Its peer in `_object.py` passes all three.
* **the re-render.** `_glue.component_ctx` did not forward `no_ctor_names`
  (nor `opaque_fields`, `opaque_state`, `create_fn`, `init_post_parse_impl`)
  to `make_state_ctx`, so any command that re-renders glue without re-running
  the object scaffold dropped them. That is `jm error`, `jm warning`,
  `jm property`, `jm view` -- which is why a plain scaffold-edit-apply looked
  correct and the bug needed a second declaration to appear.

The assertion is derived from BOTH artefacts rather than matched against a
literal: the arity of the `create()` call in the binding must equal the arity
of the `create()` prototype in the header. A check written against "expect one
argument" would pass just as happily on a header that was also wrong.
"""

from __future__ import annotations

import contextlib
import io
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._error import run as error_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._warning import run as warning_run  # noqa: E402

_NO_TOOLCHAIN = shutil.which("cmake") is None or (
    shutil.which("cc") is None and shutil.which("gcc") is None
)


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _scaffold(root: Path) -> Path:
    proj = root / "demo"
    _quiet(new_run, "demo", proj)
    _quiet(
        object_run,
        proj,
        "thing",
        None,
        state_vars=[
            ("a", "size_t", "0"),
            ("derived", "bool", "false"),
        ],
        arg_type="size_t",
        return_type="size_t",
        no_ctor_names=frozenset({"derived"}),
    )
    return proj


def _proto_arity(proj: Path) -> int:
    text = (proj / "native" / "inc" / "thing" / "thing_core.h").read_text(
        encoding="utf-8"
    )
    m = re.search(r"^thing_state_t \*thing_create\((.*?)\);", text, re.M)
    assert m is not None, "no create() prototype in the header"
    inner = m.group(1).strip()
    return 0 if inner in ("", "void") else len(inner.split(","))


def _call_arity(proj: Path) -> int:
    text = (proj / "native" / "src" / "thing" / "thing_ext.c").read_text(
        encoding="utf-8"
    )
    m = re.search(r"handle = thing_create\((.*?)\);", text)
    assert m is not None, "the binding never calls create()"
    inner = m.group(1).strip()
    return 0 if not inner else len(inner.split(","))


class TestTheManifestKeepsIt:
    """Defect one: rendered from, then not written down."""

    def test_no_ctor_survives_the_round_trip(self, tmp_path):
        proj = _scaffold(tmp_path)
        cfg = C.load(proj)
        assert C.no_ctor_names(cfg, "thing") == frozenset({"derived"}), (
            "no_ctor was rendered from but never persisted, so every rebuild "
            "from the manifest loses it"
        )

    def test_opaque_fields_survive_the_round_trip(self, tmp_path):
        """The other key the same save line dropped.

        Checked because it was omitted by the same statement, not because it
        was reported -- the reported half is one member of the pair.
        """
        proj = tmp_path / "demo"
        _quiet(new_run, "demo", proj)
        _quiet(
            object_run,
            proj,
            "thing",
            None,
            state_vars=[("a", "size_t", "0")],
            opaque_fields=[("buf", "void *")],
            arg_type="size_t",
            return_type="size_t",
        )
        cfg = C.load(proj)
        assert [f[0] for f in C.opaque_fields(cfg, "thing")] == ["buf"]


class TestBothArtefactsAgree:
    """Defect two: a re-render that forgets what the scaffold knew."""

    def test_they_agree_on_a_fresh_scaffold(self, tmp_path):
        proj = _scaffold(tmp_path)
        assert _proto_arity(proj) == _call_arity(proj) == 1

    @pytest.mark.parametrize("declare", ["error", "warning"])
    def test_they_still_agree_after_a_glue_rerender(self, tmp_path, declare):
        """`jm error` / `jm warning` re-render the binding and nothing else.

        Both are covered: they are the same call into `_glue` and fixing the
        one that was noticed is how this class recurs.
        """
        proj = _scaffold(tmp_path)
        if declare == "error":
            _quiet(error_run, proj, "thing", "ValueError", "nope")
        else:
            _quiet(
                warning_run,
                proj,
                "thing",
                "derived",
                "best effort",
                category="RuntimeWarning",
            )
        assert _proto_arity(proj) == _call_arity(proj) == 1

    def test_they_still_agree_after_apply(self, tmp_path):
        """The replay path, which rebuilds the object from the manifest."""
        proj = _scaffold(tmp_path)
        _quiet(error_run, proj, "thing", "ValueError", "nope")
        _quiet(apply_run, proj)
        assert _proto_arity(proj) == _call_arity(proj) == 1

    def test_they_agree_when_the_binding_is_built_from_scratch(self, tmp_path):
        """Deleting the binding proves this is the generator, not staleness.

        The original diagnosis turned on exactly this: an `_ext.c` removed and
        re-materialised came back wrong, which ruled out a stale file.
        """
        proj = _scaffold(tmp_path)
        _quiet(error_run, proj, "thing", "ValueError", "nope")
        (proj / "native" / "src" / "thing" / "thing_ext.c").unlink()
        _quiet(apply_run, proj)
        assert _proto_arity(proj) == _call_arity(proj) == 1


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_the_project_compiles(tmp_path):
    """The symptom, end to end.

    Declared up front, `no_ctor` is consistent everywhere from the start --
    the sacred `_core.c` is written with the narrowed signature too -- so the
    project simply builds. It did not before: `jm error` re-rendered the
    binding back to the wide call and the compiler rejected it.
    """
    proj = _scaffold(tmp_path)
    _quiet(error_run, proj, "thing", "ValueError", "nope")
    _quiet(apply_run, proj)

    # The author's own file agrees too, without anyone editing it.
    core_c = (proj / "native" / "src" / "thing" / "thing_core.c").read_text(
        encoding="utf-8"
    )
    assert "thing_create(size_t a)" in core_c

    cfg = subprocess.run(
        [
            "cmake",
            "-S",
            str(proj),
            "-B",
            str(proj / "build"),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        capture_output=True,
        text=True,
    )
    assert cfg.returncode == 0, cfg.stdout + cfg.stderr
    built = subprocess.run(
        ["cmake", "--build", str(proj / "build"), "--parallel", "4"],
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stdout + built.stderr
