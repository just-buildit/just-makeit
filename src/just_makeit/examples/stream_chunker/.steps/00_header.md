# stream_chunker example

A stream re-framer: accepts samples in arbitrary-size bursts and emits them
as fixed-size chunks.  Demonstrates **variable-size input with variable-size
output** using `--variable-output` and `--no-step`.

The key concept: some calls produce zero chunks (not enough data yet); others
produce one or several.  The Python caller never knows in advance how many
samples will come back — it just checks `len(view)` after each call.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example stream_chunker
# stream_chunker: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```
