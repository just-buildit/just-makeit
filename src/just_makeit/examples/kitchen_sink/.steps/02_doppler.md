## Linking the real doppler C library

When doppler is available (a local install/build or the prebuilt release that
`nco_tone`'s harness auto-downloads), the example adds a standalone `tone`
object that wraps doppler's `dp_nco_state_t *` as opaque state and links
`doppler::doppler-static`:

```toml
[tone]
arg_type        = "void"
return_type     = "float _Complex"
mutable         = "true"
extra_link_libs = ["doppler::doppler-static"]
# create_impl: obj->nco = dp_nco_create(norm_freq, 0);
```

`[project] find_packages = [{ name = "Doppler", pkg_config = "doppler" }]` emits
the `find_package(Doppler REQUIRED)` block, and the installed project's
`find_dependency(Doppler)` and `.pc` `Requires.private: doppler` -- the table
form is what names doppler's pkg-config module, which its CMake package name
does not. The build is configured with `-DDoppler_DIR=...`. If doppler can't be
found, the `tone` object is skipped and the rest of the example still builds —
so the example is green everywhere, and exercises the real cross-library link
wherever doppler is present.

**Gotcha it demonstrates:** linking an external library means watching for
name collisions with your own objects. The local generator is named `lfo`,
**not** `nco`: before doppler 0.58, doppler's own `nco` header was
`nco/nco_core.h`, and a local object of the same name made that `#include`
ambiguous. Since 0.58 doppler's headers live under `doppler/`
(`doppler/nco/nco_core.h`) and its C symbols carry `dp_`, so the two no
longer collide -- which is exactly what that namespacing is for.
