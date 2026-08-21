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
