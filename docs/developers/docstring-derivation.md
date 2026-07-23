# Docstring derivation: C headers → Python `.pyi` stubs

`jm` generates Python type stubs (`.pyi` files) with docstrings derived
directly from the Doxygen-style `/** ... */` comments in your project's
`<component>_core.h` headers. This page explains how that pipeline works,
what Doxygen tags are supported, and how CI keeps the stubs in sync.

______________________________________________________________________

## The pipeline

```
native/inc/<obj>_core.h   ← you write Doxygen /** ... */ comments here
        │
        │  jm reads header text with regex — no Doxygen tool invoked
        ▼
just_makeit/_docstring.py:extract_doc_blocks()
        │
        │  maps each C function name → its preceding /** ... */ block
        ▼
just_makeit/_docstring.py:parse_doxygen_block()  → DoxyBlock
        │
        │  @brief  → summary line
        │  @param  → Parameters section
        │  @return → Returns section
        │  @code   → Examples section (runnable doctests)
        ▼
just_makeit/_docstring.py:render_numpy_method_doc()
        │
        │  called from _stubs.py; emits numpy-style docstring inside the
        │  .pyi method body
        ▼
src/<pkg>/<obj>.pyi   ← committed glue file; owned by the manifest-drift gate
```

**jm does not run Doxygen.** It parses the raw C header text with a
regular expression that finds `/** ... */` blocks immediately preceding
a C function declaration. The Doxygen XML pipeline (`doxygen Doxyfile`
→ `xml/` output) exists independently — CI uses it to validate zero
warnings and mkdoxy uses it to generate HTML C API docs. Neither touches
`.pyi` generation.

______________________________________________________________________

## Supported Doxygen tags

| Tag                          | Maps to in `.pyi`           |
| ---------------------------- | --------------------------- |
| `@brief <text>`              | First line of the docstring |
| Body text (no tag)           | Continuation of summary     |
| `@param <name> <doc>`        | `Parameters` section entry  |
| `@return <doc>` / `@returns` | `Returns` section entry     |
| `@code` … `@endcode`         | `Examples` section doctest  |

Tags not in this table (e.g. `@note`, `@warning`, `@see`) are silently
dropped — they have no numpy-docstring equivalent.

### Inline word-references are reduced to the bare word

Doxygen inline markup that references a single word — `@p name`, `@c name`,
`@a name`, `@e name`, `@b name`, and `@ref name` — reads as noise in a Python
docstring, so `_strip_doxy_inline` collapses each to just the word (`length @p code_len` → `length code_len`). This runs on the brief, body, params, and
return text, but **not** inside `@code` blocks, which are copied verbatim.

### A `@brief` that only restates the name is treated as empty

`parse_doxygen_block` is given the C function name, and if the only content in
the block is a brief that matches that name (ignoring `_`/spaces and case —
jm's own `@brief gain_create.` scaffold shape), it returns `None`. The
generators then keep their name-based fallback stub. This is why a freshly
scaffolded header shows the generic docstring until a human writes real docs:
a brief has to say something beyond the identifier before it "takes."

### Before and after: a concrete example

**C header** (`native/inc/agc/agc_core.h`):

```c
/**
 * @brief Construct a log-domain feedback AGC.
 * @param ref_db   Target output power in dB (e.g. 0.0 for unity power).
 * @param loop_bw  Loop noise bandwidth in cycles/sample.
 * @return Heap-allocated agc_state_t, or NULL on allocation failure.
 */
agc_state_t *agc_create(double ref_db, double loop_bw);
```

**Generated `.pyi`** (`src/doppler/agc/agc.pyi`):

```python
class AGC:
    """Construct a log-domain feedback AGC.

    Parameters
    ----------
    ref_db : float, default 0.0
        Target output power in dB (e.g. 0.0 for unity power).
    loop_bw : float, default 0.0
        Loop noise bandwidth in cycles/sample.

    Examples
    --------
    Create with defaults:

    >>> from doppler.agc import AGC
    >>> obj = AGC(ref_db=0.0, loop_bw=0.0)
    >>> obj.get_gain()
    0.0

    """

    def __init__(
        self, ref_db: float = ..., loop_bw: float = ...
    ) -> None: ...
```

The class docstring is **summary + `Parameters` + a synthesized `Examples`
block** (a construction call plus getter read-backs, and a reset round-trip
when the object has resettable state). Note what `create()`'s block does **not**
contribute to the *class* docstring: an extended-description paragraph, the
`@return` text, and any `@code` snippet are all dropped here. The `@param`
descriptions flow through only for an object built from `init_params` (a plain
`--state` object documents its fields generically). To author your own runnable
example, put `@code` on a **method** (below), not on `create()`.

______________________________________________________________________

## `@code` blocks become runnable doctests (on methods)

A `@code` / `@endcode` block on a **method** declaration (a
`just-makeit method`, a property getter, or a free function) lands in that
member's numpy `Examples` section as a runnable doctest. CI exercises these:

```sh
uv run pytest --doctest-glob='*.pyi' -q $(find src/<pkg> -name '*.pyi')
```

So the examples in your C Doxygen comments are *actually run* against the built
C extension every CI pass. If a method comment says

```c
/**
 * @brief Scale a sample by the gain.
 * @code
 * >>> obj = Widget(0.5)
 * >>> obj.scale(1.0)
 * 0.5
 * @endcode
 */
float widget_scale(const widget_state_t *state, float x);
```

…and the C implementation returns `0.6`, CI will catch it. Write your `@code`
examples so they produce deterministic, printable output. (`@code` on the
built-in `step()`/`steps()` or on `create()` is not surfaced — use a named
method.)

______________________________________________________________________

## The manifest-drift gate

`.pyi` files are *glue files* — they are owned by the manifest-drift
gate, not by hand. After you edit a `_core.h` docstring:

1. Run `jm apply` — regenerates all glue files from the updated headers.
1. Commit the updated `.pyi` stubs alongside the header change.
1. CI runs `jm status --check`, which re-parses the headers and diffs
    the regenerated output against the committed stubs. Any mismatch
    fails the gate.

The gate ensures stubs never drift from the C source of truth.

______________________________________________________________________

## What the Doxygen XML pipeline is for

Doxygen is run separately in CI for two purposes unrelated to `.pyi` generation:

- **Zero-warnings gate** (`ci.yml`): `doxygen Doxyfile 2>&1 | tee doxygen.log`
    followed by `grep -q 'warning:'` — ensures the C API comments are clean.
- **HTML C API docs** (`docs.yml`): mkdoxy reads the Doxygen XML (at
    `.mkdoxy/doppler/xml/`) to generate `docs/c-api/` Markdown, which the
    docs site builds into browsable HTML.

Neither of these touches `.pyi` generation. The two pipelines are
completely independent — jm reads header text directly; Doxygen reads
the same headers but for documentation output.
