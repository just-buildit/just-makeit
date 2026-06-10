# Stream source example

A **source** is an object that generates samples from internal state with no
input — `steps(n)` hands you `n` fresh samples. Mark it `--streamable` and
just-makeit generates a Pythonic block iterator for free, so instead of the
hand-rolled pull loop:

```python
while True:
    block = osc.steps(256)
    consume(block)
```

you write:

```python
for block in osc.stream(256):
    consume(block)
```

This example builds a free-running ramp oscillator, marks it streamable, and
walks through everything the generator gives you: `stream(block)`, the
`count` cap, the `on_block` hook, and `__iter__`.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example stream_source
# stream_source: PASSED
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
