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

| Tag                                 | Maps to in `.pyi`           |
| ----------------------------------- | --------------------------- |
| `@brief <text>`                     | First line of the docstring |
| Body text (no tag)                  | Continuation of summary     |
| `@param <name> <doc>`               | `Parameters` section entry  |
| `@param[in]` / `[out]` / `[in,out]` | same, direction recorded    |
| `@return <doc>` / `@returns`        | `Returns` section entry     |
| `@code` … `@endcode`                | `Examples` section doctest  |

**Either command prefix works.** Doxygen treats `@brief` and `\brief` as the
same command, and so does jm — a header written in backslash style derives
identically to one written with `@`.

### Block tags map onto numpy sections

Every other recognized command has a numpy destination (gh-652). numpy's
docstring standard already has a section for nearly all of them, so this is a
mapping table rather than a design:

| Doxygen                                                            | numpy destination             |
| ------------------------------------------------------------------ | ----------------------------- |
| `@note` `@attention` `@remark` `@pre` `@post` `@invariant` `@par`  | `Notes`                       |
| `@warning`                                                         | `Warnings`                    |
| `@see` `@sa`                                                       | `See Also`                    |
| `@throws` `@exception`                                             | `Raises`                      |
| `@retval <v> <doc>`                                                | additional `Returns` entries  |
| `@deprecated <doc>`                                                | a `.. deprecated::` directive |
| `@f$ … @f$`                                                        | `:math:` role                 |
| `@todo` `@bug` `@since` `@version` `@ingroup` `@tparam` `@copydoc` | dropped — C-side metadata     |

Sections are emitted in numpydoc's order — `Parameters`, `Returns`, `Raises`,
`See Also`, `Notes`, `Warnings`, `Examples` — because tooling that parses by
section (griffe, numpydoc's validator) mis-associates prose otherwise.

Two calls worth stating outright:

- **`@pre`/`@post` land in `Notes`, not a section of their own.** numpydoc has
    no precondition section, and inventing one puts non-standard headings into
    every downstream docs build.
- **`@retval` merges into `Returns`.** A C function returning `0`/`-1` becomes
    a Python method that raises or returns a value; the per-value rows read
    correctly there and have nowhere else to go.

The tags in the final row are dropped *deliberately* — they describe the C
side and have no Python meaning. They are listed here so "dropped" is an
auditable decision rather than an omission.

### Why every command is recognized, even the dropped ones

The distinction between "recognized then dropped" and "not recognized" is the
whole point. An unrecognized tag is not inert — it is prose, and prose attaches
to whichever section is currently open, so a `@note` after a `@return` used to
land inside the return description (gh-641). Recognizing every command, whether
or not jm has a destination for it, is what keeps that from happening.

### Math markup and raw strings

`@f$ … @f$` becomes a `:math:` role, which means a backslash reaches the
generated `.pyi`. In a plain triple-quoted string `\l` is an invalid escape
sequence — a `SyntaxWarning` on 3.12+ **in the generated project**. jm
therefore emits the stub docstring as a raw string (`r"""`) whenever the
rendered text contains a backslash, which preserves the markup for a docs build
where escaping it (`\\log`) would not. Docstrings with no backslash are
unaffected.

### Inline references are reduced to their argument

Doxygen inline markup that marks the next token — `@p name`, `@c name`,
`@a name`, `@e name`, `@b name`, and `@ref name` — reads as noise in a Python
docstring, so `_strip_doxy_inline` drops the marker and keeps the argument
(`length @p code_len` → `length code_len`). This runs on the brief, body,
params, and return text, but **not** inside `@code` blocks, which are copied
verbatim.

The argument does not have to be an identifier. `@c -1`, `@c "A"` and
`@c +/-10^(clip_db/20)` are all ordinary Doxygen, and all have the marker
removed (gh-641).

A line that *begins* with one of these is prose, not a tag — `@ref demo_reset is the counterpart` stays in the body with just the marker stripped.

### A `@brief` that only restates the name is treated as empty

`_docstring.is_scaffold_doc` is the single definition of "this is jm's own
boilerplate, not documentation", and the generators keep their name-based
fallback stub whenever it says yes. This is why a freshly scaffolded header
shows the generic docstring until a human writes real docs: a brief has to say
something beyond the identifier before it "takes."

The sentinel has two strengths, because the evidence differs:

| Brief                     | Example                    | Counts as scaffold when                |
| ------------------------- | -------------------------- | -------------------------------------- |
| one of jm's own templates | `@brief Get current gain.` | always — jm wrote that string          |
| just the member's name    | `@brief tune.`             | nothing else in the block is filled in |

The second is deliberately weaker: `@brief tune.` is equally what a terse
author writes. So if the brief is still the sentinel but a `@param` or
`@return` has a description, the block is kept and that prose is derived —
a half-filled skeleton never discards what someone actually wrote. Body prose
and a `@code` example override both strengths, since no jm scaffold emits
either.

### The scaffold skeleton

`jm method` writes a prose-free Doxygen skeleton above each new declaration
(`_docstring.scaffold_doc_block`), so the author supplies prose and never
structure:

```c
/**
 * @brief tune.
 *
 * @param state
 * @param x
 * @param hz
 */
void fir_tune(fir_state_t *state, double x, double hz);
```

Three properties are load-bearing:

- **No invented descriptions.** A generated `@param hz  double parameter.` is
    not documentation, and once it is in the header nothing can distinguish it
    from prose a human typed — so it would derive into the `.pyi` as if
    authored.
- **No `@code` block.** A placeholder example would be executed by the
    generated project's doctest gate.
- **Bare `@param` still satisfies `WARN_NO_PARAMDOC`** (verified against
    Doxygen 1.15), so a fresh scaffold is not noisy under that flag, while
    *omitting* a parameter still warns.

Only a genuinely new declaration is decorated. A refreshed signature replaces
the prototype line alone, so re-running a command never stamps a skeleton over
prose already written. Doxygen binds the **nearest** preceding block, and so
does jm — a hand-written block placed above the skeleton wins.

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
