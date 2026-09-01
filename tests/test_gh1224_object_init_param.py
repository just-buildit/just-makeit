"""gh-1224 -- an init_param can name another generated class.

The issue was filed as "a composite type must be flattened or hand-written",
and that premise turned out to be **wrong**. Two generated objects could
already be wired constructor-to-constructor across two separate ``.so`` files:
``tests/test_gh790_capsule_init_param.py::test_the_pointer_crosses_both_ways_in``
builds a real project and runs ``Capture(t)``, passing the producing OBJECT
rather than its capsule. What was missing was not the capability.

What was missing was the *declaration*. Saying it required naming the same
capsule string at both ends, with a ``.pyi`` that said ``object``, nothing
checking the two ends agreed, and a typo surfacing at runtime. So ``object``
is sugar that RESOLVES to the shipped capsule path (`type`, `capsule` and
`header` all derived) rather than a new parameter kind.

That it resolves *to* the capsule is the design, not an implementation detail.
The obvious alternative -- type-check the object and read its ``handle``
straight out of the struct -- needs the producer's ``<Ref>Object`` layout
inside a consumer ``.so`` that was compiled separately, and possibly by a
different jm. That is the ABI hazard the capsule triangle exists to avoid.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402

CAP = "proj.frame.frame"


def _project(tmp_path: Path, *, module: str | None = None) -> Path:
    """A producer that publishes a capsule, and a consumer that names it."""
    root = tmp_path / "proj"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("proj", root)
        if module:
            module_run(root, module)
        object_run(root, "frame", module, state_vars=[("n", "size_t", "0")])
        property_run(
            root, "frame", "_capsule", module, "capsule", False, capsule=CAP
        )
        object_run(root, "seg", module, state_vars=[("k", "size_t", "0")])
    return root


def _declare(root: Path, ref: str, **extra) -> dict:
    cfg = C.load(root)
    rec = {"name": "frame", "object": ref, "required": True}
    rec.update(extra)
    cfg["seg"]["init_params"] = [rec]
    C.save(root, cfg)
    return C.load(root)


class TestResolution:
    def test_the_capsule_name_is_READ_not_derived(self, tmp_path):
        """The producer already owns that string. Deriving a second one from
        the component id would be a second opinion about it, and the two would
        drift the first time either changed."""
        cfg = C.load(_project(tmp_path))
        assert C.object_ref_capsule(cfg, "frame") == CAP
        assert C.resolve_object_ref(cfg, "frame")[1] == CAP

    def test_it_fills_the_three_c_side_slots(self, tmp_path):
        cfg = C.load(_project(tmp_path))
        ctype, cap, header, cls = C.resolve_object_ref(cfg, "frame")
        assert ctype == "frame_state_t *"
        assert cap == CAP
        assert header == "frame/frame_core.h"
        assert cls == "Frame"

    def test_a_view_is_a_legal_target(self, tmp_path):
        """doppler's motivating reference is `FrameDesc`, which is a VIEW of
        `frame` rather than a component. A resolver that only knew component
        class names would reject the one reference this was written for."""
        root = _project(tmp_path, module="m")
        cfg = C.load(root)
        C.add_view(
            cfg,
            "frame",
            {"class_name": "FrameDesc", "create_fn": "frame_desc"},
        )
        C.save(root, cfg)
        cfg = C.load(root)
        assert C.object_ref_classes(cfg, "frame") == ["Frame", "FrameDesc"]
        # ...and it resolves to the SAME capsule, because it is the same core.
        assert C.resolve_object_ref(cfg, "frame.FrameDesc")[1] == CAP


class TestItRefusesWithTheFix:
    """Every refusal names what to do, not just what is wrong."""

    def test_unknown_component(self, tmp_path):
        cfg = C.load(_project(tmp_path))
        with pytest.raises(ValueError, match="undeclared component 'nope'"):
            C.resolve_object_ref(cfg, "nope.Thing")

    def test_unknown_class_lists_what_there_is(self, tmp_path):
        cfg = C.load(_project(tmp_path))
        with pytest.raises(ValueError) as e:
            C.resolve_object_ref(cfg, "frame.Missing")
        assert "generates no class 'Missing'" in str(e.value)
        assert "it generates: Frame" in str(e.value)

    def test_a_producer_with_no_capsule_is_refused_at_generation(
        self, tmp_path
    ):
        """The whole point of the key: a mismatch that used to surface as a
        runtime capsule-name failure is now a refusal naming the missing
        declaration."""
        cfg = C.load(_project(tmp_path))
        with pytest.raises(ValueError) as e:
            C.resolve_object_ref(cfg, "seg")
        assert "publishes no capsule" in str(e.value)
        assert "jm property seg _capsule --type capsule" in str(e.value)

    def test_a_declared_MODULE_is_not_reported_as_undeclared(self, tmp_path):
        """gh-1227. `plain` IS declared -- as a module. Calling it an
        undeclared component sends the reader to check a spelling that is
        already right, against a list that cannot contain the answer no
        matter what they type. A confidently wrong diagnosis costs more than
        the missing feature standing behind it."""
        root = _project(tmp_path, module="m")
        cfg = C.load(root)
        with pytest.raises(ValueError) as e:
            C.resolve_object_ref(cfg, "m")
        msg = str(e.value)
        assert "undeclared" not in msg
        assert "is a declared module, not a component" in msg
        # ...and it names what CAN be referenced instead.
        assert "frame" in msg and "seg" in msg

    def test_a_kind_module_says_so_and_names_the_working_spelling(
        self, tmp_path
    ):
        """A `kind` module is the shape most likely to be wanted here, so the
        refusal names the spelling that works TODAY rather than only refusing.

        This asserted the same of a **handle** until gh-1227, which made one a
        real target -- so the case moved to `capsule`, which is still refused.
        The handle's own behaviour, including the "publishes no capsule"
        refusal this used to hit, lives in
        `test_gh1227_handle_object_target.py`.
        """
        root = _project(tmp_path)
        cfg = C.load(root)
        cfg.setdefault("module", {})["ring"] = {"kind": "capsule"}
        with pytest.raises(ValueError) as e:
            C.resolve_object_ref(cfg, "ring")
        msg = str(e.value)
        assert 'kind = "capsule" module' in msg
        assert "a capsule module is not a valid target" in msg
        assert "gh-790" in msg  # the spelling that works today
        assert "undeclared" not in msg
        # It must not describe a capsule module as the kind that IS a target.
        assert 'names a component, one of its views, or a kind = "handle"' in (
            msg
        )

    def test_object_and_capsule_together_are_refused(self, tmp_path):
        """Allowing both would re-create the duplication `object` removes,
        with no answer to which one wins."""
        cfg = C.load(_project(tmp_path))
        with pytest.raises(ValueError, match="cannot both be declared"):
            C._object_ref_slot(
                cfg, {"name": "x", "object": "frame", "capsule": "a.b"}, 1
            )


class TestTheManifestKeepsTheDECLARATION:
    def test_it_round_trips_as_object_not_as_its_resolution(self, tmp_path):
        """Persisting the resolution would bake the producer's capsule string
        back into the consumer -- re-creating by round-trip exactly the
        duplication the key removes."""
        root = _project(tmp_path)
        _declare(root, "frame.Frame")
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(root)
        raw = (root / C.FILENAME).read_text()
        assert 'object = "frame.Frame"' in raw
        assert CAP not in raw.split("[[seg.init_params]]")[1]

    def test_object_is_a_recognised_key(self, tmp_path):
        """An unregistered key warns, and a warning on a correct manifest is
        how a real one stops being read."""
        from just_makeit import _keys

        assert "object" in _keys.INIT_PARAM_KEYS
        # gh-1224's consumer is a composer-module init_param, so leaving it
        # off this second vocabulary would register it everywhere except the
        # one table that asked for it.
        assert "object" in _keys.KIND_INIT_PARAM_KEYS


class TestBothStubProducers:
    """jm has two `.pyi` producers for init_params and fixing one and not the
    other has caused a real bug every time (gh-805 §H)."""

    def test_standalone_stub_names_and_imports_the_class(self, tmp_path):
        root = _project(tmp_path)
        _declare(root, "frame.Frame")
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(root)
        pyi = (root / "src" / "proj" / "seg.pyi").read_text()
        assert "def __init__(self, frame: Frame) -> None: ..." in pyi
        assert "from .frame import Frame" in pyi

    def test_module_stub_names_and_imports_the_class(self, tmp_path):
        root = _project(tmp_path, module="m")
        _declare(root, "frame.Frame")
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(root)
        pyi = (root / "src" / "proj" / "m" / "m.pyi").read_text()
        assert "frame: Frame" in pyi

    def test_a_plain_capsule_param_still_says_object(self, tmp_path):
        """The gh-790 spelling is unchanged: no class is named, so there is
        none to annotate with."""
        root = _project(tmp_path)
        cfg = C.load(root)
        cfg["seg"]["init_params"] = [
            {
                "name": "frame",
                "type": "frame_state_t *",
                "capsule": CAP,
                "header": "frame/frame_core.h",
                "required": True,
            }
        ]
        C.save(root, cfg)
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(root)
        pyi = (root / "src" / "proj" / "seg.pyi").read_text()
        assert "frame: object" in pyi
        assert "import Frame" not in pyi


class TestTheImportIsDerivedFromTheSignature:
    def test_the_import_path_follows_how_the_producer_was_built(
        self, tmp_path
    ):
        """A module object is imported from the module, a standalone one from
        its own `.so`. Guessing wrong is an import error in every consumer's
        type-check, not a soft failure."""
        cfg = C.load(_project(tmp_path))
        assert C.object_ref_import(cfg, "frame") == "from .frame import Frame"
        cfg_m = C.load(_project(tmp_path / "b", module="m"))
        assert C.object_ref_import(cfg_m, "frame") == "from .m import Frame"

    def test_no_annotation_means_no_import(self, tmp_path):
        """Derived from the RENDERED signature, so an import cannot outlive
        the annotation that needed it."""
        root = _project(tmp_path)
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(root)
        pyi = (root / "src" / "proj" / "seg.pyi").read_text()
        assert "import Frame" not in pyi


class TestTheGeneratedCIsTheShippedCapsulePath:
    """No new C codegen: that is the design. The pointer crosses as a capsule,
    so no consumer ever needs the producer's struct layout."""

    def test_it_unwraps_by_capsule_name_not_by_struct(self, tmp_path):
        root = _project(tmp_path)
        _declare(root, "frame.Frame")
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(root)
        ext = (root / "native" / "src" / "seg" / "seg_ext.c").read_text()
        assert f'PyCapsule_GetPointer(frame_cap, "{CAP}")' in ext
        assert 'PyObject_GetAttrString(frame_obj, "_capsule")' in ext
        # never the producer's struct
        assert "FrameObject" not in ext

    def test_it_keeps_the_strong_reference_to_the_owner(self, tmp_path):
        """Inherited from gh-790, and the reason this resolves to the capsule
        rather than around it: the borrowed pointer dangles the moment the
        producer is collected."""
        root = _project(tmp_path)
        _declare(root, "frame.Frame")
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(root)
        ext = (root / "native" / "src" / "seg" / "seg_ext.c").read_text()
        assert "PyObject *_frame_owner;" in ext
        assert "Py_XSETREF(self->_frame_owner, frame_obj);" in ext
        assert "Py_XDECREF(self->_frame_owner);" in ext

    def test_the_foreign_header_reaches_the_sacred_core_h(self, tmp_path):
        """The pointed-to type lands in the `create()` prototype, so without
        the include the sacred header does not parse."""
        root = _project(tmp_path)
        _declare(root, "frame.Frame")
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(root)
        core_h = (root / "native" / "inc" / "seg" / "seg_core.h").read_text()
        assert '#include "frame/frame_core.h"' in core_h
        assert "seg_state_t *seg_create(frame_state_t *frame);" in core_h


class TestTheCLIAndTheReplay:
    """A declaration jm accepts must survive `jm script`.

    gh-900's round-trip test exists because dropping a key there replays the
    project with a DIFFERENT `create()` prototype against C the author never
    changed. An `object` param is the sharpest version of that: its `capsule`
    and `header` are stripped by the writer because they are derived, so
    without its own branch it reaches the scalar path and replays as
    `frame:frame_state_t *:required` -- a script rebuilding the object as a
    scalar of a type jm does not know.
    """

    def test_the_cli_grammar_carries_the_reference(self):
        from just_makeit._cli_parse import parse_init_param_flag

        got, _ = parse_init_param_flag(
            ["--init-param", "frame:object:frame.Frame"], 0
        )
        assert got[0] == "frame"
        assert got[15] == "frame.Frame"
        # Nothing is resolved at parse time -- there is no manifest yet, and
        # the referenced component may not even be declared.
        assert got[1] == "" and got[10] == "" and got[11] == ""
        assert got[8] is True

    def test_optional_makes_it_nullable(self):
        from just_makeit._cli_parse import parse_init_param_flag

        got, _ = parse_init_param_flag(
            ["--init-param", "frame:object:frame.Frame:optional"], 0
        )
        assert got[15] == "frame.Frame"
        assert got[8] is False

    def test_a_bare_object_marker_is_refused_with_the_spelling(self, capsys):
        from just_makeit._cli_parse import parse_init_param_flag

        with pytest.raises(SystemExit):
            parse_init_param_flag(["--init-param", "frame:object"], 0)
        err = capsys.readouterr().err
        assert "frame:object:frame.FrameDesc" in err

    def test_the_writer_omits_the_derived_type(self):
        """Persisting a type beside the reference that derives it is the
        duplication the key removes, one layer down."""
        rec = C.init_param_tuple_to_dict(
            (
                "frame",
                "",
                "",
                "",
                "",
                "",
                False,
                "",
                True,
                "",
                "",
                "",
                "",
                "",
                "",
                "frame.Frame",
            )
        )
        assert rec == {
            "name": "frame",
            "object": "frame.Frame",
            "required": True,
        }

    def test_jm_script_replays_the_object_form(self, tmp_path):
        from just_makeit._script import run as script_run

        root = _project(tmp_path)
        _declare(root, "frame.Frame")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            script_run(root)
        out = buf.getvalue()
        assert "frame:object:frame.Frame" in out, (
            f"`jm script` loses the declaration:\n{out}"
        )
        # ...and never as the resolved pointer type.
        assert "frame_state_t *" not in out

    def test_jm_script_replays_a_NULLABLE_object_param(self, tmp_path):
        """The `:optional` token, which the capsule branch below already has a
        comment about: dropping it replays a nullable reference as a mandatory
        one, rebuilding a constructor that rejects the `None` the original
        accepted. Same silent-divergence class as losing the grammar itself,
        and it survives a suite that only ever replays the required form --
        which is what codecov flagged on #1226.
        """
        from just_makeit._script import run as script_run

        root = _project(tmp_path)
        _declare(root, "frame.Frame", required=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            script_run(root)
        assert "frame:object:frame.Frame:optional" in buf.getvalue(), (
            f"`jm script` replays a nullable reference as mandatory:"
            f"\n{buf.getvalue()}"
        )

    def test_an_unresolvable_reference_yields_no_import(self, tmp_path):
        """`object_ref_import` is called on the read path for every param, so
        it must not raise on a manifest the resolver would reject -- the
        refusal belongs to `resolve_object_ref`, which reports it once with
        the fix, not to the stub helper."""
        cfg = C.load(_project(tmp_path))
        assert C.object_ref_import(cfg, "nope.Thing") == ""
