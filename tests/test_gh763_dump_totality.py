"""gh-763 — `_dump` must not silently lose a section it was never taught.

`_dump` renders the manifest one hand-written branch per section kind. Two
things followed from that, and both are the same defect wearing different
clothes:

* **The array branch keyed off a name list** (`c_deps`, `find_packages`,
  `pkg_modules`, `platforms`). Any other list-valued key fell through to the
  scalar branch and was written as a Python repr inside quotes —
  `c_format_command = "['clang-format']"` — which reloads as a string and
  raises. It failed *open*, and it proved that by biting gh-773, the first
  change to add a list-valued `[project]` key.
* **A whole section kind it does not know is dropped.** `[codec.X]` is the one
  doppler has.

`_round_trips` was supposed to contain the second. It does not: it guards only
the *update* path. `save` calls `_dump` unguarded when the file does not exist
yet, and again when tomlkit is unavailable — and that second one is a real
environment, since just-buildit does not propagate `[project].dependencies` to
the wheel. A tool-installed jm with no tomlkit deleted the entire codec table
on every root-writing command, silently.

The fix is the same shape in both halves: **ask, do not require**. The array
branch asks the value what it is; `_dump` parses its own output and appends
whatever did not survive. Neither can be forgotten for the key or the kind
somebody adds next.
"""

from __future__ import annotations

import builtins
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402

# doppler's real [codec.blue_keyword]: scalars, a bool, and an array of inline
# tables with mixed value types. It is the SSOT that retired both hand-written
# wfm keyword marshalers, so losing it loses working code.
CODEC = {
    "blue_keyword": {
        "discriminant": "char",
        "scalar_collapse": True,
        "entries": [
            {"code": "A", "ctype": "char", "bytes": True},
            {"code": "B", "ctype": "int8_t"},
            {"code": "D", "ctype": "double"},
        ],
    }
}


def _cfg():
    return {
        "project": {"name": "p", "version": "0.1.0"},
        "codec": {k: dict(v) for k, v in CODEC.items()},
    }


@pytest.fixture
def no_tomlkit(monkeypatch):
    """tomlkit absent — the environment a tool-installed jm can genuinely be
    in, and the one where `save` reaches `_dump` with no guard in front."""
    real = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "tomlkit":
            raise ModuleNotFoundError("No module named 'tomlkit'")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)


class TestTheUnguardedPaths:
    """Both reach `_dump` without `_round_trips` in front of them."""

    def test_a_brand_new_file_keeps_an_unknown_section(self, tmp_path):
        C.save(tmp_path, _cfg())
        assert C.load(tmp_path)["codec"] == _cfg()["codec"]

    def test_an_existing_file_keeps_it_without_tomlkit(
        self, tmp_path, no_tomlkit
    ):
        (tmp_path / C.FILENAME).write_text(
            '[project]\nname = "p"\nversion = "0.1.0"\n'
        )
        C.save(tmp_path, _cfg())
        assert C.load(tmp_path)["codec"] == _cfg()["codec"]

    def test_every_entry_survives_not_just_the_table(self, tmp_path):
        """A table that comes back with the right name and half its rows is
        the worse outcome — it looks like it worked."""
        C.save(tmp_path, _cfg())
        got = C.load(tmp_path)["codec"]["blue_keyword"]["entries"]
        assert len(got) == 3
        assert got == CODEC["blue_keyword"]["entries"]

    def test_the_bool_stays_a_bool(self, tmp_path):
        """`scalar_collapse = true`, not `"true"` — the scalar branch quotes
        everything it does not recognise, which is how the list half broke."""
        C.save(tmp_path, _cfg())
        got = C.load(tmp_path)["codec"]["blue_keyword"]
        assert got["scalar_collapse"] is True


class TestTheArrayBranch:
    """The half gh-773 tripped over."""

    def test_a_list_valued_project_key_is_a_toml_array(self, tmp_path):
        cfg = {
            "project": {
                "name": "p",
                "version": "0.1.0",
                "c_format_command": ["uvx", "clang-format==22.1.8"],
            }
        }
        C.save(tmp_path, cfg)
        text = (tmp_path / C.FILENAME).read_text()
        assert 'c_format_command = ["uvx", "clang-format==22.1.8"]' in text
        assert C.load(tmp_path)["project"]["c_format_command"] == [
            "uvx",
            "clang-format==22.1.8",
        ]

    def test_a_key_the_name_list_never_had_round_trips(self, tmp_path):
        """The point of testing the value instead of its name: a key nobody
        registered behaves the same as one somebody did."""
        cfg = {
            "project": {
                "name": "p",
                "version": "0.1.0",
                "status_allow": ["zensical.toml"],
            }
        }
        C.save(tmp_path, cfg)
        assert C.load(tmp_path)["project"]["status_allow"] == ["zensical.toml"]


class TestItDoesNotDisturbWhatItKnows:
    def test_a_known_section_is_not_emitted_twice(self, tmp_path):
        """The generic tail appends only what did *not* survive the parse, so
        a section `_dump` already renders must not gain a second copy."""
        cfg = _cfg()
        cfg["module"] = {"dsp": {"objects": ["fir"]}}
        C.save(tmp_path, cfg)
        text = (tmp_path / C.FILENAME).read_text()
        assert text.count("[module.dsp]") == 1
        assert text.count("[codec.blue_keyword]") == 1

    def test_saving_twice_is_stable(self, tmp_path):
        C.save(tmp_path, _cfg())
        first = (tmp_path / C.FILENAME).read_text()
        C.save(tmp_path, C.load(tmp_path))
        assert C.load(tmp_path)["codec"] == _cfg()["codec"]
        assert "[codec.blue_keyword]" in first


class TestTheLayoutMigrations:
    """`jm migrate` / `jm split-objects` rebuild the root manifest from
    scratch, so a section either survives that rewrite or is gone before any
    gate can see it.

    The `keep` list is now subtractive — whatever was not relocated. Written
    additively it had to stay in step with the *exclusion* list that decides
    what gets relocated, two lists in different functions with nothing
    checking they agree.

    Worth recording what these tests found, because it was not what the
    reading predicted: `[[enum]]` was never at risk (`_dump` has an explicit
    branch for it, so it round-trips through the fragment), and `[codec.X]`
    was lost for the reason above — not because `keep` stranded it, but
    because it *was* relocated into a fragment `_dump` then wrote empty.
    """

    MANIFEST = """[project]
name = "p"
version = "0.1.0"

[[enum]]
name = "stype"
values = ["SC", "SI", "SL"]

[codec.blue_keyword]
discriminant = "char"
entries = [{ code = "A", ctype = "char" }, { code = "D", ctype = "double" }]

[fir]
arg_type = "float _Complex"

[module.dsp]
objects = ["fir"]
"""

    @pytest.fixture(params=["migrate", "split_objects"])
    def migrated(self, request, tmp_path, capsys):
        from just_makeit import _migrate, _split_objects

        (tmp_path / C.FILENAME).write_text(self.MANIFEST)
        {"migrate": _migrate, "split_objects": _split_objects}[
            request.param
        ].run(tmp_path)
        capsys.readouterr()
        return C.load(tmp_path)

    def test_the_codec_table_survives(self, migrated):
        entries = migrated["codec"]["blue_keyword"]["entries"]
        assert len(entries) == 2
        assert entries[0]["code"] == "A"

    def test_the_enum_ssot_survives(self, migrated):
        """gh-285: list order *is* the C enum value. Losing the table
        reintroduces the four-way string duplication it exists to prevent."""
        assert migrated["enum"] == [
            {"name": "stype", "values": ["SC", "SI", "SL"]}
        ]

    def test_the_relocated_sections_still_load(self, migrated):
        assert "fir" in migrated
        assert migrated["module"]["dsp"]["objects"] == ["fir"]

    def test_project_survives(self, migrated):
        assert migrated["project"]["name"] == "p"


class TestTheGenericRenderer:
    """`_dump_generic` has to guess a shape it was not told about, so each
    shape it can be handed is worth pinning."""

    def test_a_table_of_tables_round_trips(self, tmp_path):
        """`[codec.X]`'s shape, and the one that matters in practice: the
        component loop does not claim it, so nothing is written at all and the
        generic tail can append it cleanly."""
        cfg = {
            "project": {"name": "p", "version": "0.1.0"},
            "codec": {"a": {"kind": "pack"}, "b": {"kind": "raw", "n": 2}},
        }
        C.save(tmp_path, cfg)
        assert C.load(tmp_path)["codec"] == cfg["codec"]

    def test_a_quote_in_a_value_is_escaped(self, tmp_path):
        """The scalar branch builds the literal by hand, so it owns the
        escaping — an unescaped quote produces a file that will not parse."""
        cfg = {
            "project": {"name": "p", "version": "0.1.0"},
            "codec": {"a": {"note": 'he said "hi"'}},
        }
        C.save(tmp_path, cfg)
        assert C.load(tmp_path)["codec"]["a"]["note"] == 'he said "hi"'


class TestPresentButWrongFailsClosed:
    """The half a presence check cannot see, and the reason these tests
    exist: `_dump`'s component loop claims any unrecognised top-level table
    and emits a bare `[name]` with none of its keys. The key *survives*,
    empty — so "is it still there?" answers yes while the content is gone.

    Appending cannot repair it (TOML forbids a second `[name]`), so `_dump`
    raises instead. A hard error beats a manifest that silently lost a
    section, which is the whole of gh-763.
    """

    def test_an_unrenderable_table_raises_rather_than_writing(self):
        with pytest.raises(ValueError, match="cannot faithfully serialise"):
            C._dump({"project": {"name": "p"}, "tuning": {"depth": 4}})

    def test_the_message_names_the_section(self):
        with pytest.raises(ValueError, match=r"\[tuning\]"):
            C._dump({"project": {"name": "p"}, "tuning": {"depth": 4}})

    def test_nothing_is_written_when_it_raises(self, tmp_path):
        """The point of raising: no half-written manifest is left behind."""
        with pytest.raises(ValueError):
            C.save(tmp_path, {"project": {"name": "p"}, "tuning": {"d": 4}})
        assert not (tmp_path / C.FILENAME).exists()

    def test_the_guarded_path_still_writes_it_faithfully(self, tmp_path):
        """`_dump` raising is not the same as `save` failing. With a file
        already on disk and tomlkit available, `_round_trips` sends this to
        the tomlkit writer, which renders the section correctly — the raise
        is a backstop for the paths that have no such fallback, not a new
        restriction on what a manifest may contain."""
        (tmp_path / C.FILENAME).write_text('[project]\nname = "p"\n')
        C.save(tmp_path, {"project": {"name": "p"}, "tuning": {"depth": 4}})
        assert C.load(tmp_path)["tuning"] == {"depth": 4}

    def test_a_faithful_config_still_writes(self, tmp_path):
        """Guard: the raise must not fire on a normal manifest, or it would
        break every command."""
        C.save(tmp_path, {"project": {"name": "p", "version": "0.1.0"}})
        assert C.load(tmp_path)["project"]["name"] == "p"
