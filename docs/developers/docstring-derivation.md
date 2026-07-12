# Docstring derivation: C headers → Python `.pyi` stubs

`jm` generates Python type stubs (`.pyi` files) with docstrings derived
directly from the Doxygen-style `/** ... */` comments in your project's
`<component>_core.h` headers. This page explains how that pipeline works,
what Doxygen tags are supported, and how CI keeps the stubs in sync.

---

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

---

## Supported Doxygen tags

| Tag                   | Maps to in `.pyi`           |
| --------------------- | --------------------------- |
| `@brief <text>`       | First line of the docstring |
| Body text (no tag)    | Continuation of summary     |
| `@param <name> <doc>` | `Parameters` section entry  |
| `@return <doc>`       | `Returns` section entry     |
| `@code` … `@endcode`  | `Examples` section doctest  |

Tags not in this table (e.g. `@note`, `@warning`, `@see`) are silently
dropped — they have no numpy-docstring equivalent.

### Before and after: a concrete example

**C header** (`native/inc/agc/agc_core.h`):

```c
/**
 * @brief Construct a log-domain feedback AGC and return its heap state.
 * The loop integrator starts at 0 dB (unity gain) and the power detector
 * is pre-seeded to 10^(ref_db/10) linear, so the first block of
 * on-target samples produces no transient.
 *
 * @param ref_db   Target output power in dB (e.g. 0.0 for unity power).
 * @param loop_bw  Loop noise bandwidth in cycles/sample.
 * @param alpha    Power-detector EMA coefficient (0 < alpha < 1).
 * @return Heap-allocated agc_state_t, or NULL on allocation failure.
 * @code
 * >>> from doppler.agc import AGC
 * >>> agc = AGC(ref_db=0.0, loop_bw=0.0025, alpha=0.05)
 * @endcode
 */
agc_state_t *agc_create(double ref_db, double loop_bw, double alpha);
```

**Generated `.pyi`** (`src/doppler/agc/agc.pyi`):

```python
class AGC:
    """Construct a log-domain feedback AGC and return its heap state.

    The loop integrator starts at 0 dB (unity gain) and the power
    detector is pre-seeded to 10^(ref_db/10) linear, so the first
    block of on-target samples produces no transient.

    Parameters
    ----------
    ref_db : float
        Target output power in dB (e.g. 0.0 for unity power).
    loop_bw : float
        Loop noise bandwidth in cycles/sample.
    alpha : float
        Power-detector EMA coefficient (0 < alpha < 1).

    Returns
    -------
    AGC
        Heap-allocated AGC instance.

    Examples
    --------
    >>> from doppler.agc import AGC
    >>> agc = AGC(ref_db=0.0, loop_bw=0.0025, alpha=0.05)

    """

    def __init__(
        self, ref_db: float = ..., loop_bw: float = ..., alpha: float = ...
    ) -> None: ...
```

---

## `@code` blocks become runnable doctests

Code inside `@code` / `@endcode` lands in the numpy `Examples` section.
CI exercises these via:

```sh
uv run pytest --doctest-glob='*.pyi' -q $(find src/<pkg> -name '*.pyi')
```

This means the examples in your C Doxygen comments are *actually run*
against the built C extension every CI pass. If a comment says

```c
 * @code
 * >>> obj = Widget(0.5)
 * >>> obj.step(1.0)
 * 0.5
 * @endcode
```

…and the C implementation returns `0.6`, CI will catch it. Write your
`@code` examples so they produce deterministic, printable output.

---

## The manifest-drift gate

`.pyi` files are *glue files* — they are owned by the manifest-drift
gate, not by hand. After you edit a `_core.h` docstring:

1. Run `jm apply` — regenerates all glue files from the updated headers.
2. Commit the updated `.pyi` stubs alongside the header change.
3. CI runs `jm status --check`, which re-parses the headers and diffs
   the regenerated output against the committed stubs. Any mismatch
   fails the gate.

The gate ensures stubs never drift from the C source of truth.

---

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
