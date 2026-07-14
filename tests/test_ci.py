"""Integration tests for `just-makeit ci`."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._ci import run as ci_run


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    dest = tmp_path / "dsp"
    new_run("dsp", dest)
    object_run(
        dest, "eng", module=None, state_vars=[("gain", "double", "1.0")]
    )
    return dest


@pytest.fixture()
def pytest_project(tmp_path: Path) -> Path:
    dest = tmp_path / "pdsp"
    new_run("pdsp", dest, pytest_=True)
    object_run(
        dest, "eng", module=None, state_vars=[("gain", "double", "1.0")]
    )
    return dest


class TestCi:
    def test_github_creates_workflow(self, project):
        ci_run(project, provider="github")
        wf = project / ".github" / "workflows" / "ci.yml"
        assert wf.exists()
        text = wf.read_text(encoding="utf-8")
        assert "run: make" in text and "run: make test" in text
        assert "ubuntu-latest" in text and "macos-latest" in text
        assert "ubuntu-24.04-arm" in text
        # No unsubstituted placeholders.
        assert "<<" not in text

    def test_unittest_project_omits_pytest(self, project):
        ci_run(project, provider="github")
        text = (project / ".github" / "workflows" / "ci.yml").read_text()
        assert "numpy" in text
        assert "pytest" not in text

    def test_pytest_project_includes_pytest(self, pytest_project):
        ci_run(pytest_project, provider="github")
        text = (
            pytest_project / ".github" / "workflows" / "ci.yml"
        ).read_text()
        assert "numpy pytest" in text

    def test_woodpecker_provider(self, project):
        ci_run(project, provider="woodpecker")
        wf = project / ".woodpecker.yml"
        assert wf.exists()
        text = wf.read_text(encoding="utf-8")
        assert "make test" in text
        assert "<<" not in text

    def test_does_not_clobber_without_force(self, project, capsys):
        wf = project / ".github" / "workflows" / "ci.yml"
        ci_run(project, provider="github")
        wf.write_text("# hand-tuned\n", encoding="utf-8")
        ci_run(project, provider="github")  # second run, no --force
        assert wf.read_text(encoding="utf-8") == "# hand-tuned\n"
        assert "already exists" in capsys.readouterr().out

    def test_force_overwrites(self, project):
        wf = project / ".github" / "workflows" / "ci.yml"
        ci_run(project, provider="github")
        wf.write_text("# hand-tuned\n", encoding="utf-8")
        ci_run(project, provider="github", force=True)
        assert "make test" in wf.read_text(encoding="utf-8")

    def test_unknown_provider_exits(self, project):
        with pytest.raises(SystemExit):
            ci_run(project, provider="gitlab")

    def test_no_manifest_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            ci_run(tmp_path, provider="github")
