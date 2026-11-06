"""gh-2440: a public reader of the merged manifest, in Python and on the CLI.

Under the fragment layout the root `just-makeit.toml` holds `[project]` and
an `include` list; every `[module.X]` lives in `modules/*.toml`. doppler's
scripts parsed the root with `tomllib`, found no modules, and would have
passed on nothing; their only fix was the private `_config.load`.

The contract held here, on a fragment-layout project built by jm itself:

- the root alone has no `module` table (so the fixture is the layout that
  broke doppler, and a root-only reader cannot pass),
- `just_makeit.load_manifest(root)` returns the fragment's module, and is
  exactly the merged manifest `apply` reads (`_config.load`),
- `just-makeit manifest --json` prints that same dict,
- the returned dict is the caller's to mutate,
- `migrate-to-fragments` and `split-objects` tell the author to switch.
- a manifest refusal reaches a library caller as a `Refusal` naming every
  problem, with nothing printed, while the CLI (and `apply`'s own handlers)
  still print one `error:` line per problem and exit 1.
"""

import json
import sys

from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from _jmrun import run_cli

import just_makeit
from just_makeit import _config as C
from just_makeit._migrate import READER_NOTE
from just_makeit._config import tomllib
from just_makeit._report import Refusal


def _ok(*args: str, cwd: Path) -> str:
    res = run_cli(*args, cwd=cwd)
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout


@pytest.fixture(scope="module")
def proj(tmp_path_factory) -> Path:
    """`jm new` (fragment layout), a module, and an object in it."""
    base = tmp_path_factory.mktemp("gh2440")
    _ok("new", "p", cwd=base)
    root = base / "p"
    _ok("module", "filt", cwd=root)
    _ok("object", "eng", "--module", "filt", cwd=root)
    return root


def test_the_root_alone_has_no_module(proj):
    """Arms the gate: a reader of the root alone sees no module here."""
    with (proj / C.FILENAME).open("rb") as f:
        root_only = tomllib.load(f)
    assert "include" in root_only
    assert "module" not in root_only
    assert (proj / "modules" / "filt.toml").is_file()


def test_load_manifest_merges_the_fragments(proj):
    cfg = just_makeit.load_manifest(proj)
    assert "filt" in cfg["module"]
    assert "eng" in cfg["module"]["filt"]["objects"]
    assert "eng" in cfg
    assert cfg == C.load(proj), "not the manifest `apply` reads"


def test_manifest_json_is_load_manifest(proj):
    out = _ok("manifest", "--json", cwd=proj)
    from_cli = json.loads(out)
    assert "filt" in from_cli["module"]
    assert from_cli == just_makeit.load_manifest(proj)


def test_the_result_is_the_callers(proj):
    cfg = just_makeit.load_manifest(proj)
    cfg["module"].clear()
    cfg["project"]["name"] = "changed"
    again = just_makeit.load_manifest(proj)
    assert "filt" in again["module"]
    assert again["project"]["name"] == "p"


def test_no_manifest_is_an_error_not_an_empty_dict(tmp_path):
    with pytest.raises(FileNotFoundError):
        just_makeit.load_manifest(tmp_path)
    res = run_cli("manifest", "--json", cwd=tmp_path)
    assert res.returncode == 1
    assert res.stdout == ""


def test_manifest_requires_json(proj):
    res = run_cli("manifest", cwd=proj)
    assert res.returncode == 2
    assert res.stdout == ""


@pytest.mark.parametrize("cmd", ["migrate-to-fragments", "split-objects"])
def test_a_migration_points_at_the_reader(cmd, tmp_path):
    _ok("new", "p", "--no-fragments", "--object", "eng", cwd=tmp_path)
    root = tmp_path / "p"
    out = _ok(cmd, cwd=root)
    assert READER_NOTE in out
    assert "eng" in just_makeit.load_manifest(root)
    # Nothing to move: nothing to say.
    assert READER_NOTE not in _ok(cmd, cwd=root)


@pytest.fixture
def refused(tmp_path) -> Path:
    """A fragment project whose two modules each name a platform jm does
    not know: two problems `load` reports through `_config._refuse`."""
    _ok("new", "p", cwd=tmp_path)
    root = tmp_path / "p"
    for mod in ("alpha", "beta"):
        _ok("module", mod, cwd=root)
    for mod in ("alpha", "beta"):
        frag = root / "modules" / f"{mod}.toml"
        frag.write_text(
            frag.read_text(encoding="utf-8") + 'platforms = ["plan9"]\n',
            encoding="utf-8",
        )
    return root


def test_a_refusal_is_an_exception_not_an_exit(refused, capsys):
    """A library caller gets a Refusal naming every problem, and nothing
    on stderr: `_refuse` used to print each one and raise SystemExit."""
    capsys.readouterr()
    with pytest.raises(Refusal) as info:
        just_makeit.load_manifest(refused)
    with pytest.raises(Refusal):
        C.load(refused)
    errors = info.value.errors
    assert len(errors) == 2, errors
    assert any("[module.alpha]" in e for e in errors), errors
    assert any("[module.beta]" in e for e in errors), errors
    assert str(info.value) == "\n".join(errors)
    assert capsys.readouterr().err == ""


def test_the_cli_prints_every_error_line(refused):
    """The command line still reads one `error:` line per problem, exit 1."""
    res = run_cli("manifest", "--json", cwd=refused)
    assert res.returncode == 1, res.stdout + res.stderr
    lines = res.stderr.strip().splitlines()
    assert len(lines) == 2, res.stderr
    assert all(ln.startswith("error: ") for ln in lines), res.stderr
    assert "[module.alpha]" in lines[0] or "[module.alpha]" in lines[1]
    assert res.stdout == ""


@pytest.mark.parametrize("seam", ["replay_project", "_owned_fragments"])
def test_apply_prints_every_error_a_refusal_carries(
    seam, monkeypatch, tmp_path
):
    """`apply`'s own handlers print a caught refusal as the CLI does: one
    `error:` line per problem, exit 1."""
    from just_makeit import _apply

    _ok("new", "p", cwd=tmp_path)
    root = tmp_path / "p"

    def _refuses(*_a, **_k):
        raise Refusal.of(["gh2440 one", "gh2440 two"])

    monkeypatch.setattr(_apply, seam, _refuses)
    res = run_cli("apply", cwd=root)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "error: gh2440 one\nerror: gh2440 two" in res.stderr, res.stderr


def test_a_toml_datetime_prints_as_rfc_3339(tmp_path):
    """`manifest --json` writes a TOML date-time with the `T`, not as the
    `str()` of a Python datetime."""
    _ok("new", "p", cwd=tmp_path)
    root = tmp_path / "p"
    manifest = root / C.FILENAME
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        text.replace(
            "[project]\n", "[project]\nreleased = 2024-01-02T03:04:05Z\n"
        ),
        encoding="utf-8",
    )
    out = _ok("manifest", "--json", cwd=root)
    assert (
        json.loads(out)["project"]["released"] == "2024-01-02T03:04:05+00:00"
    )
