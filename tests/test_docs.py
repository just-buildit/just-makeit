"""The docs site must not drift from the examples it publishes.

This is `jm status --check` pointed at the docs. Every problem it guards
against was real, found in one audit, and had been shipping silently:

- 12 nav entries were only correct because `scripts/copy_examples.py` happened
  to list the same 12 examples. Nothing checked the two against each other.
- 8 examples had a README nobody published, so their prose existed and was
  unreachable.
- 3 hand-written pages lived in nav but not in the generator's list, so the
  index table couldn't see them.

`zensical build --strict` catches a nav entry pointing at a missing page, and
nothing else here: it is silent about a page that exists but is unreachable,
and about a README that is never published at all. That gap is this file.

The chain these tests defend is three links long:

    .steps/*.md ->(assemble.py)-> README.md ->(copy_examples.py)-> docs/examples/

`tests/test_readme_assembled.py` gates the first arrow. This file gates the
second, plus the nav that has to agree with it.
"""

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
EXAMPLES = ROOT / "src" / "just_makeit" / "examples"
MKDOCS = ROOT / "mkdocs.yml"


def _load_copy_examples():
    """Import scripts/copy_examples.py, which is a script, not a package."""
    spec = importlib.util.spec_from_file_location(
        "copy_examples", ROOT / "scripts" / "copy_examples.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["copy_examples"] = mod
    spec.loader.exec_module(mod)
    return mod


CE = _load_copy_examples()


def _nav_examples() -> dict[str, str]:
    """Map examples/<name>.md -> nav title, for the Examples nav section.

    mkdocs.yml is not valid plain YAML to a strict loader — the Material
    theme's `!!python/name:` tags appear in `markdown_extensions` — so the
    unknown tags are ignored rather than resolved. Only `nav` is read here.
    """

    class _Loader(yaml.SafeLoader):
        pass

    _Loader.add_multi_constructor(
        "tag:yaml.org,2002:python/name", lambda loader, suffix, node: None
    )
    _Loader.add_multi_constructor("!", lambda loader, suffix, node: None)

    cfg = yaml.load(MKDOCS.read_text(encoding="utf-8"), Loader=_Loader)
    for entry in cfg["nav"]:
        if isinstance(entry, dict) and "Examples" in entry:
            out = {}
            for item in entry["Examples"]:
                ((title, path),) = item.items()
                out[path] = title
            return out
    raise AssertionError("mkdocs.yml has no Examples nav section")


class TestGeneratorAgreesWithDisk:
    """copy_examples.py must account for every example, both directions."""

    def test_repo_is_in_sync(self):
        problems = CE._reconcile(CE._example_dirs())
        assert problems == [], "\n".join(problems)

    def test_every_gallery_entry_has_a_readme(self):
        for name in CE.GALLERY:
            assert (EXAMPLES / name / "README.md").exists(), (
                f"{name} is in GALLERY but has no README.md to publish"
            )

    def test_unpublished_examples_are_real_and_reasoned(self):
        for name, reason in CE.UNPUBLISHED.items():
            assert (EXAMPLES / name).is_dir(), f"{name} no longer exists"
            assert reason.strip(), f"{name} is excluded without a reason"


class TestReconcileFailsLoudly:
    """The point of the rewrite: silence was the bug.

    The old generator printed `skip` and carried on when a README was
    missing, which is precisely how a nav entry ends up pointing at a page
    that was never generated. These pin the failure, not the message.
    """

    def test_new_example_with_readme_must_be_listed(self):
        dirs = dict(CE._example_dirs())
        dirs["brand_new_example"] = True
        problems = CE._reconcile(dirs)
        assert any("brand_new_example" in p for p in problems), (
            "an unlisted example with a README was published silently"
        )

    def test_example_without_readme_must_be_excused(self):
        dirs = dict(CE._example_dirs())
        dirs["undocumented_example"] = False
        problems = CE._reconcile(dirs)
        assert any("undocumented_example" in p for p in problems)

    def test_gallery_entry_losing_its_readme_is_an_error(self):
        dirs = dict(CE._example_dirs())
        dirs["fir_filter"] = False  # README deleted
        problems = CE._reconcile(dirs)
        assert any("fir_filter" in p for p in problems), (
            "a GALLERY entry with no README would generate no page, and the "
            "nav entry would 404"
        )

    def test_deleted_example_still_in_gallery_is_an_error(self):
        dirs = {k: v for k, v in CE._example_dirs().items() if k != "iqfile"}
        problems = CE._reconcile(dirs)
        assert any("iqfile" in p for p in problems)

    def test_unpublished_example_that_grows_a_readme_is_an_error(self):
        dirs = dict(CE._example_dirs())
        dirs["jm_remove"] = True  # a README appeared
        problems = CE._reconcile(dirs)
        assert any("jm_remove" in p for p in problems), (
            "prose was written but the example stayed unpublished"
        )

    def test_in_sync_layout_reports_nothing(self):
        # A layout holding exactly what the two lists claim — every gallery
        # example with its README, every excused one without — is silent,
        # without consulting the disk at all.
        dirs = {name: True for name in CE.GALLERY}
        dirs.update({name: False for name in CE.UNPUBLISHED})
        assert CE._reconcile(dirs) == []


class TestNavAgreesWithGallery:
    """A page nobody can reach from the nav is a page nobody reads."""

    def test_every_published_example_is_in_the_nav(self):
        nav = _nav_examples()
        missing = [
            name for name in CE.GALLERY if f"examples/{name}.md" not in nav
        ]
        assert missing == [], (
            f"published but unreachable from the left nav: {missing}. "
            f"Add them to the Examples section of mkdocs.yml."
        )

    def test_every_nav_entry_is_generated(self):
        nav = _nav_examples()
        generated = {f"examples/{name}.md" for name in CE.GALLERY}
        generated.add("examples/index.md")
        stale = [path for path in nav if path not in generated]
        assert stale == [], (
            f"nav points at pages copy_examples.py does not generate: "
            f"{stale}. These 404 on the built site."
        )

    def test_nav_titles_match_gallery_display_names(self):
        nav = _nav_examples()
        for name, display in CE.GALLERY.items():
            assert nav[f"examples/{name}.md"] == display, (
                f"{name}: nav says {nav[f'examples/{name}.md']!r}, "
                f"GALLERY says {display!r} — the index table and the nav "
                f"would disagree"
            )

    def test_nav_order_matches_gallery_order(self):
        nav_order = [
            path[len("examples/") : -len(".md")]
            for path in _nav_examples()
            if path != "examples/index.md"
        ]
        assert nav_order == list(CE.GALLERY), (
            "the Examples nav and the index table would list the examples "
            "in different orders"
        )


class TestPublishedPagesAreSiteShaped:
    """READMEs are copied to docs/examples/, so their links resolve there."""

    @pytest.mark.parametrize("name", sorted(CE.GALLERY))
    def test_no_source_base_links(self, name):
        text = (EXAMPLES / name / "README.md").read_text(encoding="utf-8")
        # `../<example>/README.md` resolves when browsing the repo on GitHub
        # and 404s on the site, where the page lives at docs/examples/<n>.md.
        # The site is the audience whose links get checked (--strict).
        offenders = [
            line for line in text.splitlines() if "README.md)" in line
        ]
        assert offenders == [], (
            f"{name}/README.md links to another README; on the site that "
            f"path does not exist. Link to the published page instead "
            f"(e.g. `stream_source.md`). Offending lines: {offenders}"
        )
