## Linking the real doppler C library

When doppler is available (a local install/build or the prebuilt release that
`nco_tone`'s harness auto-downloads), the example adds a standalone `tone`
object that wraps doppler's `nco_state_t *` as opaque state and links
`doppler::doppler-static`:

```toml
[tone]
arg_type        = "void"
return_type     = "float _Complex"
mutable         = "true"
extra_link_libs = ["doppler::doppler-static"]
# create_impl: obj->nco = nco_create(norm_freq, 0);
```

`[project] find_packages = ["Doppler"]` emits the `find_package(Doppler REQUIRED)`
block; the build is configured with `-DDoppler_DIR=...`. If doppler can't be
found, the `tone` object is skipped and the rest of the example still builds —
so the example is green everywhere, and exercises the real cross-library link
wherever doppler is present.

**Gotcha it demonstrates:** the local generator is named `lfo`, **not** `nco`,
on purpose. doppler ships its own `nco` whose header is `nco/nco_core.h`; a
local object of the same name would make `#include "nco/nco_core.h"` ambiguous
and silently resolve to the wrong one. Vendoring/linking an external library
means watching for name collisions with your own objects.
