"""gh-1489: the scaffolded Python test is jm's until its author says otherwise.

`jm new` / `jm object` write ``src/<pkg>/tests/test_<comp>.py`` against the
constructor as it is then. The file was create-only, so an init param added
later left it calling the old signature -- 5 of 5 tests failing with
`TypeError` / `NameError` -- and `status` could not say so, because a file
the author owns is expected to differ from its render.

The fix reuses gh-1448's ownership token rather than a second provenance
mechanism: the test is born carrying ``# jm:generated test_<comp>.py``.
While it does, `apply` renders the file whole and `status` sees its drift;
deleting the line makes the file the author's for good. A project scaffolded
before the token existed has none, and nothing touches its tests.

What `apply` will not do is delete a test FUNCTION it does not render: that
is most likely one the author added without deleting the token, so it
refuses and names both ways out.

GATE: a scaffolded Python test carrying jm's ownership token follows the
manifest's constructor; without the token it is never written.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from _jmrun import run_cli


TOKEN = "# jm:generated test_g.py"

INIT_PARAM = (
    '\n[[g.init_params]]\nname = "level"\ntype = "int"\ndefault = "3"\n'
)

_NO_TOOLCHAIN = shutil.which("cmake") is None or (
    shutil.which("cc") is None and shutil.which("gcc") is None
)


def _project(tmp_path: Path, *, module: bool = False) -> Path:
    """A fresh project with one object ``g``, standalone or in module ``m``."""
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    args = ["object", "g", "--no-step", "--state", "k:int:7"]
    if module:
        assert run_cli("module", "m", cwd=root).returncode == 0
        args += ["--module", "m"]
    r = run_cli(*args, cwd=root)
    assert r.returncode == 0, r.stderr
    return root


def _test_path(root: Path, module: bool) -> Path:
    base = root / "src" / "p" / ("m" if module else "")
    return base / "tests" / "test_g.py"


def _add_init_param(root: Path) -> None:
    """Append an init param to wherever the object's table lives."""
    frag = root / "objects" / "g.toml"
    target = frag if frag.is_file() else root / "just-makeit.toml"
    target.write_text(
        target.read_text(encoding="utf-8") + INIT_PARAM, encoding="utf-8"
    )


def _ctor_calls(text: str) -> set:
    return set(re.findall(r"\bG\(([^)]*)\)", text))


@pytest.mark.parametrize("module", [False, True], ids=["standalone", "module"])
class TestOwned:
    def test_a_fresh_scaffold_carries_the_token(self, tmp_path, module):
        root = _project(tmp_path, module=module)
        first = _test_path(root, module).read_text().splitlines()[0]
        assert first == TOKEN

    def test_a_new_init_param_reaches_the_test(self, tmp_path, module):
        """The issue's symptom: the test kept calling the old constructor."""
        root = _project(tmp_path, module=module)
        before = _ctor_calls(_test_path(root, module).read_text())
        _add_init_param(root)
        r = run_cli("apply", cwd=root)
        assert r.returncode == 0, r.stderr
        after = _test_path(root, module).read_text()
        assert after.splitlines()[0] == TOKEN
        assert "G(level=3)" in after, after
        assert _ctor_calls(after) != before

    def test_a_second_apply_writes_nothing(self, tmp_path, module):
        root = _project(tmp_path, module=module)
        _add_init_param(root)
        assert run_cli("apply", cwd=root).returncode == 0
        path = _test_path(root, module)
        text = path.read_bytes()
        r = run_cli("apply", cwd=root)
        assert r.returncode == 0
        assert path.read_bytes() == text
        assert "tests/test_g.py" not in r.stdout, r.stdout

    def test_status_sees_drift_in_an_owned_test(self, tmp_path, module):
        root = _project(tmp_path, module=module)
        assert run_cli("status", "--check", cwd=root).returncode == 0
        path = _test_path(root, module)
        path.write_text(path.read_text() + "\n# drift\n")
        assert run_cli("status", "--check", cwd=root).returncode != 0
        r = run_cli("status", cwd=root)
        assert re.search(
            r"^  ~ src/p/(m/)?tests/test_g\.py$", r.stdout, re.M
        ), r.stdout

    def test_deleting_the_token_hands_it_over(self, tmp_path, module):
        root = _project(tmp_path, module=module)
        path = _test_path(root, module)
        mine = path.read_text().replace(TOKEN + "\n", "", 1)
        mine += "\n\ndef test_mine():\n    assert True\n"
        path.write_text(mine)
        _add_init_param(root)
        assert run_cli("apply", cwd=root).returncode == 0
        assert path.read_text() == mine
        # A module object's header is not reconciled (gh-627), so `--check`
        # also reports a CTOR finding there; only the test file is asked.
        assert "tests/test_g.py" not in run_cli("status", cwd=root).stdout

    def test_a_test_only_on_disk_refuses(self, tmp_path, module):
        """An owned file gaining a test jm does not render: apply would
        delete it, so it refuses, writes nothing, and names the way out."""
        root = _project(tmp_path, module=module)
        path = _test_path(root, module)
        added = path.read_text() + "\n\ndef test_mine():\n    assert True\n"
        path.write_text(added)
        _add_init_param(root)
        r = run_cli("apply", cwd=root)
        assert r.returncode != 0
        assert "only here: test_mine" in r.stderr + r.stdout
        assert "# jm:generated" in r.stderr + r.stdout
        assert path.read_text() == added


def test_an_untokened_test_is_never_written(tmp_path):
    """A project scaffolded before gh-1489: its test has no token, and stays
    exactly as it is whatever the constructor does."""
    root = _project(tmp_path)
    path = _test_path(root, False)
    old = path.read_text().replace(
        TOKEN + "\n", "", 1
    )  # what jm <= 0.87 wrote
    path.write_text(old)
    _add_init_param(root)
    assert run_cli("apply", cwd=root).returncode == 0
    assert path.read_text() == old


def test_a_neighbours_token_does_not_count(tmp_path):
    """A test copied from a sibling carries the sibling's name: not evidence
    that jm rendered THIS file, the same rule as a copied fragment."""
    root = _project(tmp_path)
    path = _test_path(root, False)
    copied = path.read_text().replace(TOKEN, "# jm:generated test_h.py", 1)
    path.write_text(copied)
    _add_init_param(root)
    assert run_cli("apply", cwd=root).returncode == 0
    assert path.read_text() == copied


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
@pytest.mark.parametrize("module", [False, True], ids=["standalone", "module"])
def test_the_rerendered_suite_passes(tmp_path, module):
    """The whole point, against a real extension: after the constructor
    gains a parameter, the generated suite runs green with nothing deleted."""
    from test_gh1109_seeded_construction_is_attempted import _pytest_counts

    root = _project(tmp_path, module=module)
    if module:
        # A module object's binding fragment is the author's by default
        # (gh-1448), so a new init param would reach the test but not the
        # binding. Adopting it is how a module object follows its manifest.
        assert run_cli("adopt", "g", cwd=root).returncode == 0
    _add_init_param(root)
    assert run_cli("apply", cwd=root).returncode == 0
    out = run_cli("test", cwd=root)
    assert out.returncode == 0, out.stdout + out.stderr
    counts = _pytest_counts(out.stdout)
    assert counts.get("passed", 0) >= 5, out.stdout
    assert counts.get("failed", 0) == 0, out.stdout
    assert counts.get("skipped", 0) == 0, out.stdout
