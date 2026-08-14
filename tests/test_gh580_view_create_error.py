"""gh-580: a view inherits its parent's create_error, and may override it.

A ``[[<obj>.views]]`` flavor got no create-failure translation at all — the
generator passed an empty category unconditionally — so every construction
failure on a view surfaced as the blanket ``MemoryError`` that gh-482 exists to
replace.

That is backwards for this case. A view exists precisely *because* its
constructor takes different, usually more, parameters than the parent's:
``RateConverter_create(rate, compensate)`` has two ways to be handed something
invalid; ``RateConverter_create_matched(rate, compensate, pulse, beta, span,
pulse_sps, num_phases)`` has seven. The flavor is the constructor that most
needs the translation, and it was the only one that could not have it.

Note the deliberate asymmetry with gh-509's *warnings*, which a view does NOT
inherit: a view carries no parent warnings because a warning describes a
condition the view may simply not have. An error, by contrast, describes the
same object refusing to construct, so the parent's translation is right for both
front doors — inheritance is what makes the common case correct with no extra
declaration, and an explicit override handles the rest.
"""

import io
import contextlib
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._error import run as error_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._view import run as view_run  # noqa: E402

PARENT_MSG = "RateConverter: invalid parameter (need rate > 0)"
VIEW_MSG = "MatchedRateConverter: invalid pulse/beta/span combination"


@pytest.fixture()
def project(tmp_path, capsys):
    """A module object with a view, mirroring doppler's RateConverter."""
    dest = tmp_path / "proj"
    new_run("proj", dest, modules=["resample"])
    object_run(
        dest,
        "rateconv",
        module="resample",
        arg_type="float _Complex",
        return_type="float _Complex",
        state_vars=[("rate", "double", "0.0")],
    )
    view_run(
        dest,
        "rateconv",
        "MatchedRateConverter",
        "resample",
        "rateconv_create_matched",
        init_params=[("rate", "double", "0.0"), ("beta", "double", "0.0")],
    )
    capsys.readouterr()
    return dest


def _joined(src):
    """Collapse adjacent C string literals so a wrapped message reads whole.

    `_c_string_literal` wraps at column 24, so a long message is emitted as
    ``"...span " "combination"`` across two lines — a plain substring check for
    the original text fails on exactly the long messages a view most wants.
    """
    return re.sub(r'"\s*\n\s*"', "", src)


def _script_text(project):
    """`jm script` output. `_script.run` prints, so capture rather than call a
    builder — this exercises the same path a user sees."""
    from just_makeit._script import run as script_run

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        script_run(project)
    return buf.getvalue()


def _view_ext(project):
    return (
        project
        / "native"
        / "src"
        / "resample"
        / "resample_ext_matchedrateconverter.c"
    ).read_text(encoding="utf-8")


def _parent_ext(project):
    return (
        project / "native" / "src" / "resample" / "resample_ext_rateconv.c"
    ).read_text(encoding="utf-8")


def _declare_parent(project, capsys):
    error_run(project, "rateconv", "ValueError", PARENT_MSG, module="resample")
    capsys.readouterr()


def _declare_view(project, capsys, msg=VIEW_MSG):
    error_run(
        project,
        "rateconv",
        "ValueError",
        msg,
        module="resample",
        view="MatchedRateConverter",
    )
    capsys.readouterr()


class TestInheritance:
    def test_undeclared_parent_leaves_view_on_memoryerror(
        self, project, capsys
    ):
        """No declaration anywhere → byte-identical to pre-gh-580 output."""
        ext = _joined(_view_ext(project))
        assert "PyExc_MemoryError" in ext
        assert "rateconv_create_matched returned NULL" in ext

    def test_view_inherits_parent_translation(self, project, capsys):
        """The whole point: declaring on the parent fixes the view too."""
        _declare_parent(project, capsys)
        ext = _joined(_view_ext(project))
        assert "PyExc_ValueError" in ext
        assert PARENT_MSG in ext
        assert "PyExc_MemoryError" not in ext

    def test_inheritance_is_not_frozen_into_the_manifest(
        self, project, capsys
    ):
        """The view's TOML stays clean — it tracks the parent, not a copy.

        Dumping the *resolved* value would silently convert inheritance into a
        snapshot, so a later change to the parent would stop reaching the view.
        """
        _declare_parent(project, capsys)
        cfg = C.load(project)
        view = C.views(cfg, "rateconv")[0]
        assert "create_error" not in view
        assert C.view_create_error(cfg, "rateconv", view) == "ValueError"


class TestOverride:
    def test_view_declaration_wins(self, project, capsys):
        _declare_parent(project, capsys)
        _declare_view(project, capsys)
        ext = _joined(_view_ext(project))
        assert VIEW_MSG in ext
        assert PARENT_MSG not in ext

    def test_parent_is_unaffected_by_the_view_override(self, project, capsys):
        _declare_parent(project, capsys)
        _declare_view(project, capsys)
        parent = _joined(_parent_ext(project))
        assert PARENT_MSG in parent
        assert VIEW_MSG not in parent

    def test_override_persists_across_a_save_load_cycle(self, project, capsys):
        """The view dumper writes an explicit key list; a new key it does not
        know about is silently dropped, so the feature would appear to work
        in-process and vanish on reload."""
        _declare_view(project, capsys)
        cfg = C.load(project)
        view = C.views(cfg, "rateconv")[0]
        assert view.get("create_error") == "ValueError"
        assert view.get("create_error_message") == VIEW_MSG

    def test_view_can_declare_without_any_parent_declaration(
        self, project, capsys
    ):
        _declare_view(project, capsys)
        assert VIEW_MSG in _joined(_view_ext(project))
        # ...and the parent, undeclared, keeps its historical block.
        assert "PyExc_MemoryError" in _parent_ext(project)


class TestPerKeyResolution:
    """Category and message resolve independently, as at the object level."""

    def test_message_only_refines_wording_under_parent_category(self):
        cfg = {
            "acq": {
                "create_error": "ValueError",
                "create_error_message": "parent text",
                "views": [{"create_error_message": "view text"}],
            }
        }
        view = cfg["acq"]["views"][0]
        assert C.view_create_error(cfg, "acq", view) == "ValueError"
        assert C.view_create_error_message(cfg, "acq", view) == "view text"

    def test_category_only_reuses_parent_message(self):
        cfg = {
            "acq": {
                "create_error": "ValueError",
                "create_error_message": "parent text",
                "views": [{"create_error": "RuntimeError"}],
            }
        }
        view = cfg["acq"]["views"][0]
        assert C.view_create_error(cfg, "acq", view) == "RuntimeError"
        assert C.view_create_error_message(cfg, "acq", view) == "parent text"

    def test_explicit_empty_category_opts_out(self):
        """A view can deliberately fall back to MemoryError."""
        cfg = {
            "acq": {
                "create_error": "ValueError",
                "create_error_message": "parent text",
                "views": [{"create_error": ""}],
            }
        }
        view = cfg["acq"]["views"][0]
        assert C.view_create_error(cfg, "acq", view) == ""


class TestDumpSerializer:
    """`_dump` is a second serializer, and it needs the keys too.

    `C.save` normally routes through `_write_doc`, which edits an existing file
    with tomlkit and therefore carries any key through generically. But
    `_dump` writes brand-new fragments and is what `jm split-objects` and
    `jm upgrade` use — and it emits an explicit key list, so a key it does not
    know about is silently dropped. A test that only exercises save-then-load on
    an *existing* fragment passes either way; this one is what actually pins the
    dumper.
    """

    def _view_section(self, dumped):
        return dumped[dumped.index("[[acq.views]]") :]

    def test_declared_override_is_dumped(self):
        text = C._dump(
            {
                "acq": {
                    "views": [
                        {
                            "class_name": "Burst",
                            "create_fn": "acq_create_burst",
                            "create_error": "ValueError",
                            "create_error_message": "burst text",
                        }
                    ]
                }
            }
        )
        section = self._view_section(text)
        assert 'create_error = "ValueError"' in section
        assert "burst text" in section

    def test_inherited_translation_is_not_dumped(self):
        text = C._dump(
            {
                "acq": {
                    "create_error": "ValueError",
                    "create_error_message": "parent text",
                    "views": [
                        {
                            "class_name": "Burst",
                            "create_fn": "acq_create_burst",
                        }
                    ],
                }
            }
        )
        assert "create_error" not in self._view_section(text)

    def test_dumped_override_reparses_with_view_subtables(self, tmp_path):
        """The scalar keys must precede the nested subtables.

        TOML binds ``[[acq.views.warnings]]`` to the *preceding* ``[[acq.views]]``,
        so a scalar emitted after a subtable would land on the warning table
        instead — the view would silently lose its translation and the warning
        would gain a nonsense key.
        """
        text = C._dump(
            {
                "acq": {
                    "views": [
                        {
                            "class_name": "Burst",
                            "create_fn": "acq_create_burst",
                            "create_error": "ValueError",
                            "create_error_message": "burst text",
                            "warnings": [
                                {
                                    "condition": "clipped",
                                    "message": "clipping",
                                    "category": "UserWarning",
                                }
                            ],
                        }
                    ]
                }
            }
        )
        parsed = C.tomllib.loads(text)
        view = parsed["acq"]["views"][0]
        assert view["create_error"] == "ValueError"
        assert view["create_error_message"] == "burst text"
        assert view["warnings"][0]["condition"] == "clipped"
        assert "create_error" not in view["warnings"][0]


class TestScriptRoundTrip:
    def test_declared_override_is_emitted_with_view_flag(
        self, project, capsys
    ):
        _declare_parent(project, capsys)
        _declare_view(project, capsys)
        text = _script_text(project)
        assert "--view MatchedRateConverter" in text
        assert VIEW_MSG in text

    def test_inherited_translation_is_not_re_emitted(self, project, capsys):
        """Emitting it would replay as an explicit override — see
        TestInheritance.test_inheritance_is_not_frozen_into_the_manifest."""
        _declare_parent(project, capsys)
        text = _script_text(project)
        assert "--view MatchedRateConverter" not in text


class TestCLIGuards:
    # gh-963: this asserted that omitting `--module` on a MODULE-owned object
    # was an error. The manifest records the owner, so it never should have
    # been — and treating the flag as the only source of truth is what let the
    # sibling verbs take the standalone path and emit a class with the member
    # missing. What survives is the guard where it means something: a view on a
    # genuinely standalone object, which cannot have one.
    def test_view_on_a_standalone_object_is_rejected(self, project, capsys):
        object_run(project, "solo", None, [("s", "double", "0.0")])
        with pytest.raises(SystemExit):
            error_run(
                project,
                "solo",
                "ValueError",
                VIEW_MSG,
                view="MatchedRateConverter",
            )
        assert "is standalone" in capsys.readouterr().err

    def test_a_module_owned_view_no_longer_needs_the_flag(self, project):
        error_run(
            project,
            "rateconv",
            "ValueError",
            VIEW_MSG,
            view="MatchedRateConverter",
        )

    def test_unknown_view_is_rejected(self, project, capsys):
        with pytest.raises(SystemExit):
            error_run(
                project,
                "rateconv",
                "ValueError",
                VIEW_MSG,
                module="resample",
                view="NoSuchView",
            )
        assert "no view 'NoSuchView'" in capsys.readouterr().err
