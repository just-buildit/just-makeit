- **No code span on the docs site shows a run of spaces** (gh-1779). Nine
    spans, seven under `docs/` plus one each in `CLAUDE.md` and the
    ring_buffer example's README, held one: a span wrapped across an indented
    line break keeps the indent, so `make_window` rendered with three spaces
    before `*out`. They now read with single spaces, and the definition-list
    syntax in the zensical notes moved into a fenced block, where its spacing
    is meant. `make lint` now refuses such a span in any tracked Markdown file
    (`code-span-check`).
