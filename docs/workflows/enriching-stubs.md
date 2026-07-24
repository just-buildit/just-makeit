# Enriching stubs from your C header

The stubs above are the *name-based fallback*. The `.pyi` docstrings are
derived directly from the Doxygen `/** ... */` comments in your
`native/inc/<obj>/<obj>_core.h`, so what you write in C surfaces in your
editor's tooltips, in the rendered Python API docs, and — for methods,
properties, and free functions — as **runnable doctests**.

Two header comments feed the class stub: `<obj>_create()` supplies the class
docstring, and each method/property is documented from its own declaration.

## The class docstring — from `create()`

Give `gain_create` a real `@brief`:

```c
/**
 * @brief Construct a scalar gain stage.
 * @param gain  Linear gain applied to each sample (default: 1.0).
 * @return Heap-allocated gain_state_t, or NULL on allocation failure.
 */
gain_state_t *gain_create(float gain);
```

Run `just-makeit apply` (or any mutating command) and the `@brief` becomes the
class summary. The `Parameters` section documents the constructor — for an
object built from `[[<obj>.init_params]]` each `@param` description flows
through; a plain `--state` object documents its state fields generically. The
`Examples` block is **synthesized by jm** (a construction call plus getter
read-backs and a reset round-trip) and runs as a doctest out of the box:

```python
class Gain:
    """Construct a scalar gain stage.

    Parameters
    ----------
    gain : float, default 1.0
        gain state variable.

    Examples
    --------
    Create with defaults:

    >>> from my_dsp import Gain
    >>> obj = Gain(1.0)
    >>> obj.get_gain()
    1.0

    Reset restores defaults:

    >>> obj.set_gain(0.0)
    >>> obj.reset()
    >>> obj.get_gain()
    1.0

    """
```

The class summary is the `@brief` line only: an extended-description paragraph
and `@return` on `create()` are not rendered into the class docstring, and the
`Examples` block is always the synthesized construction demo — never a `@code`
snippet. To author your own runnable example, put it on a **method**.

## Method docstrings — where `@code` becomes a doctest

A method added with `just-makeit method` derives its entire docstring from the
header, and a `@code` block becomes a **runnable Examples doctest**:

```c
/**
 * @brief Scale one sample by the gain and return it.
 * @param x  Input sample.
 * @return The scaled sample.
 * @code
 * >>> from my_dsp import Gain
 * >>> Gain(2.0).scale(1.5)
 * 3.0
 * @endcode
 */
float gain_scale(const gain_state_t *state, float x);
```

Regenerate and `gain.pyi` carries:

```python
    def scale(self, x: float) -> float:
        """Scale one sample by the gain and return it.

        Parameters
        ----------
        x
            Input sample.

        Returns
        -------
        float
            The scaled sample.

        Examples
        --------
        >>> from my_dsp import Gain
        >>> Gain(2.0).scale(1.5)
        3.0

        """
```

That doctest runs against the *built* extension — if the C returns `3.1`, it
fails:

```termynal
$ pytest --doctest-glob='*.pyi' src/my_dsp/gain.pyi
{d}collected 1 item{/d}

src/my_dsp/gain.pyi {g}.{/g}                                          {g}[100%]{/g}

{g}1 passed{/g} in 0.03s
```

A `just-makeit property` getter's `@brief` becomes the property docstring the
same way. The built-in `step()`/`steps()` keep their standard docstrings — a
`@param` on `step` refines the argument description, but they are not the place
for a `@brief` or `@code`.

## Which Doxygen tags jm reads

| Tag                    | On `create()`              | On a method / property / free function |
| ---------------------- | -------------------------- | -------------------------------------- |
| `@brief`               | Class summary              | Docstring summary                      |
| `@param <name> <doc>`  | `Parameters` (init-params) | `Parameters` entry                     |
| `@return` / `@returns` | — (not rendered)           | `Returns` entry                        |
| `@code` … `@endcode`   | — (not rendered)           | Runnable `Examples` doctest            |

Inline word-references — `@p name`, `@c name`, `@a`/`@e`/`@b name`, and
`@ref name` — are reduced to the bare word so the prose reads cleanly in
Python. Tags with no numpy equivalent (`@note`, `@warning`, `@see`) are
dropped.

Three things worth knowing when your docs don't seem to "take":

- **Your `@brief` must say more than the function name.** A brief that merely
    restates the name (jm's own `@brief gain_create.` scaffold shape) is treated
    as empty, and jm keeps the name-based fallback until you write something
    real.
- **`@code` examples are executed in CI.** They run against the *built* C
    extension via `pytest --doctest-glob='*.pyi'`, so a `>>>` that expects `3.0`
    fails the build if the C returns `3.1`. Write examples with deterministic,
    printable output (whole-number results avoid floating-point noise).
- **Stubs are glue — regenerate, don't hand-edit.** After editing a header,
    run `just-makeit apply` and commit the updated `.pyi` alongside it;
    `just-makeit status --check` fails the manifest-drift gate if they diverge.

For the full parsing pipeline and the CI wiring, see
[Docstring derivation (C → .pyi)](../developers/docstring-derivation.md).
