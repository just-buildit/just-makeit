"""Render the sandbox welcome page from what was actually built.

The image's welcome text used to be a hand-written list inside ``motd.sh``.
It went stale exactly the way every hand-written list in this repo has: it
advertised ``my_corr/``, which no example produces, and omitted roughly
twenty-five project directories that are really there. The same failure that
`make help` exists to prevent — see the ``help-check`` gate, which was added
after ``make wheel`` stayed advertised in doppler once its rule was gone.

So nothing here is written by hand. :func:`render` is handed the mapping the
builder already computes — it diffs the destination directory before and after
each example runs, so it knows precisely which projects each one created — and
the one-line description comes from each example's own ``README.md``.

The result is written to two places from one string, because two copies of a
welcome drift the moment someone edits the friendlier one:

- ``$JM_HOME/README.md``, which the editor opens on attach (Codespaces shows
  nothing at all without it — the workspace folder held only dotfiles and
  ``examples/``);
- the terminal, via ``motd.sh``, which simply prints the same file.

Markdown was chosen for that reason: it renders in the editor and is still
readable when catted into a terminal.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HEADER = """# just-makeit example sandbox

Every project below is **already scaffolded, compiled and tested** inside this
image. Nothing needs building before you can read or run it.

> This page is generated when the image is built, from the examples that
> actually succeeded. If a project is listed here, it exists.
"""

_FOOTER = """
## Re-run an example end to end

Scaffolds fresh in a temp dir, builds, and runs its tests — the whole pipeline,
printed as it happens:

```sh
just-makeit example {first_example}
```

## Read an example's tutorial

Every example ships a walkthrough. They are package data inside site-packages,
so the image symlinks them somewhere you can type:

```sh
less ~/tutorials/{first_example}/README.md
```

## Start your own project

```sh
cd ~ && just-makeit new my_proj --object my_obj
cd my_proj
# edit src/my_proj/my_obj.c, then:
make && python3 -c "import my_proj; print(my_proj.MyObj())"
```

## Where things are

| What | Where |
| --- | --- |
| the built projects | `~/examples/` |
| the example tutorials | `~/tutorials/` |
| this page | `~/README.md` |
| every jm command | `just-makeit --help` |
"""


def describe(example_dir: Path) -> str:
    """One-line summary of an example, taken from its own ``README.md``.

    The bundled READMEs open with an ``# <name> example`` heading and then a
    short prose paragraph. The summary is that paragraph's **first sentence**,
    reflowed — not its first line, because the sources are hard-wrapped and a
    line ends wherever the wrapping fell.

    Returns ``""`` when the file is absent or has no prose before its first
    subheading — a missing description costs a blank cell, while inventing one
    would put text in the sandbox that no source owns.
    """
    readme = example_dir / "README.md"
    if not readme.is_file():
        return ""

    # Collect the whole first prose PARAGRAPH, not its first line. The bundled
    # READMEs are hard-wrapped at 79 columns, so a line ends wherever the
    # wrapping fell — taking one gave the sandbox table cells like "This
    # example builds one jm project that demonstrates the **object-of-objects",
    # severed mid-phrase.
    para: list[str] = []
    for line in readme.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if para:
                break
            continue
        # A list marker needs the trailing space. Bare "-"/"*" prefixes match
        # `**both** test and benchmark styles`, which is ordinary bold prose
        # in the middle of a wrapped paragraph — matching it truncated
        # full_workflow's summary mid-clause.
        if stripped.startswith(("!", "[", ">", "|", "```", "- ", "* ", "+ ")):
            if para:
                break
            continue
        para.append(stripped)
    if not para:
        return ""

    text = " ".join(para)
    # First sentence. The period must be followed by a space or end-of-text so
    # that "8 bytes/sample) is a cf32." survives and "cf32 (complex float-32,
    # 8 bytes/sample)" is not cut at a decimal point or an abbreviation dot.
    for i, ch in enumerate(text):
        if ch == "." and (i + 1 == len(text) or text[i + 1] == " "):
            text = text[: i + 1]
            break
    # A table cell, so it has to stay one line's worth. Cut on a word.
    if len(text) > 110:
        text = text[:110].rsplit(" ", 1)[0] + "…"
    # `|` would open a new column and silently mangle the row.
    return text.replace("|", "\\|")


def render(
    built: "dict[str, list[str]]", descriptions: "dict[str, str]"
) -> str:
    """The welcome page for *built*, a ``{example: [project dirs]}`` mapping.

    *descriptions* is ``{example: one-line summary}``. Both are supplied by the
    caller rather than discovered here, so this function is pure and a test can
    hand it a mapping that no image contains — which is the only way to prove
    the page follows its input instead of the repo it happens to be run in.

    An example that produced no project directory is omitted: the sandbox is a
    tour of things you can `cd` into, and a row that names nothing on disk is
    the stale entry this module exists to prevent.
    """
    rows: list[str] = []
    for example in sorted(built):
        projects = sorted(built[example])
        if not projects:
            continue
        for project in projects:
            desc = descriptions.get(example, "") or ""
            rows.append(f"| `~/examples/{project}/` | {desc} | `{example}` |")

    if not rows:
        body = (
            "\n_No examples were built into this image._ That is a build "
            "failure, not an empty tour — see the image build log.\n"
        )
    else:
        body = (
            "\n## The projects\n\n"
            "| Project | What it shows | Example |\n"
            "| --- | --- | --- |\n" + "\n".join(rows) + "\n"
        )

    return (
        _HEADER
        + body
        + _FOOTER.format(first_example=_worked_example(built, descriptions))
    )


def _worked_example(
    built: "dict[str, list[str]]", descriptions: "dict[str, str]"
) -> str:
    """The example the footer's copy-pasteable commands should name.

    Three conditions, and each is a command that would otherwise fail in front
    of a first-time reader:

    - it **built something**, or `just-makeit example <it>` is a tour of
      nothing (the naive `sorted(built)[0]` picked an example that produced no
      project at all);
    - it **has a description**, which in practice means it ships a
      `README.md` — three bundled examples do not, and the footer tells the
      reader to `less ~/tutorials/<it>/README.md`;
    - `fir_filter` wins when eligible, because it is the example the docs
      lead with everywhere else.

    Falls back through those conditions rather than off them: an image where
    nothing qualifies still gets a name, and the surrounding prose is honest
    about what the sandbox contains.
    """
    workable = sorted(e for e in built if built[e] and descriptions.get(e))
    if "fir_filter" in workable:
        return "fir_filter"
    if workable:
        return workable[0]
    any_built = sorted(e for e in built if built[e])
    return any_built[0] if any_built else "fir_filter"


def main(argv: "list[str]") -> int:
    """``welcome.py <jm_home>`` — render ``.jm-built.json`` into README.md.

    Run as its own Docker layer, after the examples are built. Keeping it
    separate is what lets the welcome's wording change without rebuilding
    twenty-odd example projects to see it.
    """
    if len(argv) != 2:
        print("usage: welcome.py <jm_home>", file=sys.stderr)
        return 2
    home = Path(argv[1])
    manifest = home / ".jm-built.json"
    if not manifest.is_file():
        print(f"welcome.py: {manifest} missing", file=sys.stderr)
        return 1
    data = json.loads(manifest.read_text(encoding="utf-8"))
    page = render(data["built"], data["descriptions"])
    (home / "README.md").write_text(page, encoding="utf-8")
    print(f"welcome.py: wrote {home / 'README.md'} ({len(page)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
