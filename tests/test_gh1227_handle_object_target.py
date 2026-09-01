"""gh-1227: a `kind = "handle"` module is a valid `object` target.

gh-794 exists precisely so a handle can hand its pointer to another module --
its own docstring calls the handle "the shape most likely to be on the giving
end" and "the only one that could not give one" before that change. gh-1224
then taught jm to *name* a producer, and named only components. So the one
kind built to be borrowed from was the one kind a consumer could not name, and
the refusal called a declared module an undeclared component.

The message half shipped in 0.73.0. This is the capability half, which was
blocked on gh-1229: there was no point resolving `object = "<handle>"` against
a `capsule` key that did not survive the next save.

**Every slot is read, not derived** -- the lesson gh-1234 charged for. A
handle declares `handle_type`, and the generated struct stores exactly
`{htype} *h` while the capsule lends `self->h`, so the published pointer's
type IS the declaration. Contrast the object side, where the C type is still
inferred from the component id because an object producer has no way to state
one (gh-1235). This resolver is the better-founded of the two.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

CAP = "proj.wfm.writer"

#: A handle module in the shape `_handle` actually generates from, trimmed to
#: the keys this feature reads. `create_args` is present because a handle with
#: none took a different path through `render_ext` (gh-1131).
_HANDLE = {
    "kind": "handle",
    "backing": "wfm_writer",
    "package": "wfm",
    "header": "wfm/wfm_writer.h",
    "type_name": "Writer",
    "create_fn": "wfm_writer_open",
    "close_fn": "wfm_writer_close",
    "capsule": CAP,
    "create_args": [{"name": "path", "type": "path"}],
}


def _project(
    tmp_path: Path, *, with_header: bool = True, **handle_extra: object
) -> Path:
    """A handle module that publishes a capsule, and an object to consume it.

    The backing header is **written**, because a handle's is the author's file
    -- jm never generates it -- and `_inject_includes_into_core_h` only injects
    an `extra` header that actually exists under `native/inc`. A fixture that
    omitted it would make the include assertion below unreachable while looking
    like it passed for a reason.
    """
    root = tmp_path / "proj"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("proj", root)
        object_run(root, "seg", None, state_vars=[("k", "size_t", "0")])
    if with_header:
        h = root / "native" / "inc" / "wfm" / "wfm_writer.h"
        h.parent.mkdir(parents=True, exist_ok=True)
        h.write_text(
            "#ifndef WFM_WRITER_H\n#define WFM_WRITER_H\n"
            "typedef struct wfm_writer wfm_writer_t;\n"
            "#endif\n",
            encoding="utf-8",
        )
    cfg = C.load(root)
    cfg.setdefault("module", {})["wfm_writer"] = {**_HANDLE, **handle_extra}
    C.save(root, cfg)
    return root


def _declare(root: Path, ref: str) -> dict:
    cfg = C.load(root)
    cfg["seg"]["init_params"] = [
        {"name": "w", "object": ref, "required": True}
    ]
    C.save(root, cfg)
    return C.load(root)


class TestItResolves:
    def test_all_four_slots_come_from_declarations(self, tmp_path: Path):
        cfg = C.load(_project(tmp_path))
        ctype, cap, header, cls = C.resolve_object_ref(cfg, "wfm_writer")
        # `handle_type` defaults to `<backing>_t`, and the struct stores
        # `{htype} *h` -- so this is the declared type, not a guess.
        assert ctype == "wfm_writer_t *"
        assert cap == CAP
        assert header == "wfm/wfm_writer.h"
        assert cls == "Writer"

    def test_an_explicit_handle_type_wins(self, tmp_path: Path):
        """The whole point of reading rather than deriving: a handle whose C
        type is not `<backing>_t` still lines up."""
        cfg = C.load(_project(tmp_path, handle_type="dp_writer"))
        assert C.resolve_object_ref(cfg, "wfm_writer")[0] == "dp_writer *"

    def test_capsule_name_falls_back_the_way_gh794_does(self, tmp_path: Path):
        """`capsule_name` is how a capsule module spells the same idea, and
        `handle_capsule` already accepts either. The resolver must not
        introduce a third opinion about which key wins."""
        cfg = C.load(_project(tmp_path, capsule=None, capsule_name="proj.alt"))
        cfg["module"]["wfm_writer"].pop("capsule", None)
        assert C.resolve_object_ref(cfg, "wfm_writer")[1] == "proj.alt"

    def test_the_dotted_form_may_name_the_generated_class(
        self, tmp_path: Path
    ):
        cfg = C.load(_project(tmp_path))
        assert C.resolve_object_ref(cfg, "wfm_writer.Writer")[3] == "Writer"

    def test_a_wrong_class_says_which_one_exists(self, tmp_path: Path):
        cfg = C.load(_project(tmp_path))
        with pytest.raises(ValueError, match="generates the class 'Writer'"):
            C.resolve_object_ref(cfg, "wfm_writer.Nope")

    def test_the_defaults_are_filled_when_nothing_is_declared(
        self, tmp_path: Path
    ):
        """A handle need declare neither `header` nor `package`; both have
        defaults its own writers already apply. The consumer must get the
        SAME answer, or the include it emits names a file that is not there."""
        cfg = C.load(_project(tmp_path, header=None, package=None))
        m = cfg["module"]["wfm_writer"]
        m.pop("header", None)
        m.pop("package", None)
        assert (
            C.resolve_object_ref(cfg, "wfm_writer")[2]
            == "wfm_writer/wfm_writer_core.h"
        )
        assert (
            C.object_ref_import(cfg, "wfm_writer")
            == "from .wfm_writer import Writer"
        )


class TestItRefuses:
    def test_a_handle_publishing_no_capsule(self, tmp_path: Path):
        cfg = C.load(_project(tmp_path, capsule=None))
        cfg["module"]["wfm_writer"].pop("capsule", None)
        with pytest.raises(ValueError) as exc:
            C.resolve_object_ref(cfg, "wfm_writer")
        msg = str(exc.value)
        assert "publishes no capsule" in msg
        # The fix is a MODULE-table key here, not `jm property` -- telling a
        # handle author to run the object command is the gh-1227 mistake over
        # again, one level down.
        assert "[module.wfm_writer]" in msg
        assert "jm property" not in msg

    def test_a_capsule_module_is_still_refused_and_says_so(
        self, tmp_path: Path
    ):
        """Only `handle` became a target. The message must no longer promise
        gh-1227 as pending, and must not describe a capsule module as a
        handle."""
        root = _project(tmp_path)
        cfg = C.load(root)
        cfg["module"]["cap"] = {"kind": "capsule", "backing": "cap"}
        with pytest.raises(ValueError) as exc:
            C.resolve_object_ref(cfg, "cap")
        msg = str(exc.value)
        assert "a capsule module is not a valid target" in msg
        assert 'kind = "handle" module' in msg
        assert "gh-1227" not in msg

    def test_a_plain_object_module_still_names_its_objects(
        self, tmp_path: Path
    ):
        root = _project(tmp_path)
        cfg = C.load(root)
        cfg["module"]["grp"] = {"objects": ["a", "b"]}
        with pytest.raises(ValueError, match="a module is a container"):
            C.resolve_object_ref(cfg, "grp")


class TestTheGeneratedCIsTheCapsulePath:
    """Same design as gh-1224: no consumer ever needs the producer's layout."""

    def _apply(self, tmp_path: Path, **kw: object) -> Path:
        root = _project(tmp_path, **kw)
        _declare(root, "wfm_writer")
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(root)
        return root

    def test_the_declared_pointer_type_reaches_the_sacred_core_h(
        self, tmp_path: Path
    ):
        """The regression gh-1234 is about, on the half that CAN be right:
        the prototype names the type the capsule actually carries."""
        root = self._apply(tmp_path)
        core_h = (root / "native" / "inc" / "seg" / "seg_core.h").read_text()
        assert "seg_state_t *seg_create(wfm_writer_t *w);" in core_h
        assert '#include "wfm/wfm_writer.h"' in core_h

    def test_a_header_that_is_not_there_is_not_included(self, tmp_path: Path):
        """The peer of the test above, from gh-790. A handle's backing header
        is the author's -- jm must name it when it is present and must not
        emit an include for a file that is not, or the component stops
        compiling because of a declaration it only referenced."""
        root = self._apply(tmp_path, with_header=False)
        core_h = (root / "native" / "inc" / "seg" / "seg_core.h").read_text()
        assert "seg_state_t *seg_create(wfm_writer_t *w);" in core_h
        assert "wfm/wfm_writer.h" not in core_h

    def test_it_unwraps_by_capsule_name_not_by_struct(self, tmp_path: Path):
        root = self._apply(tmp_path)
        ext = (root / "native" / "src" / "seg" / "seg_ext.c").read_text()
        assert f'PyCapsule_GetPointer(w_cap, "{CAP}")' in ext
        assert 'PyObject_GetAttrString(w_obj, "_capsule")' in ext
        assert "WriterObject" not in ext

    def test_it_keeps_the_strong_reference_to_the_owner(self, tmp_path: Path):
        """A handle owns a resource with a `close()`; the borrowed pointer
        dangles the moment the producer is collected, exactly as for an
        object."""
        root = self._apply(tmp_path)
        ext = (root / "native" / "src" / "seg" / "seg_ext.c").read_text()
        assert "PyObject *_w_owner;" in ext
        assert "Py_XSETREF(self->_w_owner, w_obj);" in ext


class TestTheStubNamesAndImportsTheClass:
    def test_the_import_targets_the_handle_package(self, tmp_path: Path):
        """A handle frequently lands INSIDE a sibling package (`package =
        "wfm"`), which is not its module id -- so a stub that assumed the id
        would import from a module that does not exist."""
        cfg = C.load(_project(tmp_path))
        assert C.object_ref_import(cfg, "wfm_writer") == (
            "from .wfm import Writer"
        )

    def test_the_annotation_is_the_class_not_object(self, tmp_path: Path):
        root = _project(tmp_path)
        _declare(root, "wfm_writer")
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(root)
        pyi = (root / "src" / "proj" / "seg.pyi").read_text()
        assert "from .wfm import Writer" in pyi
        assert "w: Writer" in pyi


def test_one_answer_to_where_a_handle_lands(tmp_path: Path) -> None:
    """`handle_package_resolved` / `handle_header_resolved` exist so the
    fallback is spelled once. Four call sites had it inline; a consumer
    disagreeing with the producer about either path is a broken include or a
    broken import, not a warning."""
    cfg = C.load(_project(tmp_path))
    cfg["module"]["wfm_writer"].pop("package")
    cfg["module"]["wfm_writer"].pop("header")
    assert C.handle_package_resolved(cfg, "wfm_writer") == "wfm_writer"
    assert (
        C.handle_header_resolved(cfg, "wfm_writer")
        == "wfm_writer/wfm_writer_core.h"
    )
