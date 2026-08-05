"""gh-764: `apply`/`status` were quadratic in the manifest.

Measured on doppler (2213 files, 41 modules, 69 fragment files):

| | before | after |
| --- | ---: | ---: |
| `jm apply` | 90.1 s | 7.7 s |
| `jm status --check` | 87.6 s | 8.2 s |
| one `jm property` | 0.36 s | 0.13 s |
| CLI import | 29.5 ms | 8.0 ms |

`apply`'s replay runs one mutating command per object, method, property and
function — 718 of them on doppler — and each ended by writing the *whole*
manifest: 2.48 million tomlkit ``__setitem__`` calls, 87% of the runtime.

gh-698 already found this and added `_config.scratch_writes` and
`_object.deferred_module_regen`. `scratch_writes` swaps the tomlkit round-trip
for the plain `_dump`, but only when `_round_trips` confirms `_dump` reproduced
the config — and `_dump` is not total. It drops ``[codec.X]`` and renders a
list value as its Python repr. doppler has both, so the guard rejected the fast
path on *every* save and the speedup never applied there. See gh-763.

`deferred_save` sidesteps that: one write instead of 718, so `_dump`'s
totality stops being a performance question.

**These tests are about correctness, not speed.** Nothing here asserts a
duration — a timing assertion fails on a loaded CI box and teaches nobody
anything. What they pin is that the fast path produces byte-identical output,
which is the only reason it is allowed to exist.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _status  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402


def _project(root: Path) -> None:
    """A split-layout project with enough shape to exercise the replay.

    ``fragments=True`` is what `jm new` does by default; the Python entry
    point defaults the other way. The split layout is the point here — the
    per-destination skip only has something to skip when there is more than
    one destination.
    """
    new_run("proj", root, fragments=True)
    module_run(root, "filters")
    for name in ("fir", "biquad"):
        object_run(
            root, name, "filters", state_vars=[("gain", "double", "1.0")]
        )
        method_run(
            root,
            name,
            f"{name}_ctrl",
            "filters",
            "double",
            "double",
            False,
            [],
        )
        property_run(root, name, f"{name}_level", "filters", "double", False)
    object_run(root, "solo", None, state_vars=[("rate", "double", "2.0")])


def _fragment_files(root: Path) -> list[Path]:
    return sorted(
        [*root.glob("objects/*.toml"), *root.glob("modules/*.toml")]
        + [root / C.FILENAME]
    )


def _hashes(paths: list[Path]) -> dict[Path, str]:
    return {
        p: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in paths
        if p.exists()
    }


class TestDeferredSaveIsUnobservable:
    """The whole safety argument: only the end state may differ, and it doesn't."""

    def test_a_deferred_save_reaches_disk_on_exit(self, tmp_path):
        root = tmp_path / "proj"
        _project(root)
        cfg = C.load(root)

        with C.deferred_save():
            cfg["solo"]["mutable"] = "true"
            C.save(root, cfg)
            # Still the old value on disk — that is the point.
            assert C.load_manifest(root) is not None
            on_disk_mid = _fragment_files(root)
            assert on_disk_mid  # sanity: the project exists

        assert C.load(root)["solo"]["mutable"] == "true"

    def test_load_inside_the_scope_sees_the_pending_write(self, tmp_path):
        """The invariant the replay depends on.

        `_apply._replay` calls ``C.load(temp_root)`` twice mid-replay. If those
        read the stale file instead of the pending config, every mutation
        before them is silently dropped.
        """
        root = tmp_path / "proj"
        _project(root)
        cfg = C.load(root)

        with C.deferred_save():
            cfg["solo"]["mutable"] = "true"
            C.save(root, cfg)
            assert C.load(root)["solo"]["mutable"] == "true"

    def test_load_returns_a_copy_the_caller_owns(self, tmp_path):
        """A caller that loads, mutates, then declines to save must not
        corrupt the pending write — the same contract parsing from disk has."""
        root = tmp_path / "proj"
        _project(root)
        cfg = C.load(root)

        with C.deferred_save():
            C.save(root, cfg)
            stray = C.load(root)
            stray["solo"]["mutable"] = "true"  # never saved

        assert C.load(root)["solo"].get("mutable") != "true"

    def test_the_bootstrap_save_is_not_deferred(self, tmp_path):
        """A dozen commands gate on ``(root / FILENAME).exists()`` rather than
        on a load, so the first save has to reach disk. Deferring it made
        every replay step after it exit with "no just-makeit.toml found"."""
        root = tmp_path / "fresh"
        with C.deferred_save():
            new_run("fresh", root)
            assert (root / C.FILENAME).exists()

    def test_nested_deferral_folds_into_the_outer_scope(self, tmp_path):
        root = tmp_path / "proj"
        _project(root)
        cfg = C.load(root)

        with C.deferred_save():
            with C.deferred_save():
                cfg["solo"]["mutable"] = "true"
                C.save(root, cfg)
            # Inner exit hands the write to the outer scope, not to disk.
            assert C.load(root)["solo"]["mutable"] == "true"

        assert C.load(root)["solo"]["mutable"] == "true"


class TestApplyStillAgreesWithTheRealTree:
    """The end-to-end assertion, and the strongest one available.

    The real tree is built by write-through mutating commands; the scratch
    tree `status` compares it against is built by the *deferred* replay. A
    byte comparison of the two is exactly `jm status`, so a clean status is
    proof that deferral changed nothing observable.
    """

    def test_status_is_clean_after_apply(self, tmp_path):
        root = tmp_path / "proj"
        _project(root)
        apply_run(root)
        assert _status.run(root, check=True) == 0

    def test_status_accepts_a_relative_root(self, tmp_path, monkeypatch):
        """gh-764: ``root.name`` is "" for ``Path(".")``, so the scratch path
        collapsed onto the temp dir and `copytree` raised FileExistsError."""
        root = tmp_path / "proj"
        _project(root)
        apply_run(root)
        monkeypatch.chdir(root)
        assert _status.run(Path("."), check=True) == 0


class TestUnchangedFilesAreNotRewritten:
    """`save` rewrote every destination; 69 of doppler's 70 came back identical."""

    def test_only_the_changed_fragment_is_written(self, tmp_path):
        root = tmp_path / "proj"
        _project(root)
        files = _fragment_files(root)
        assert len(files) >= 4, files  # split layout really is in play

        before = _hashes(files)
        cfg = C.load(root)
        cfg["solo"]["mutable"] = "true"
        C.save(root, cfg)
        after = _hashes(files)

        changed = [p.name for p in files if before.get(p) != after.get(p)]
        assert changed == ["solo.toml"], changed

    def test_the_untouched_destinations_are_never_opened_for_writing(
        self, tmp_path, monkeypatch
    ):
        """The one that proves the skip *fires*.

        The byte assertions above hold with or without this optimization —
        the old code rewrote all 70 destinations and they came out identical,
        which is precisely why the cost was invisible. Only counting the
        writes distinguishes "skipped" from "rewrote the same bytes".
        """
        root = tmp_path / "proj"
        _project(root)
        files = _fragment_files(root)

        written: list[str] = []
        real = Path.write_text

        def spy(self, *a, **kw):
            written.append(self.name)
            return real(self, *a, **kw)

        monkeypatch.setattr(Path, "write_text", spy)

        cfg = C.load(root)
        cfg["solo"]["mutable"] = "true"
        C.save(root, cfg)

        # `save` still *visits* every destination — the skip lives inside
        # `_write_doc` — but only the changed one is written to disk.
        assert written == ["solo.toml"], (
            f"{len(files)} destinations exist; files written: {written}"
        )

    def test_a_no_op_save_rewrites_nothing_at_all(self, tmp_path):
        root = tmp_path / "proj"
        _project(root)
        files = _fragment_files(root)

        before = _hashes(files)
        C.save(root, C.load(root))  # save exactly what is already there

        assert _hashes(files) == before

    def test_the_skip_is_byte_preserving_not_just_semantic(self, tmp_path):
        """An author's layout must survive a save that does not touch it.

        This is gh-491's guarantee reached sooner: `_sync` leaves unchanged
        keys alone to preserve formatting, and a file already parsing to *cfg*
        has no changed keys to apply.
        """
        root = tmp_path / "proj"
        _project(root)
        frag = root / "objects" / "solo.toml"
        authored = (
            "# why solo is standalone\n"
            + frag.read_text(encoding="utf-8")
            + "\n# trailing note\n"
        )
        frag.write_text(authored, encoding="utf-8")

        cfg = C.load(root)
        cfg["fir"]["mutable"] = "true"  # a *different* object
        C.save(root, cfg)

        assert frag.read_text(encoding="utf-8") == authored


class TestLazyVersion:
    """gh-764: 25.5 ms of the 29 ms CLI import, paid by every invocation."""

    def test_version_still_resolves(self):
        import just_makeit

        assert just_makeit.__version__
        assert just_makeit.__version__ == C.jm_cli_version()

    def test_from_import_still_works(self):
        from just_makeit import __version__

        assert __version__

    def test_it_is_discoverable(self):
        import just_makeit

        assert "__version__" in dir(just_makeit)

    def test_an_unknown_attribute_still_raises(self):
        import just_makeit

        try:
            just_makeit.not_a_real_attribute
        except AttributeError as e:
            assert "not_a_real_attribute" in str(e)
        else:
            raise AssertionError("expected AttributeError")

    def test_importing_the_cli_does_not_pull_in_importlib_metadata(self):
        """The actual saving — the import graph, not the call.

        Run in a subprocess: `importlib.metadata` is certainly already in
        `sys.modules` by the time this test runs.
        """
        import subprocess

        src = Path(__file__).parent.parent / "src"
        code = (
            "import sys;"
            "sys.path.insert(0, %r);" % str(src) + "import just_makeit._cli;"
            "print('importlib.metadata' in sys.modules)"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True
        )
        assert out.stdout.strip() == "False", out.stdout + out.stderr
