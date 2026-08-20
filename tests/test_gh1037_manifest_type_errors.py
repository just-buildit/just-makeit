"""gh-1037: an unusable type is reported as itself, not as someone else's bug.

Adding an object with ``type = "unsigned"`` on an init-param made ``jm apply``
print

    error: object 'dp_tlm_capture': [dp_tlm_capture.destroy] exit 'close' is
           not a declared method. Declared: none.

about an unrelated object in an unrelated module that the change never
touched -- while ``close`` was declared forty lines above the block naming it.
Rejecting the type is right; the message was a false statement about someone
else's declaration, and it cost the reporter six isolation runs and a wrongly
filed issue before they bisected their own manifest.

Two independent defects, gated separately below because either one alone
reproduces a version of it.

**A deferral flushed through an abort.** ``_apply``'s replay runs inside
``_object.deferred_module_regen()``, whose flush renders the FINAL state of
each module. It ran from a ``finally``, so it also ran while the body was
unwinding -- rendering a manifest caught mid-replay. ``_replay`` creates every
object before replaying any method, so at that moment an object declaring
``[X.destroy] exit = "m"`` genuinely has no methods, and the render raised
``ValueError`` from inside the ``finally``. An exception raised there
**replaces** the one propagating, so the user's real error was discarded and
its stand-in reported instead. "Declared: none" was the tell.

**The manifest type check did not cover the input side.** ``manifest_type_errors``
(gh-595/gh-598) validated ``return_type`` and ``result_fields`` and nothing
that feeds a binding, so four tables reached ``_CTYPE_META[...]`` in the
renderer and raised a bare ``KeyError``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _object as O  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


class TestDeferralsDoNotFlushThroughAnAbort:
    """The flush renders the final state; an aborted body has none.

    Unit-level and independent of any particular render failure, because the
    bug is not about types at all: ANY exception mid-replay was liable to be
    replaced by whatever the flush happened to complain about. Gating it
    through the type error would let the type fix mask a regression here.
    """

    def test_module_regen_is_dropped_when_the_body_raises(self, tmp_path):
        ran: list[str] = []
        real = O._regenerate_module_now
        O._regenerate_module_now = lambda *a, **k: ran.append(a[2])
        try:
            with pytest.raises(RuntimeError, match="the real error"):
                with O.deferred_module_regen():
                    O._regenerate_module(tmp_path, {}, "tlm", "demo")
                    raise RuntimeError("the real error")
        finally:
            O._regenerate_module_now = real
        assert ran == [], (
            "the flush rendered a module while the replay was unwinding; a "
            "failure inside it REPLACES the exception being propagated"
        )

    def test_module_regen_still_flushes_on_a_clean_exit(self, tmp_path):
        """The fix must not disarm the deferral it is guarding.

        Without this, dropping the flush entirely would pass the test above.
        """
        ran: list[str] = []
        real = O._regenerate_module_now
        O._regenerate_module_now = lambda *a, **k: ran.append(a[2])
        try:
            with O.deferred_module_regen():
                O._regenerate_module(tmp_path, {}, "tlm", "demo")
        finally:
            O._regenerate_module_now = real
        assert ran == ["tlm"]

    def test_deferred_save_is_dropped_when_the_body_raises(self, tmp_path):
        """The sibling deferral, same rule -- checked on disk.

        `save` decides internally whether to defer, so the honest check is
        the file: a manifest written through an aborted scope would show the
        mutation that never completed.
        """
        root = tmp_path / "demo"
        new_run("demo", root)
        before = (root / C.FILENAME).read_text(encoding="utf-8")
        cfg = C.load(root)
        cfg["project"]["version"] = "9.9.9"
        with pytest.raises(RuntimeError, match="the real error"):
            with C.deferred_save():
                C.save(root, cfg)
                raise RuntimeError("the real error")
        assert (root / C.FILENAME).read_text(encoding="utf-8") == before

    def test_deferred_save_still_writes_on_a_clean_exit(self, tmp_path):
        """...and the deferral it guards is not disarmed."""
        root = tmp_path / "demo"
        new_run("demo", root)
        cfg = C.load(root)
        cfg["project"]["version"] = "9.9.9"
        with C.deferred_save():
            C.save(root, cfg)
        assert "9.9.9" in (root / C.FILENAME).read_text(encoding="utf-8")

    def test_the_original_exception_is_what_propagates(self, tmp_path):
        """The defect in one assertion: a raising flush must not win.

        Before the fix the flush's own ValueError replaced the RuntimeError
        here, which is exactly how a KeyError about 'unsigned' reached the
        user as a ValueError about an unrelated object's destroy block.
        """
        real = O._regenerate_module_now

        def _boom(*a, **k):
            raise ValueError("the misleading stand-in")

        O._regenerate_module_now = _boom
        try:
            with pytest.raises(RuntimeError, match="the real error"):
                with O.deferred_module_regen():
                    O._regenerate_module(tmp_path, {}, "tlm", "demo")
                    raise RuntimeError("the real error")
        finally:
            O._regenerate_module_now = real


class TestManifestTypeErrorsCoversTheInputSide:
    """The four tables that reached ``_CTYPE_META`` unchecked."""

    @pytest.mark.parametrize(
        "cfg,expect",
        [
            (
                {"v": {"init_params": [{"name": "k", "type": "unsigned"}]}},
                "'v' init_param 'k' has unknown type 'unsigned'.",
            ),
            (
                {"v": {"methods": [{"name": "run", "arg_type": "unsigned"}]}},
                "'v' method 'run' has unknown arg_type 'unsigned'.",
            ),
            (
                {
                    "v": {
                        "methods": [
                            {
                                "name": "run",
                                "params": [{"name": "p", "type": "unsigned"}],
                            }
                        ]
                    }
                },
                "'v' method 'run': param 'p' has unknown type 'unsigned'.",
            ),
            (
                {
                    "module": {
                        "win": {
                            "functions": [
                                {
                                    "name": "hann",
                                    "out_type": "double",
                                    "params": [
                                        {"name": "n", "type": "unsigned"}
                                    ],
                                }
                            ]
                        }
                    }
                },
                "module 'win' function 'hann': param 'n' has unknown "
                "type 'unsigned'.",
            ),
        ],
        ids=["init_param", "method_arg_type", "method_param", "fn_param"],
    )
    def test_each_input_table_is_checked(self, cfg, expect):
        errors = C.manifest_type_errors(cfg)
        assert errors, "the declaration was accepted"
        assert errors[0].splitlines()[0] == expect

    def test_an_array_of_an_unknown_element_is_caught(self):
        """``is_array_param_type`` is syntactic -- it only sees the ``[]``.

        The renderer indexes the ELEMENT type, so ``unsigned[]`` crashed in
        exactly the same place while passing a check that asked only about
        the suffix.
        """
        cfg = {"v": {"methods": [{"name": "run", "arg_type": "unsigned[]"}]}}
        assert C.manifest_type_errors(cfg)

    @pytest.mark.parametrize(
        "cfg",
        [
            # gh-515/gh-565: manifest-only pseudo-types with a real coercion.
            {"v": {"init_params": [{"name": "p", "type": "path"}]}},
            {"v": {"init_params": [{"name": "b", "type": "bytes"}]}},
            # A foreign C type the project owns; jm never looks it up.
            {
                "v": {
                    "init_params": [
                        {"name": "h", "type": "dp_t *", "header": "dp.h"}
                    ]
                }
            },
            # A codec param declares its shape by role, not by a type.
            {
                "v": {
                    "methods": [
                        {
                            "name": "pack",
                            "codec": "kw",
                            "params": [{"name": "v", "role": "variant"}],
                        }
                    ]
                }
            },
            # Ordinary good declarations, including arrays and enums.
            {"v": {"init_params": [{"name": "t", "type": "float[]"}]}},
            {"v": {"methods": [{"name": "r", "arg_type": "void"}]}},
            {
                "v": {
                    "methods": [
                        {
                            "name": "r",
                            "params": [{"name": "k", "type": "enum:kind"}],
                        }
                    ]
                }
            },
        ],
        ids=["path", "bytes", "header", "codec_role", "array", "void", "enum"],
    )
    def test_legitimate_declarations_are_not_rejected(self, cfg):
        """A false rejection is worse than the traceback this replaces.

        Every entry here is a spelling some shipped project depends on; the
        first two are the whole point of gh-515 and gh-565.
        """
        assert C.manifest_type_errors(cfg) == []


class TestTheIssueEndToEnd:
    """The reported scenario, through the interface the reporter used."""

    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        root = tmp_path / "demo"
        new_run("demo", root)
        module_run(root, "tlm")
        object_run(
            root,
            "capture",
            "tlm",
            state_vars=[("n", "uint64_t", "0")],
            arg_type="void",
            return_type="int",
        )
        method_run(root, "capture", "close", "tlm", "void", "int", False, [])
        # The destroy block naming a method declared in the same fragment --
        # correct, and what the false error claimed was wrong.
        man = root / C.FILENAME
        man.write_text(
            man.read_text(encoding="utf-8")
            + '\n[capture.destroy]\nexit = "close"\n',
            encoding="utf-8",
        )
        apply_run(root)
        # A second, unrelated object in a LATER module: its replay runs after
        # capture's has already deferred a module regen, and before any
        # method is replayed -- the window where capture has no methods.
        module_run(root, "zz")
        object_run(
            root,
            "viterbi",
            "zz",
            state_vars=[("acc", "uint32_t", "0")],
            arg_type="void",
            return_type="int",
        )
        man.write_text(
            man.read_text(encoding="utf-8")
            + '\n[[viterbi.init_params]]\nname = "k"\ntype = "unsigned"\n',
            encoding="utf-8",
        )
        return root

    def test_apply_names_the_offending_object(self, project, capsys):
        with pytest.raises(SystemExit):
            apply_run(project)
        err = capsys.readouterr().err
        assert "'viterbi' init_param 'k' has unknown type 'unsigned'." in err

    def test_apply_says_nothing_about_the_unrelated_object(
        self, project, capsys
    ):
        """The defect itself: the report must not name someone else."""
        with pytest.raises(SystemExit):
            apply_run(project)
        err = capsys.readouterr().err
        assert "capture" not in err, (
            "the error still names an unrelated object; a check ran against "
            "a partially composed config and reported its own incomplete "
            "state as the user's error"
        )
        assert "Declared: none" not in err
