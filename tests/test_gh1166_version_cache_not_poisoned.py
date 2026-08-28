"""gh-1166: a failed version lookup must not be memoised.

gh-764 made ``just_makeit.__version__`` lazy, because
``importlib.metadata.version()`` cost ~25.5 ms of the CLI's ~29 ms import and
every invocation paid it. PEP 562's module ``__getattr__`` runs only for names
not in the module namespace, so the first access caches into ``globals()`` and
every later one is a plain attribute lookup. That part is right and stays.

What was wrong is WHAT it cached. The fallback was memoised too, and the cache
lives for the whole process with no invalidation -- so a transient failure
became permanent. Two ways that happens:

* metadata genuinely not resolvable yet (an editable install mid-sync, a
  frozen build), where the next call would have succeeded;
* a test that patches ``importlib.metadata.version``, if any test happens to
  take the FIRST look at ``__version__`` inside that window.

The second is what surfaced it. ``test_gh764_apply_performance``'s
``test_version_still_resolves`` asserts
``just_makeit.__version__ == C.jm_cli_version()``, and failed under some
random orderings and passed under others -- because ``jm_cli_version()``
re-imports and gets the real value, while the poisoned cache still held
``"unknown"``. It passed in isolation, which is what made it read as a flake.

Fixing the ordering (pinning the seed, or clearing the cache in the patching
test's teardown) would hide it. The cache is the bug: it is real state with no
invalidation, and the fix is to only ever memoise a value worth keeping.
"""

from __future__ import annotations

import importlib
import importlib.metadata

import pytest


@pytest.fixture
def fresh():
    """``just_makeit`` with no ``__version__`` cached, restored afterwards.

    The cache is module state, so a test that fills it would leak into every
    later test in the session -- which is the very failure this file is about.
    """
    import just_makeit

    had = "__version__" in just_makeit.__dict__
    saved = just_makeit.__dict__.get("__version__")
    just_makeit.__dict__.pop("__version__", None)
    try:
        yield just_makeit
    finally:
        just_makeit.__dict__.pop("__version__", None)
        if had:
            just_makeit.__dict__["__version__"] = saved


class TestTheFallbackIsNotCached:
    def test_a_failed_lookup_leaves_no_cache_behind(
        self, fresh, monkeypatch
    ) -> None:
        """The whole fix, in one assertion: after a failure the name is still
        absent from the module dict, so the NEXT access asks again."""

        def _boom(_name):
            raise importlib.metadata.PackageNotFoundError

        monkeypatch.setattr(importlib.metadata, "version", _boom)
        assert fresh.__version__ == "unknown"
        assert "__version__" not in fresh.__dict__, (
            "the fallback was memoised; every later reader in this process "
            "now disagrees with importlib.metadata"
        )

    def test_it_recovers_once_the_lookup_works_again(
        self, fresh, monkeypatch
    ) -> None:
        """The poisoning scenario end to end: fail, then succeed, and the
        second answer must be the real one rather than the cached failure.

        Both halves are patched rather than letting the second read the real
        metadata. Whether `importlib.metadata.version("just-makeit")` resolves
        is a fact about the ENVIRONMENT -- an editable install, a wheel, a
        PYTHONPATH-only checkout all differ -- and a test that depends on it
        passes here and fails on a runner that installs differently. The
        mechanism is what is under test, so the mechanism is what is faked.
        """

        def _boom(_name):
            raise importlib.metadata.PackageNotFoundError

        monkeypatch.setattr(importlib.metadata, "version", _boom)
        assert fresh.__version__ == "unknown"

        monkeypatch.setattr(importlib.metadata, "version", lambda _n: "9.9.9")
        assert fresh.__version__ == "9.9.9", (
            "the failure was cached, so the recovered lookup never ran"
        )

    def test_the_two_readers_agree(self, fresh) -> None:
        """`__version__` and `_config.jm_cli_version()` are two readers of one
        fact. gh-764 is only safe while they cannot diverge.

        Asserted as equality, not against a literal: whatever the environment
        resolves (or fails to), both must report it.
        """
        from just_makeit import _config as C

        assert fresh.__version__ == C.jm_cli_version()


class TestTheCacheStillWorks:
    """gh-764's saving is the reason any of this exists; keep it."""

    def test_a_successful_lookup_is_memoised(self, fresh, monkeypatch) -> None:
        """Patched to succeed: whether the real metadata resolves depends on
        how the package was installed, and this is about the CACHE."""
        monkeypatch.setattr(importlib.metadata, "version", lambda _n: "9.9.9")
        assert "__version__" not in fresh.__dict__
        resolved = fresh.__version__
        assert resolved == "9.9.9"
        assert fresh.__dict__.get("__version__") == resolved

        # With the value cached, `__getattr__` must not run again -- so a
        # lookup that would now raise is never reached.
        def _boom(_name):  # pragma: no cover - must not be called
            raise AssertionError("__getattr__ ran despite a cached value")

        monkeypatch.setattr(importlib.metadata, "version", _boom)
        assert fresh.__version__ == resolved

    def test_it_is_still_discoverable_and_importable(self, fresh) -> None:
        assert "__version__" in dir(fresh)
        from just_makeit import __version__

        # Truthy, not a specific value: "unknown" is a legitimate answer in an
        # environment where the metadata is not installed, and gh-764's own
        # test asserts exactly this much.
        assert __version__

    def test_an_unknown_attribute_still_raises(self, fresh) -> None:
        with pytest.raises(AttributeError):
            fresh.zz_not_a_real_attribute
