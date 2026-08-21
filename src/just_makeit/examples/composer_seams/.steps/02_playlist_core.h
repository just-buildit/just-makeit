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
