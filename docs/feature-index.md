# Feature map

The menu of what just-makeit can do, and the one bundled example that shows
each feature most clearly. This is a *map*, not a tutorial — scan for the thing
you need, then open its example.

Every example is runnable end to end with:

```sh
just-makeit example <name>
```

It scaffolds fresh in a temp dir, builds, and runs its tests. Linked names have
a walkthrough page; the rest you can read under
`src/just_makeit/examples/<name>/` or just run.

For a single guided build that touches many features at once, start with the
[feature tour](feature-tour.md). For composing objects out of objects, see the
[object-of-objects guide](object-of-objects.md).

______________________________________________________________________

## Object shapes — what `step()` looks like

| Feature                              | Example                                          |
| ------------------------------------ | ------------------------------------------------ |
| Scalar processor (`x -> y`)          | [FIR filter](examples/fir_filter.md)             |
| Generator (`void -> y`)              | [NCO tone](examples/nco_tone.md)                 |
| Consumer / sink (`x -> void`)        | [Accumulator](examples/accumulator.md)           |
| Blockwise (`T[] -> U[]`)             | [IQ file](examples/iqfile.md)                    |
| Reader (no `step()`, custom methods) | [Stream chunker](examples/stream_chunker.md)     |
| Process a whole block (`steps`)      | [Array processing](examples/array_processing.md) |

## State and lifecycle

| Feature                                     | Example                                                  |
| ------------------------------------------- | -------------------------------------------------------- |
| State variables with defaults               | [Running stats](examples/running_stats.md)               |
| Mutable state (`--mutable`)                 | [NCO tone](examples/nco_tone.md)                         |
| Opaque heap state (`create` / `destroy`)    | [Opaque counter](examples/opaque_counter.md)             |
| `create` / `reset` / `destroy` + heap field | [Delay line](examples/delay_line.md)                     |
| Declarative TOML fragment (`jm apply`)      | [Declarative scaffold](examples/declarative_scaffold.md) |
| `no_state` / user-facing init-params        | [Feature tour](feature-tour.md)                          |

## Methods and outputs

| Feature                                            | Example                                          |
| -------------------------------------------------- | ------------------------------------------------ |
| Named execute methods with params                  | [Accumulator](examples/accumulator.md)           |
| Variable-length output                             | [Array processing](examples/array_processing.md) |
| Per-call output array (`out-type` / `out-divisor`) | [Array processing](examples/array_processing.md) |
| Second output array (`multi-output`)               | `varargs_method`                                 |
| GIL release (`nogil`), `pass-capacity`             | `kitchen_sink`                                   |

## Functions and properties

| Feature                                     | Example                                                                                                                    |
| ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| Module-level C function                     | [Module functions](examples/jm_function.md)                                                                                |
| Inline function (in the header)             | [Module functions](examples/jm_function.md)                                                                                |
| Filesystem path argument (`name:path`)      | [Extend command](commands/extend.md#just-makeit-function-name---module-mod---param-nametype---return-type-type---doc-text) |
| Enum argument (`name:enum:<ename>`)         | [Extend command](commands/extend.md#just-makeit-function-name---module-mod---param-nametype---return-type-type---doc-text) |
| Raise on non-zero return (`--check-return`) | [Extend command](commands/extend.md#just-makeit-function-name---module-mod---param-nametype---return-type-type---doc-text) |
| Writable property                           | [Feature tour](feature-tour.md)                                                                                            |
| Field-backed property                       | [IQ file](examples/iqfile.md)                                                                                              |

## Modules and linking

| Feature                                    | Example                                    |
| ------------------------------------------ | ------------------------------------------ |
| Multiple types in one module `.so`         | [Filter module](examples/filter_module.md) |
| External C library (`find_package` + link) | [NCO tone](examples/nco_tone.md)           |
| Vendored C dep, `depends_on`, reexports    | `kitchen_sink`                             |

## Performance

| Feature                                 | Example                                              |
| --------------------------------------- | ---------------------------------------------------- |
| `JM_HOT` / `JM_FORCEINLINE` annotations | [FIR filter](examples/fir_filter.md)                 |
| SIMD batch dispatch (`JM_DEFINE_STEPS`) | [Sliding correlator](examples/sliding_correlator.md) |

## Composing objects out of objects

| Feature                                       | Example / guide                                 |
| --------------------------------------------- | ----------------------------------------------- |
| `kind = "handle"` — typed RAII resource class | [Composites](examples/composites.md)            |
| `kind = "capsule"` / `kind = "composer"`      | [Object-of-objects guide](object-of-objects.md) |

## Streaming

| Feature                                | Example                                      |
| -------------------------------------- | -------------------------------------------- |
| `streamable` → `stream()` / `__iter__` | `stream_source`                              |
| Blockwise streaming                    | `stream_blockwise`                           |
| Async iteration                        | `stream_source_async`                        |
| Re-framing a stream into fixed chunks  | [Stream chunker](examples/stream_chunker.md) |

## Applications and tooling

| Feature                                           | Example                                    |
| ------------------------------------------------- | ------------------------------------------ |
| C exe / console script / PEP 723 from a component | [App targets](examples/jm_app.md)          |
| Full lifecycle (build, test, bench, docs)         | [Full workflow](examples/full_workflow.md) |
| `--pytest` / `--pytest-benchmark` test styles     | [pytest style](examples/pytest_style.md)   |
