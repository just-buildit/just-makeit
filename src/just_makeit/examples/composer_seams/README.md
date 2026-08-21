# composer_seams example

A `kind = "composer"` module — the third and largest of the object-of-objects
generators — and specifically **the two seams where it hands work back to you
as plain C** (gh-998).

A composer emits four CPython types into one `.so`: a *source* (`Clip`), a
*segment* (`Track`), a *timeline*, and the *composer* itself (`Mix`). All of
that binding is jm's. Two things are not, and they are the interesting part:

| seam | declared by | what you write |
| --- | --- | --- |
| build the generator from a source config | `[module.X.source.generates] bridge_fn` | `<gen>_state_t *fn(const <struct> *, double)` |
| derive a read-only property | `[[module.X.source.computed]] fn` | `<type> fn(const <struct> *)` |

Both are **straight C with no CPython in them**. jm knows their signatures
exactly, so it publishes them in a generated header —
`native/inc/<mod>/<mod>_bridge.h` — and the binding includes that rather than
re-declaring them. Before gh-998 they were `extern` lines buried inside the
generated `_ext.c`, so a C test or benchmark could reach a signature jm owns
only by writing a second copy of it.

That header is the one file this example is really about, and step 7 uses it
the way it is meant to be used: from a translation unit that includes nothing
else.

A composer is also **manifest-only** — there is no `jm composer` command. You
write the table and run `just-makeit apply`.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example composer_seams
# composer_seams: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Pass a custom path to keep the venv somewhere persistent:

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh) -- ~/my-venv
```

Or with `pip` if just-makeit is already installed:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```

---

## 1. Scaffold the generator being composed

```sh
just-makeit new studio
cd studio

# The object a source composes into: an ordinary jm component whose
# create/step/steps/reset/destroy are exactly what a composer expects.
just-makeit object clip \
    --state "level:double:0.0" \
    --arg-type void \
    --return-type "float _Complex"
```

Nothing composer-specific yet — this is a plain `jm object`. It matters
because the composer's defaults are named after it: declaring
`generator = "clip"` makes jm expect `clip_state_t`, `clip_step`,
`clip_steps`, `clip_reset`, `clip_destroy` and `clip/clip_core.h`, all of
which `jm object` has just produced. Every one is overridable in the manifest;
none of them needs to be here.

`--arg-type void --return-type "float _Complex"` is what makes it a *source*:
`clip_step(state)` takes no input and returns a sample, and
`clip_steps(state, out, n)` fills a block.

---

## 2. Write the backing kernel

A composer does not generate its kernel — `backing = "playlist"` names C that
is yours. It lives in a `c_deps` directory, which is jm's escape hatch for
hand-written C: jm emits an `add_subdirectory()` line for it and never touches
anything inside.

`native/inc/playlist/playlist_core.h`:

```
/* playlist_core.h — the backing kernel, hand-written.
 *
 * jm never generates this: a composer's `backing` is the project's own C.
 * What jm DOES rely on is the shape below, so the three declarations here
 * are effectively an interface the generated binding calls into.
 */
#ifndef PLAYLIST_CORE_H
#define PLAYLIST_CORE_H

#include <complex.h>
#include <stddef.h>

/* The source config the `Clip` type wraps. One member per
 * `[[module.playlist.source.fields]]` entry — jm marshals Python onto this. */
typedef struct
{
  double gain;
} clip_t;

/* One segment: a set of sources summed over `dur` samples. jm builds a
 * transient array of these from the OO objects and hands it to create().
 * `fs` is always present, whether or not the manifest lists it. */
typedef struct
{
  clip_t *sources;
  size_t  n_sources;
  size_t  dur;
  double  fs;
} track_t;

typedef struct playlist_state playlist_state_t;

playlist_state_t *playlist_create (const track_t *tracks, size_t n, int repeat,
                                   int continuous);
size_t playlist_execute (playlist_state_t *state, float complex *out,
                         size_t max);
/* Reflects the RESOLVED spec back, so `Mix.segments` can rebuild OO objects
 * from what the kernel actually holds rather than from what was passed in. */
const track_t *playlist_segments (const playlist_state_t *state, size_t *n,
                                  int *repeat, int *continuous);
void           playlist_destroy (playlist_state_t *state);

#endif /* PLAYLIST_CORE_H */
```

`native/src/backing/playlist_core.c`:

```c
#include "playlist/playlist_core.h"
#include <stdlib.h>
#include <string.h>

struct playlist_state
{
  track_t *tracks;
  size_t   n_tracks;
  size_t   track_i;
  size_t   pos;
  int      repeat;
  int      continuous;
};

playlist_state_t *
playlist_create (const track_t *tracks, size_t n, int repeat, int continuous)
{
  playlist_state_t *st;

  if (!tracks || n == 0)
    return NULL;
  st = calloc (1, sizeof *st);
  if (!st)
    return NULL;
  st->tracks = calloc (n, sizeof *st->tracks);
  if (!st->tracks)
    {
      free (st);
      return NULL;
    }
  /* Deep-copy: the array jm passes is transient and its `sources` pointers
   * alias buffers owned by the Python objects. */
  for (size_t i = 0; i < n; i++)
    {
      st->tracks[i]         = tracks[i];
      st->tracks[i].sources = calloc (tracks[i].n_sources, sizeof (clip_t));
      if (!st->tracks[i].sources)
        {
          st->n_tracks = i;
          playlist_destroy (st);
          return NULL;
        }
      memcpy (st->tracks[i].sources, tracks[i].sources,
              tracks[i].n_sources * sizeof (clip_t));
    }
  st->n_tracks   = n;
  st->repeat     = repeat;
  st->continuous = continuous;
  return st;
}

size_t
playlist_execute (playlist_state_t *state, float complex *out, size_t max)
{
  size_t n = 0;

  if (!state || !out)
    return 0;
  while (n < max)
    {
      track_t *tr;
      double   sum = 0.0;

      if (state->track_i >= state->n_tracks)
        {
          if (!state->repeat && !state->continuous)
            break; /* a finite spec simply runs out */
          state->track_i = 0;
        }
      tr = &state->tracks[state->track_i];
      for (size_t k = 0; k < tr->n_sources; k++)
        sum += tr->sources[k].gain;
      out[n++] = (float complex)sum;
      if (++state->pos >= tr->dur)
        {
          state->pos = 0;
          state->track_i++;
        }
    }
  return n;
}

const track_t *
playlist_segments (const playlist_state_t *state, size_t *n, int *repeat,
                   int *continuous)
{
  *n          = state->n_tracks;
  *repeat     = state->repeat;
  *continuous = state->continuous;
  return state->tracks;
}

void
playlist_destroy (playlist_state_t *state)
{
  if (!state)
    return;
  for (size_t i = 0; i < state->n_tracks; i++)
    free (state->tracks[i].sources);
  free (state->tracks);
  free (state);
}
```

`native/src/backing/CMakeLists.txt`:

```
# Hand-owned. A `c_deps` entry gets an `add_subdirectory()` line from jm and
# nothing else — this file is never regenerated, which is the whole point of
# the escape hatch.
add_library(backing_core OBJECT playlist_core.c playlist_bridge.c)
target_include_directories(backing_core PUBLIC ${CMAKE_SOURCE_DIR}/native/inc)
```

---

## 3. Declare the composer — manifest only

```python
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
```

The two seams are five lines of that table. Everything else describes the
types jm will emit, and the `fields` list is the keystone: one ordered list of
`{name, type, default}` per source and segment drives the C struct
marshalling, the getset slots, and the constructor keywords all at once.

---

## 4. Apply

```sh
just-makeit apply
```

```
  create  native/inc/playlist/playlist_bridge.h
  create  native/src/playlist/CMakeLists.txt
  create  native/src/playlist/playlist_ext.c
  create  src/studio/playlist/playlist.pyi
  update  CMakeLists.txt
```

The first of those is the gh-998 file, and it is the only header a composer
module emits:

```c
/* Build the composed generator from a source config (source -> generator). */
clip_state_t *clip_from_source(const clip_t *, double);

/* Computed read-only property `duration`. */
double clip_duration(const clip_t *);
```

Self-contained on purpose — it pulls in the backing header and the
generator's, so a consumer does not have to work out what to include first. It
is emitted **only** when the source declares at least one seam; a composer
with neither gets no header at all, because there would be nothing to say.

---

## 5. Write the two seams

`native/src/backing/playlist_bridge.c`:

```c
/* The bodies behind playlist_bridge.h.
 *
 * Note what is NOT here: no Python.h, no PyObject, no reference counting.
 * jm owns the binding that calls these; the algorithms are the project's.
 * The include is the generated header, so these definitions are checked
 * against jm's declarations by the compiler rather than by eye.
 */
#include "playlist/playlist_bridge.h"

/* Seam 1 — source config to running generator. A real one would derive
 * increments from `fs`; this one just carries the level across. */
clip_state_t *
clip_from_source (const clip_t *src, double fs)
{
  (void)fs;
  return clip_create (src->gain);
}

/* Seam 2 — a quantity derived from the config, never stored beside it. */
double
clip_duration (const clip_t *src)
{
  return src->gain * 2.0;
}
```

Two things are worth noticing.

The file **includes the generated header** rather than declaring anything.
That is what makes the split safe: if the manifest renames `bridge_fn`, or a
computed property changes type, this file stops compiling instead of quietly
linking against a signature that no longer matches.

And there is no CPython in it. The seams exist precisely so that the parts
only you can write stay in plain C — the marshalling, the type objects, the
reference counting and the error translation are all on jm's side of the line.

---

## 6. Build

```sh
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel 4
```

The composer's `.so` links `clip_core` (the generator) and `backing_core`
(the `c_deps` library) alongside NumPy. CMake will not pull an OBJECT
library's objects through another target transitively, which is why both are
named in the manifest rather than left to propagate.

---

## 7. Drive it from Python

```python
"""Drive the four generated OO types, and both seams."""

import sys

sys.path.insert(0, "src")

import numpy as np  # noqa: E402

from studio.playlist.playlist import Clip, Mix, Track  # noqa: E402

# ── seam 2: a computed property ──────────────────────────────────────────
clip = Clip(gain=2.0)
print(f"Clip(gain=2.0).gain      -> {clip.gain}")
print(f"           .duration     -> {clip.duration}   (clip_duration, in C)")
assert clip.duration == 4.0

# Derived on every read, so reassigning the field it depends on is enough.
# A stored attribute would still be reporting 4.0 here.
clip.gain = 5.0
print(f"  after gain = 5.0       -> {clip.duration}  (recomputed, not stored)")
assert clip.duration == 10.0

# It is read-only: there is no field behind it to assign to.
try:
    clip.duration = 1.0
    raise AssertionError("expected a read-only property")
except AttributeError:
    print("           .duration = 1.0 -> AttributeError (read-only)")

# ── seam 1: standalone generation through the bridge ─────────────────────
# Clip.steps() has no kernel of its own; it calls clip_from_source() to build
# a clip_state_t and then drives the generator jm never had to know about.
block = Clip(gain=7.0, fs=1.0).steps(3)
print(f"Clip(gain=7.0).steps(3)  -> {block}   (via clip_from_source)")
assert isinstance(block, np.ndarray)
assert np.allclose(block, [7 + 0j, 7 + 0j, 7 + 0j])

# ── the composed object-of-objects ───────────────────────────────────────
# Track sums its sources; Mix sequences tracks and runs the backing kernel.
track = Track.sum(Clip(gain=2.0), Clip(gain=3.0), dur=4)
mix = Mix(track)
out = mix.execute(8)
print(f"Mix(Track.sum(2,3,dur=4)).execute(8) -> {out}")
# Four samples of 2+3, then the finite spec runs out -- execute() returns a
# short block rather than padding.
assert np.allclose(out, [5 + 0j] * 4)
assert len(out) == 4

# The resolved spec reflects back as rebuilt OO objects -- read from the
# kernel through playlist_segments(), not cached from what was passed in.
print(
    f"mix.segments             -> {len(mix.segments)} track(s), "
    f"repeat={mix.repeat}, continuous={mix.continuous}"
)
assert len(mix.segments) == 1
assert isinstance(mix.segments[0], Track)
assert mix.repeat is False and mix.continuous is False

print("composer_seams demo: PASSED")
```

```
Clip(gain=2.0).gain      -> 2.0
           .duration     -> 4.0   (clip_duration, in C)
  after gain = 5.0       -> 10.0  (recomputed, not stored)
           .duration = 1.0 -> AttributeError (read-only)
Clip(gain=7.0).steps(3)  -> [7.+0.j 7.+0.j 7.+0.j]   (via clip_from_source)
Mix(Track.sum(2,3,dur=4)).execute(8) -> [5.+0.j 5.+0.j 5.+0.j 5.+0.j]
mix.segments             -> 1 track(s), repeat=False, continuous=False
composer_seams demo: PASSED
```

`duration` is the argument for computing rather than storing: reassigning
`gain` changes it, and there is no cache to invalidate because there is no
cache. And `Clip.steps()` is a generator the source type never contained —
`clip_from_source` builds it on demand, which is why a *config* object can
produce samples at all.

---

## 8. The point: a C consumer needs only the generated header

```c
/* A C consumer of the composer's seams.
 *
 * This translation unit includes the generated bridge header and NOTHING
 * else -- no Python, no re-declared externs, no knowledge of where the
 * definitions live. That is the whole of gh-998: before it, the only route
 * to these signatures was to write a second copy of them, and a second copy
 * is what drifts.
 */
#include "playlist/playlist_bridge.h"

#include <stdio.h>

int
main (void)
{
  clip_t        src = { 0 };
  clip_state_t *gen;
  double        d;

  src.gain = 3.0;

  /* Seam 2, called straight from C. */
  d = clip_duration (&src);
  if (d != 6.0)
    {
      fprintf (stderr, "clip_duration: got %f, want 6.0\n", d);
      return 1;
    }

  /* Seam 1, likewise -- and the generator it returns is an ordinary
   * jm component, so the rest of its API is already available here. */
  gen = clip_from_source (&src, 1.0);
  if (!gen)
    {
      fprintf (stderr, "clip_from_source returned NULL\n");
      return 1;
    }
  if (crealf (clip_step (gen)) != 3.0f)
    {
      fprintf (stderr, "clip_step disagrees with the source config\n");
      clip_destroy (gen);
      return 1;
    }
  clip_destroy (gen);

  printf ("bridge consumer: PASSED\n");
  return 0;
}
```

```sh
cc -I native/inc native/tests/test_bridge.c \
   build/native/src/backing/CMakeFiles/backing_core.dir/*.o \
   build/native/src/clip/CMakeFiles/clip_core.dir/*.o -lm -o /tmp/test_bridge
/tmp/test_bridge
# bridge consumer: PASSED
```

This is what the seams' prototypes moving out of `_ext.c` bought. That file is
a CPython translation unit — including it from a test is not an option — so
while the declarations lived there, a C test wanting to assert that the
composed path and the standalone path agree could reach only the half with a
public header. The real instance downstream had to say so in a comment and
cover the other half from Python instead, which is a weaker claim than the one
it was trying to make.

One header, one declaration each, checked by the compiler on both sides.
