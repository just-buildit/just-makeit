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

# `str | None` (PEP 604) is evaluated at def-time and is 3.10+; jm supports
# 3.9, so defer annotation evaluation to keep them strings on every leg.
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

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


_NAV_ITEM = re.compile(r"^(?P<indent>\s*)-\s+(?P<title>.+?):\s+(?P<path>\S+)$")
# A bare `- path.md` with no title: the section-index page under
# `navigation.indexes` (the section title itself links to it — no "Overview"
# child). Its "title" is None.
_NAV_BARE = re.compile(r"^(?P<indent>\s*)-\s+(?P<path>\S+\.md)\s*$")
_NAV_SECTION = re.compile(r"^(?P<indent>\s*)-\s+Examples:\s*$")


def _nav_examples() -> dict[str, str | None]:
    """Map ``examples/<name>.md`` -> nav title, for the Examples nav section.

    Parsed by hand rather than with PyYAML, deliberately. PyYAML is not a
    declared dependency of this project — it is only ever present as a
    transitive one — and the CI test legs install just the package under test.
    A `import yaml` here is a collection error that fails the whole matrix,
    and the alternative (skip when unavailable) would mean this gate silently
    never runs in CI, which is the exact class of bug the file exists to stop.

    The nav is a flat list of ``- Title: path`` under ``- Examples:``, plus one
    bare ``- examples/index.md`` (the section index under `navigation.indexes`,
    mapped to a None title), so this needs no general YAML. It raises rather
    than returns empty if the section moves or the shape changes: a parser that
    quietly finds nothing would make every test below vacuously pass.
    """
    lines = MKDOCS.read_text(encoding="utf-8").splitlines()
    out: dict[str, str | None] = {}
    section_indent = None

    for line in lines:
        if section_indent is None:
            m = _NAV_SECTION.match(line)
            if m:
                section_indent = len(m.group("indent"))
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        titled = _NAV_ITEM.match(line)
        bare = None if titled else _NAV_BARE.match(line)
        hit = titled or bare
        # The section ends at the first entry indented no deeper than
        # `- Examples:` itself, i.e. the next sibling nav entry (or any line
        # that is not a nav item at all).
        if not hit or len(hit.group("indent")) <= section_indent:
            break
        out[hit.group("path")] = titled.group("title") if titled else None

    if section_indent is None:
        raise AssertionError("mkdocs.yml has no `- Examples:` nav section")
    if not out:
        raise AssertionError("the Examples nav section parsed as empty")
    return out


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


# Matches a nav leaf's path, whether titled (`- Title: path.md`) or the bare
# `navigation.indexes` section-index form (`- path.md`).
_NAV_PATH = re.compile(r"[:-]\s+(?P<path>[A-Za-z0-9_./-]+\.md)\s*$")


def _all_nav_paths() -> set[str]:
    """Every ``.md`` path referenced anywhere in the nav, docs-relative.

    Same hand-parse rationale as `_nav_examples`: no PyYAML dependency. Every
    nav leaf is ``- Title: some/path.md``; section headers (``- Guides:``)
    carry no ``.md`` and are skipped.
    """
    paths = set()
    in_nav = False
    for line in MKDOCS.read_text(encoding="utf-8").splitlines():
        if line.startswith("nav:"):
            in_nav = True
            continue
        if in_nav and line and not line[0].isspace():
            break  # left the nav block (e.g. `theme:`)
        m = _NAV_PATH.search(line)
        if m:
            paths.add(m.group("path"))
    return paths


class TestEveryDocPageIsReachable:
    """An orphaned page ships in the build but no reader can navigate to it.

    `commands/app.md` (a real, documented command) and a stale roadmap doc
    were both live-but-unreachable until this pass. `--strict` cannot catch
    it: a page missing from the nav is not a broken link, just an invisible
    page. `docs/examples/**` is exempt — it is generated and gated by the
    Examples tests above.
    """

    def _orphans(self) -> list[str]:
        nav = _all_nav_paths()
        docs_root = ROOT / "docs"
        orphans = []
        for page in docs_root.rglob("*.md"):
            rel = page.relative_to(docs_root).as_posix()
            if rel.startswith("examples/"):
                continue
            if rel not in nav:
                orphans.append(rel)
        return sorted(orphans)

    def test_no_orphan_pages(self):
        orphans = self._orphans()
        assert orphans == [], (
            f"pages exist under docs/ but are unreachable from the nav: "
            f"{orphans}. Add a nav entry in mkdocs.yml, or delete the page."
        )


_TAB_MARKER = re.compile(r"""^=== ["'].+["']\s*$""")


def _all_doc_pages() -> list[Path]:
    """Every page under docs/, source READMEs included (not generated copies).

    The generated `docs/examples/*.md` are copies of the source READMEs, so
    linting the source under `src/just_makeit/examples/**` covers them without
    depending on a `make docs` having run.
    """
    pages = [
        p
        for p in (ROOT / "docs").rglob("*.md")
        if not p.relative_to(ROOT / "docs").as_posix().startswith("examples/")
    ]
    pages += list(EXAMPLES.glob("*/README.md"))
    return sorted(pages)


class TestTabbedBlocksRender:
    """A `=== "tab"` whose content is not indented renders as an empty tab.

    pymdownx.tabbed takes the *indented* block after a `=== "..."` marker as
    that tab's content. A code fence left at column 0 (e.g. wrapped in stray
    ```` ```` fences) is not part of the tab: the tab renders empty and the
    fence floats out as a separate block showing its own ``` markers as text.
    This is valid Markdown, so `zensical build --strict` does not catch it —
    it shipped on the homepage and in configuration.md (from #478) until a
    reader noticed. This gate is the backstop.
    """

    @pytest.mark.parametrize(
        "page",
        _all_doc_pages(),
        ids=lambda p: p.relative_to(ROOT).as_posix(),
    )
    def test_tab_content_is_indented(self, page):
        lines = page.read_text(encoding="utf-8").splitlines()
        bad = []
        for i, line in enumerate(lines):
            if not _TAB_MARKER.match(line):
                continue
            # First non-blank line after the marker is the tab's content.
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j >= len(lines):
                continue
            nxt = lines[j]
            # Valid: indented content, or an adjacent tab marker.
            if nxt.startswith((" ", "\t")) or _TAB_MARKER.match(nxt):
                continue
            bad.append(f"line {j + 1}: {nxt[:60]!r}")
        assert bad == [], (
            f"{page.relative_to(ROOT)}: tab content is not indented under its "
            f'`=== "..."` marker, so the tab renders empty and the block '
            f"floats out. Indent the tab body 4 spaces. {bad}"
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
