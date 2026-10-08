"""gh-2064: `jm module --package` declares `[module.X] package` up front.

``[module.X] package`` (gh-523) had no CLI flag. The documented route was
`jm module mod`, then adding ``package = "other"`` to the manifest. By then
`jm module` had already written ``src/<pkg>/mod/__init__.py`` and
``mod.pyi`` for the module id. `apply` wrote the module into ``other/`` and
left those two files behind, owned by nothing, and `status` did not report
them. `jm script` could not replay the key either: it printed a NOTE asking
the reader to add it back by hand, which is the same route.

`jm module <id> --package <dir>` now writes the key before anything else is
written, and `jm script` replays it as that flag. The key set by hand on a
module that already exists still leaves the old directory behind; that is
gh-2081, ratcheted in `test_gh2057_verb_leaves_what_apply_writes.py`.

GATE: `jm module mod --package other` then `jm object o --module mod`
      writes nothing under ``src/<pkg>/mod/``, `status --check` exits 0, and
      `jm script` replays the key.
"""

from __future__ import annotations

from _jmrun import run_cli, script_round_trip
from just_makeit import _config as C


def test_the_flag_declares_the_package_before_anything_is_written(tmp_path):
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    for argv in (
        ("module", "mod", "--package", "other"),
        ("object", "o", "--module", "mod"),
    ):
        r = run_cli(*argv, cwd=root)
        assert r.returncode == 0, (argv, r.stderr)

    assert C.module_package(C.load(root), "mod") == "other"
    assert (root / "src" / "p" / "other" / "mod.pyi").is_file()
    assert not (root / "src" / "p" / "mod").exists(), sorted(
        p.relative_to(root).as_posix()
        for p in (root / "src" / "p" / "mod").rglob("*")
    )
    s = run_cli("status", "--check", cwd=root)
    assert s.returncode == 0, s.stdout

    script = run_cli("script", cwd=root).stdout
    assert "--package other" in script
    # The hand-edit NOTE it replaces would send the reader down gh-2081.
    assert "# NOTE: [module.mod] package" not in script
    orig, replay = script_round_trip(root, tmp_path / "replay")
    assert orig == replay
