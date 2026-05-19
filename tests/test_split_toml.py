"""Integration tests for split per-object TOML support (schema 6).

Covers `_config.load()` resolving the `include` key and the
`jm apply <fragment>` compose path.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._new import run as new_run


_AGC_FRAGMENT = """\
[agc]
arg_type = "float _Complex"
return_type = "float _Complex"
mutable = "false"
no_state = "false"
no_step = "false"

[[agc.state]]
name = "ref_db"
type = "double"
default = "0.0"
"""

_MIXER_FRAGMENT = """\
[mixer]
arg_type = "float _Complex"
return_type = "float _Complex"
mutable = "false"
no_state = "false"
no_step = "false"
"""


@pytest.fixture()
def bare_project(tmp_path):
    """A scaffolded project with no objects yet."""
    root = tmp_path / "proj"
    new_run("proj", root)
    return root


def _add_include(manifest: Path, line: str) -> None:
    """Prepend a top-level include line to the manifest."""
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(f"{line}\n\n{text}", encoding="utf-8")


class TestLoadResolvesIncludes:
    def test_glob_include_merges_fragment(self, bare_project):
        (bare_project / "objects").mkdir()
        (bare_project / "objects" / "agc.toml").write_text(_AGC_FRAGMENT)
        _add_include(bare_project / C.FILENAME, 'include = ["objects/*.toml"]')
        cfg = C.load(bare_project)
        assert "agc" in C.components(cfg)
        assert C.state_vars(cfg, "agc") == [("ref_db", "double", "0.0")]

    def test_explicit_path_include(self, bare_project):
        (bare_project / "fragments").mkdir()
        (bare_project / "fragments" / "agc.toml").write_text(_AGC_FRAGMENT)
        _add_include(
            bare_project / C.FILENAME,
            'include = ["fragments/agc.toml"]',
        )
        assert "agc" in C.components(C.load(bare_project))

    def test_missing_explicit_path_errors(self, bare_project):
        _add_include(
            bare_project / C.FILENAME, 'include = ["objects/missing.toml"]'
        )
        with pytest.raises(FileNotFoundError):
            C.load(bare_project)

    def test_duplicate_object_across_fragments_errors(self, bare_project):
        (bare_project / "objects").mkdir()
        (bare_project / "objects" / "a.toml").write_text(_AGC_FRAGMENT)
        (bare_project / "objects" / "b.toml").write_text(_AGC_FRAGMENT)
        _add_include(bare_project / C.FILENAME, 'include = ["objects/*.toml"]')
        with pytest.raises(ValueError, match="agc"):
            C.load(bare_project)

    def test_project_in_fragment_errors(self, bare_project):
        (bare_project / "objects").mkdir()
        (bare_project / "objects" / "bad.toml").write_text(
            '[project]\nname = "x"\nversion = "0.1.0"\n'
        )
        _add_include(bare_project / C.FILENAME, 'include = ["objects/*.toml"]')
        with pytest.raises(ValueError, match=r"\[project\]"):
            C.load(bare_project)

    def test_load_manifest_skips_includes(self, bare_project):
        """load_manifest reads the manifest only — no merge."""
        (bare_project / "objects").mkdir()
        (bare_project / "objects" / "agc.toml").write_text(_AGC_FRAGMENT)
        _add_include(bare_project / C.FILENAME, 'include = ["objects/*.toml"]')
        manifest = C.load_manifest(bare_project)
        assert "agc" not in manifest

    def test_no_include_unchanged_behavior(self, bare_project):
        """A single-file project (no include key) loads exactly as before."""
        cfg = C.load(bare_project)
        assert C.project_name(cfg) == "proj"
        assert "include" not in C.load_manifest(bare_project)


class TestApplyCompose:
    def test_compose_copies_fragment_and_updates_manifest(
        self, bare_project, tmp_path
    ):
        external = tmp_path / "agc.toml"
        external.write_text(_AGC_FRAGMENT)
        apply_run(bare_project, fragment=external)

        assert (bare_project / "objects" / "agc.toml").exists()
        assert "include" in C.load_manifest(bare_project)
        assert "agc" in C.components(C.load(bare_project))
        # Materialization actually ran.
        assert (bare_project / "native" / "inc" / "agc").is_dir()

    def test_compose_idempotent_include(self, bare_project, tmp_path):
        """Adding a second fragment doesn't duplicate the include line."""
        e1 = tmp_path / "agc.toml"
        e1.write_text(_AGC_FRAGMENT)
        e2 = tmp_path / "mixer.toml"
        e2.write_text(_MIXER_FRAGMENT)
        apply_run(bare_project, fragment=e1)
        apply_run(bare_project, fragment=e2)

        manifest_text = (bare_project / C.FILENAME).read_text(encoding="utf-8")
        assert manifest_text.count("include = ") == 1

    def test_compose_conflict_errors_with_remedy(self, bare_project, tmp_path):
        external = tmp_path / "agc.toml"
        external.write_text(_AGC_FRAGMENT)
        apply_run(bare_project, fragment=external)

        dup = tmp_path / "agc2.toml"
        dup.write_text(_AGC_FRAGMENT)
        with pytest.raises(SystemExit):
            apply_run(bare_project, fragment=dup)
