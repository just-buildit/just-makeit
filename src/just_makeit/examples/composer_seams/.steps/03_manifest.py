"""Declare the composer module. There is no `jm composer` command.

A composer is manifest-only: this table plus `just-makeit apply` is the whole
interface. `c_deps` goes on `[project]`; everything else describes the four
OO types jm will emit.
"""

from pathlib import Path

MANIFEST = Path("just-makeit.toml")

COMPOSER = """
[module.playlist]
kind = "composer"
backing = "playlist"
composes = ["clip"]
# `clip_core` is the generator's OBJECT lib; `backing_core` is the c_deps one.
# CMake will not pull either through transitively, so both are named here.
depends_on = [{ name = "clip", link = true }]
extra_link_libs = ["backing_core"]

[module.playlist.source]
object = "clip"
struct = "clip_t"
type_name = "Clip"

[[module.playlist.source.fields]]
name = "gain"
type = "double"
default = "1.0"

# ── seam 1: build the generator from a source config ──────────────────────
# jm emits the binding for Clip.step()/steps(); `clip_from_source` is the
# straight-C function that turns a clip_t into a running clip_state_t.
[module.playlist.source.generates]
generator = "clip"
bridge_fn = "clip_from_source"

# ── seam 2: a derived read-only property ──────────────────────────────────
# Computed in C on every read, so it cannot go stale when `gain` is
# reassigned -- which a stored field would.
[[module.playlist.source.computed]]
name = "duration"
type = "double"
fn = "clip_duration"

[module.playlist.segment]
type_name = "Track"
struct = "track_t"
sources = "multi"

[[module.playlist.segment.fields]]
name = "dur"
type = "size_t"
default = "4"

[module.playlist.oo]
composer_type_name = "Mix"
"""


def main() -> None:
    text = MANIFEST.read_text(encoding="utf-8")
    if "[module.playlist]" in text:
        return
    assert "[project]\n" in text, "unexpected manifest layout"
    text = text.replace("[project]\n", '[project]\nc_deps = ["backing"]\n', 1)
    MANIFEST.write_text(text + COMPOSER, encoding="utf-8")


if __name__ == "__main__":
    main()
