# Feature map

The menu of what just-makeit can do, and the one bundled example that shows
each feature most clearly. This is a *map*, not a tutorial — scan for the thing
you need, then open its example.

Every example is runnable end to end with:

```sh
just-makeit example <name>
```

It scaffolds fresh in a temp dir, builds, and runs its tests. Every example has
a walkthrough page.

For a single guided build that touches many features at once, start with the
[feature tour](feature-tour.md). For composing objects out of objects, see the
[object-of-objects guide](object-of-objects.md).

______________________________________________________________________

## Object shapes — what `step()` looks like

| Feature                                          | Example                                                                                   |
| ------------------------------------------------ | ----------------------------------------------------------------------------------------- |
| Scalar processor (`x -> y`)                      | [FIR filter](examples/fir_filter.md)                                                      |
| Generator (`void -> y`)                          | [NCO tone](examples/nco_tone.md)                                                          |
| Consumer / sink (`x -> void`)                    | [Accumulator](examples/accumulator.md)                                                    |
| Blockwise (`T[] -> U[]`)                         | [Blockwise template](templates/blockwise.md)                                              |
| Array-buffer primary arg (`--arg-type T[]`)      | [Array processing](examples/array_processing.md#5-arg-type-type-array-buffer-primary-arg) |
| Method-only object (`--no-step`, custom methods) | [Ring buffer](examples/ring_buffer.md)                                                    |
| Process a whole block (`steps`)                  | [Array processing](examples/array_processing.md)                                          |

## State and lifecycle

| Feature                                     | Example                                                  |
| ------------------------------------------- | -------------------------------------------------------- |
| State variables with defaults               | [Running stats](examples/running_stats.md)               |
| Mutable state (`--mutable`)                 | [NCO tone](examples/nco_tone.md)                         |
| Opaque heap state (`create` / `destroy`)    | [Delay line](examples/delay_line.md)                     |
| `create` / `reset` / `destroy` + heap field | [Delay line](examples/delay_line.md)                     |
| Declarative TOML fragment (`jm apply`)      | [Declarative scaffold](examples/declarative_scaffold.md) |
| `no_state` / user-facing init-params        | [Feature tour](feature-tour.md)                          |

## Methods and outputs

| Feature                                                                                          | Example                                            |
| ------------------------------------------------------------------------------------------------ | -------------------------------------------------- |
| Named execute methods with params                                                                | [Accumulator](examples/accumulator.md)             |
| Variable-length output                                                                           | [Array processing](examples/array_processing.md)   |
| Per-call output array (`out-type` / `out-divisor`)                                               | [Array processing](examples/array_processing.md)   |
| Second output array (`multi-output`)                                                             | [Array processing](examples/array_processing.md)   |
| GIL release (`nogil`), `pass-capacity`                                                           | [Kitchen sink](examples/kitchen_sink.md)           |
| `*args` / `**kwargs` methods (`--varargs`)                                                       | [Varargs methods](examples/varargs_method.md)      |
| One record / structured array / list of records (`--single`, `--record-dtype`, `--result-field`) | [Record shapes](examples/record_shapes.md)         |
| Zero-copy borrowed views (`--borrow`, `--status-fn`, `--releases`, `--strict`, `--header-only`)  | [Ring buffer](examples/ring_buffer.md)             |
| Failure channels (`jm error`, `jm warning`, `--status-return`, `--error-negative`)               | [Errors and warnings](examples/errors_warnings.md) |

## Functions and properties

| Feature                                                           | Example                                                                        |
| ----------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Module-level C function                                           | [Module functions](examples/jm_function.md)                                    |
| Inline function (in the header)                                   | [Module functions](examples/jm_function.md)                                    |
| Filesystem path argument (`name:path`)                            | [Extend command](commands/extend.md#just-makeit-function)                      |
| Enum argument (`name:enum:<ename>`)                               | [Extend command](commands/extend.md#just-makeit-function)                      |
| C enum whose values are not 0..n-1 (`[[enum]] enumerators`)       | [C enum constants](examples/enum_constants.md)                                 |
| Raise on non-zero return (`--check-return`)                       | [Extend command](commands/extend.md#just-makeit-function)                      |
| Raise the C refusal's own reason (`--why`)                        | [Extend command](commands/extend.md#just-makeit-function)                      |
| Raise a composer's refusal reason (`from_json_why`, `create_why`) | [Object-of-objects](object-of-objects.md#47-json-faces-generated-vs-delegated) |
| Writable property                                                 | [Feature tour](feature-tour.md)                                                |
| Field-backed property                                             | [IQ file](examples/iqfile.md)                                                  |

## Modules and linking

| Feature                                    | Example                                    |
| ------------------------------------------ | ------------------------------------------ |
| Multiple types in one module `.so`         | [Filter module](examples/filter_module.md) |
| External C library (`find_package` + link) | [NCO tone](examples/nco_tone.md)           |
| Vendored C dep, `depends_on`, reexports    | [Kitchen sink](examples/kitchen_sink.md)   |

## Performance

| Feature                                                         | Example                                    |
| --------------------------------------------------------------- | ------------------------------------------ |
| `JM_HOT` / `JM_FORCEINLINE` annotations                         | [FIR filter](examples/fir_filter.md)       |
| SIMD batch dispatch (`JM_DEFINE_STEPS`)                         | [FIR filter](examples/fir_filter.md)       |
| Portable SIMD macros (`jm_simd.h`: `JM_ADD_F32`, `JM_HSUM_F32`) | [Sliding power](examples/sliding_power.md) |

## Composing objects out of objects

| Feature                                       | Example / guide                                 |
| --------------------------------------------- | ----------------------------------------------- |
| `kind = "handle"` — typed RAII resource class | [Composites](examples/composites.md)            |
| `kind = "capsule"` / `kind = "composer"`      | [Object-of-objects guide](object-of-objects.md) |
| Composer bridge / computed-property C seams   | [Composer seams](examples/composer_seams.md)    |

## Two classes over one core

| Feature                                              | Example                           |
| ---------------------------------------------------- | --------------------------------- |
| Second Python class over one C core (`jm view`)      | [Views](examples/views_module.md) |
| A view's own constructor (`--create-fn`)             | [Views](examples/views_module.md) |
| Trim a view's surface (`--exclude-property/-method`) | [Views](examples/views_module.md) |
| Add / override a member on a view (`--view`)         | [Views](examples/views_module.md) |

## Streaming

| Feature                                    | Example                                                  |
| ------------------------------------------ | -------------------------------------------------------- |
| `streamable` → `stream()` / `__iter__`     | [Stream source](examples/stream_source.md)               |
| Blockwise streaming                        | [Stream blockwise](examples/stream_blockwise.md)         |
| Async iteration                            | [Stream source (async)](examples/stream_source_async.md) |
| Re-framing a stream (method-only `framer`) | [Stateful vs Pure](pure.md#method-only-object-no-step)   |

## Documentation

| Feature                                             | Example / guide                                                                                   |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| Doxygen `@brief` / `@param` → `.pyi` docstrings     | [Accumulator](examples/accumulator.md#document-once-in-c-rich-stubs-and-runnable-doctests)        |
| `@code` block → doctest run against the built `.so` | [Accumulator](examples/accumulator.md#document-once-in-c-rich-stubs-and-runnable-doctests)        |
| Documenting free and `static inline` functions      | [Module functions](examples/jm_function.md#5-document-once-in-c-rich-stubs-and-runnable-doctests) |
| Which Doxygen tags jm reads, and what they map to   | [Enriching stubs](workflows/enriching-stubs.md)                                                   |
| Doxygen C site + Zensical Python site (`make docs`) | [Full workflow](examples/full_workflow.md)                                                        |

## Applications and tooling

| Feature                                           | Example                                               |
| ------------------------------------------------- | ----------------------------------------------------- |
| C exe / console script / PEP 723 from a component | [Three faces](examples/three_face.md)                 |
| Full lifecycle (build, test, bench, docs)         | [Full workflow](examples/full_workflow.md)            |
| `--pytest` / `--pytest-benchmark` test styles     | [Full workflow](examples/full_workflow.md)            |
| Upgrading an old project (`jm upgrade`)           | [Upgrading an old project](examples/stale_project.md) |
