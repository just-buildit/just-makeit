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
