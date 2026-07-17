"""Copy example READMEs into docs/examples/ and generate the index page.

Run from the project root:

    python3 scripts/copy_examples.py

Every example under ``src/just_makeit/examples/`` that has a ``README.md`` is
published to the docs site. Everything this writes into ``docs/examples/`` is
gitignored and regenerated before every build (see the ``docs`` target in the
Makefile and the Docs workflow), so nothing in that directory is hand-authored:
an example's prose has exactly one home, the README that sits next to the
``test.py`` verifying its steps.

That rule was broken once before. ``docs/examples/`` was declared generated in
May 2026, and hand-written pages were then force-added into it past the ignore
rule — which is how two of them drifted into a 692-line README and a 263-line
page describing the same example differently.

To add an example: create the directory with a ``README.md``, add it to
``GALLERY`` (display name and position), and add a matching nav entry in
``mkdocs.yml``. This script *fails* rather than skips when those fall out of
sync — the old silent ``skip`` on a missing README is exactly how a nav entry
ends up pointing at a page that was never generated.

Link convention: a README is copied to ``docs/examples/<name>.md``, so its
relative links must resolve from ``docs/examples/`` — ``../commands/app.md``
is correct, ``../other_example/README.md`` is not. This makes cross-doc links
correct on the site and broken when browsing the README on GitHub; the site is
the audience that gets the link checked (``zensical build --strict``).
"""

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src" / "just_makeit" / "examples"
DEST = ROOT / "docs" / "examples"

# Published examples: folder -> display name, in gallery order. The order here
# is the order of the index table and should match the Examples nav section in
# mkdocs.yml.
GALLERY = {
    "running_stats": "Running stats",
    "fir_filter": "FIR filter",
    "sliding_power": "Sliding power",
    "sliding_correlator": "Sliding correlator",
    "array_processing": "Array processing",
    "varargs_method": "Varargs methods",
    "opaque_counter": "Opaque counter",
    "delay_line": "Delay line",
    "accumulator": "Accumulator",
    "stream_source": "Stream source",
    "stream_blockwise": "Stream blockwise",
    "stream_chunker": "Stream chunker",
    "stream_source_async": "Stream source (async)",
    "dsp_toolkit": "DSP toolkit",
    "filter_module": "Filter module",
    "jm_function": "Module functions",
    "iqfile": "IQ file",
    "nco_tone": "NCO tone",
    "jm_app": "App scaffolding",
    "three_face": "Three faces",
    "declarative_scaffold": "Declarative scaffold",
    "pytest_style": "pytest style",
    "full_workflow": "Full workflow",
    "composites": "Composites",
    "kitchen_sink": "Kitchen sink",
}

# Examples with no README, deliberately. These are end-to-end regression
# drivers for the CI suite rather than gallery material; `jm example <name>`
# still runs them. Listed explicitly so that an example that is *missing* its
# README is an error, not an omission nobody notices.
UNPUBLISHED = {
    "app_shapes": "regression driver: jm app over non-scalar objects",
    "bench_upgrade": "regression driver: bench regeneration via jm upgrade",
    "jm_remove": "regression driver: jm remove",
}


def _example_dirs() -> dict[str, bool]:
    """Map every example directory name to whether it has a README.md."""
    return {
        p.name: (p / "README.md").exists()
        for p in SRC.iterdir()
        if p.is_dir() and not p.name.startswith(("_", "."))
    }


def _reconcile(dirs: dict[str, bool]) -> list[str]:
    """Return the reasons this run must fail, empty when in sync.

    Checks both directions between what is on disk and what the two lists
    above claim, so neither a new example nor a deleted one can slip through
    silently.

    Parameters
    ----------
    dirs
        Example directory name -> whether it holds a ``README.md``, as
        produced by `_example_dirs`. Passed in rather than read from disk so
        the reconciliation rules can be tested against synthetic layouts.
    """
    problems: list[str] = []

    for name, has_readme in sorted(dirs.items()):
        listed = name in GALLERY
        excused = name in UNPUBLISHED
        if listed and excused:
            problems.append(
                f"{name}: listed in both GALLERY and UNPUBLISHED — pick one"
            )
        elif has_readme and not listed:
            problems.append(
                f"{name}: has a README but is not in GALLERY — add it there "
                f"(and add a nav entry in mkdocs.yml), or delete the README"
            )
        elif not has_readme and not excused:
            problems.append(
                f"{name}: has no README — write one and add it to GALLERY, "
                f"or add it to UNPUBLISHED with a reason"
            )
        elif not has_readme and listed:
            problems.append(
                f"{name}: in GALLERY but has no README.md to publish"
            )
        elif has_readme and excused:
            problems.append(
                f"{name}: has a README but is marked UNPUBLISHED — move it "
                f"to GALLERY, or delete the README"
            )

    for name in sorted(set(GALLERY) - set(dirs)):
        problems.append(f"{name}: in GALLERY but {SRC / name} does not exist")
    for name in sorted(set(UNPUBLISHED) - set(dirs)):
        problems.append(
            f"{name}: in UNPUBLISHED but {SRC / name} does not exist"
        )

    return problems


def _first_sentence(md_path: Path) -> str:
    """Return the first meaningful sentence from the first body paragraph."""
    text = md_path.read_text(encoding="utf-8")
    found_heading = False
    para_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not found_heading:
            if stripped.startswith("# "):
                found_heading = True
            continue
        if stripped.startswith(("---", "___", "#")):
            if para_lines:
                break
            continue
        if stripped == "":
            if para_lines:
                break
        else:
            para_lines.append(stripped)

    para = " ".join(para_lines)
    # Trim to first sentence (ends at `. `, `.\n`, or end of string).
    m = re.search(r"\.(\s|$)", para)
    if m:
        para = para[: m.start() + 1]
    # Collapse inline backtick spans (keep them as-is, just tidy whitespace).
    return re.sub(r"\s+", " ", para).strip()


def _index(rows: list[tuple[str, str, str]]) -> str:
    """Render index.md from (out_name, display_name, description) rows."""
    # Width the first column to the rendered link, not just the display name —
    # `[Running stats](running_stats.md)` is far wider than `Running stats`.
    # Alignment is cosmetic to a Markdown renderer, but nothing reflows this
    # file: it is gitignored, so mdformat never sees it.
    links = {r[0]: f"[{r[1]}]({r[0]})" for r in rows}
    col1 = max((len(v) for v in links.values()), default=20)
    col2 = max((len(r[2]) for r in rows), default=40)

    header = (
        "# Examples\n\n"
        "Each example is a complete, buildable project that walks through a "
        "real\nalgorithm from scaffold to optimised implementation. Run any "
        "of them with\n`just-makeit example <name>`.\n\n"
    )
    table = f"| {'Example':<{col1}} | {'What it demonstrates':<{col2}} |\n"
    table += f"| {'-' * col1} | {'-' * col2} |\n"
    for out_name, _display_name, description in rows:
        table += f"| {links[out_name]:<{col1}} | {description:<{col2}} |\n"

    footer = (
        "\nEvery example ships with an end-to-end test in "
        "`src/just_makeit/examples/*/test.py`\nthat the CI suite runs, so the "
        "steps above are executed, not just described.\nSee "
        "`src/just_makeit/examples/README.md` for contributor notes on the\n"
        "`.steps/` naming convention.\n"
    )
    return header + table + footer


def main() -> int:
    problems = _reconcile(_example_dirs())
    if problems:
        print("copy_examples: examples and the gallery list are out of sync:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    DEST.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, str, str]] = []
    for folder, display_name in GALLERY.items():
        out_name = f"{folder}.md"
        dst = DEST / out_name
        shutil.copy2(SRC / folder / "README.md", dst)
        print(
            f"  copy  examples/{folder}/README.md → docs/examples/{out_name}"
        )
        rows.append((out_name, display_name, _first_sentence(dst)))

    (DEST / "index.md").write_text(_index(rows), encoding="utf-8")
    print(f"  gen   docs/examples/index.md  ({len(rows)} examples)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
