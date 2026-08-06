"""gh-625 — `jm property` / `jm method` reject a name that breaks the build.

Both commands accepted any string, wrote it into four artifacts including the
**sacred** header, and exited 0:

    $ just-makeit property thing level:double     # note: no --type
    $ echo $?
    0
    // native/inc/thing/thing_core.h:96
    size_t thing_get_level:double(const thing_state_t *state);

`make` then fails in generated code the user did not write, and the `.pyi` is
not parseable Python. Recovery means hand-editing the sacred header plus three
generated files, or `jm remove`-ing a property whose name is itself malformed.

The input is a natural mistake rather than an exotic one: every
`--state`/`--init-param`/`--param` flag in jm is colon-delimited
`name:type[:default]`, so `jm property thing level:double` is the shape muscle
memory produces — `jm property` takes the type as a separate `--type` flag.

`jm object` and `jm function` already rejected exactly this. The predicate was
written out five separate times and reachable from neither of the two commands
missing it, so the fix is one implementation every command shares rather than
a sixth copy.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402

BAD = "level:double"


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "probe"
    new_run("probe", root)
    object_run(root, "thing", None, state_vars=[("gain", "double", "1.0")])
    return root


class TestThePredicate:
    """One implementation, five former copies. These pin its exact semantics
    so a future edit cannot loosen it for everyone at once."""

    @pytest.mark.parametrize("name", ["gain", "gain_2", "_leading", "a"])
    def test_accepts_an_identifier(self, name):
        assert C.valid_identifier(name)

    @pytest.mark.parametrize(
        "name", [BAD, "", "2fast", "has space", "has-dash", "a.b", "_"]
    )
    def test_rejects_what_breaks_generated_code(self, name):
        assert not C.valid_identifier(name)

    def test_the_empty_name_does_not_raise(self):
        """The old inline form was `not n.replace(...).isalnum() or
        n[0].isdigit()` — safe only because `or` short-circuits. Worth
        pinning: an implementation that reordered those terms would
        IndexError on the empty string instead of reporting it."""
        assert C.valid_identifier("") is False
        assert C.validate_name("", "property")

    def test_the_message_names_the_kind(self):
        assert "property name" in C.validate_name(BAD, "property")
        assert "method name" in C.validate_name(BAD, "method")

    def test_agreement_with_the_module_id_validator(self):
        """`validate_module_id` used to carry its own copy of the predicate.
        Now it calls this one, so a dotted id is exactly per-segment valid."""
        assert C.validate_module_id("dsp.filters") is None
        assert C.validate_module_id("dsp.2fast") is not None
        assert C.validate_module_id("dsp..filters") is not None


class TestTheCommandsThatWereMissingIt:
    def test_property_rejects_it(self, project, capsys):
        with pytest.raises(SystemExit) as exc:
            property_run(project, "thing", BAD, None, "size_t", False)
        assert exc.value.code == 1
        assert "not a valid property name" in capsys.readouterr().err

    def test_method_rejects_it(self, project, capsys):
        with pytest.raises(SystemExit) as exc:
            method_run(
                project,
                "thing",
                "exec:float",
                None,
                "double",
                "double",
                False,
                [],
            )
        assert exc.value.code == 1
        assert "not a valid method name" in capsys.readouterr().err

    def test_nothing_is_written(self, project, capsys):
        """The damage in the report is not the exit code — it is the four
        artifacts, one of them sacred, left carrying the bad name. Rejecting
        after writing would be no better than not rejecting."""
        header = project / "native" / "inc" / "thing" / "thing_core.h"
        before = header.read_text()
        manifest = (project / C.FILENAME).read_text()

        with pytest.raises(SystemExit):
            property_run(project, "thing", BAD, None, "size_t", False)

        assert header.read_text() == before, "the sacred header was touched"
        assert (project / C.FILENAME).read_text() == manifest
        assert BAD not in before


class TestTheCommandsThatAlreadyHadIt:
    """Routing four call sites through one helper must not change what they
    accept or what they print — the messages are load-bearing, quoted in the
    issue and in every project's muscle memory."""

    def test_object_still_rejects_and_says_the_same_thing(
        self, project, capsys
    ):
        with pytest.raises(SystemExit):
            object_run(project, "bad:name", None)
        err = capsys.readouterr().err
        assert "'bad:name' is not a valid object name." in err
        assert "must not start with a digit." in err

    def test_a_valid_name_still_works(self, project, capsys):
        property_run(project, "thing", "level", None, "size_t", False)
        assert "level" in (project / C.FILENAME).read_text()


class TestTheAudit:
    """The issue asked for the other name-taking surfaces in the same pass."""

    def test_a_view_class_name_is_validated(self, project, capsys):
        """A view's class name is lowercased into its fragment's *filename*
        (gh-504), so an invalid one lands in a path too."""
        from just_makeit._view import run as view_run

        with pytest.raises(SystemExit):
            view_run(project, "thing", "Bad:Name", None, "thing_create")
        assert "not a valid view class name" in capsys.readouterr().err

    def test_app_is_deliberately_not_validated(self):
        """`jm app --name` becomes a filename, a CMake target and a console
        script's `prog` — all three of which legitimately allow a hyphen
        (`my-tool`). Requiring a C identifier there would reject apps that
        work today, so it is excluded on purpose rather than overlooked.
        Pinned so the exclusion is a decision someone has to revisit, not a
        gap someone rediscovers."""
        assert not C.valid_identifier("my-tool")
