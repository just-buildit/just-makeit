# Stream blockwise example

A **blockwise** producer pulls a block of samples per call and returns however
many it had — a short or empty block once the source runs dry. The classic
shape is a `--variable-output` method: `run(n) -> array`. Mark the object
`--streamable` and just-makeit drives that method with a Pythonic iterator that
**stops on its own when the source drains**, so instead of:

```python
while len(block := decoder.run(4096)):
    consume(block)
```

you write:

```python
for block in decoder.stream(4096):
    consume(block)
```

This example builds a finite "drainer" — a source of exactly `total` complex
samples that empties as you pull it — marks it streamable, and shows the drain,
`count`, `on_block`, and `__iter__`, plus the one gotcha that comes with
zero-copy output: **copy each block before the next call.**

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example stream_blockwise
# stream_blockwise: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Or with `pip` if just-makeit is already installed:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```
