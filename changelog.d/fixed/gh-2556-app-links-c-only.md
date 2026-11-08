- **A C app links C libraries only** (gh-2556). An app asked for libm as
    `$<$<NOT:$<PLATFORM_ID:Windows>>:m>`. Under `jm module m` the bare name
    `m` resolves to that module's Python extension target, the same shadow
    gh-1305 fixed for tests and benchmarks: macOS refused the link
    (`unsupported mach-o filetype`) and Linux silently linked the extension.
    A function app over a dotted module (`dsp.filters`) linked
    `dsp.filters_core`, which is not a target, so its build failed on the
    core's header. An app now links libm through `${JM_MATH_LIBRARY}`, the
    reference every other link line uses, and a module's core through its
    cname (`dsp_filters_core`).
