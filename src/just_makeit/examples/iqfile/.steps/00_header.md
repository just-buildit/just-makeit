# iqfile example

A block-wise converter between **cf32** (complex float-32, 8 bytes/sample)
and **q15** (complex signed 16-bit fixed-point, 4 bytes/sample) — the two
most common raw IQ file formats in software-defined radio.

```
cf32: [f32_i, f32_q, f32_i, f32_q, ...]   8 bytes per complex sample
q15:  [i16_i, i16_q, i16_i, i16_q, ...]   4 bytes per complex sample
```

This example builds a complete, installable Python package that demonstrates
every major just-makeit feature in one project:

| Feature                              | Where                                    |
| ------------------------------------ | ---------------------------------------- |
| Module subpackage (single `.so`)     | `conv` module                            |
| Two objects sharing one extension    | `Cf32ToQ15`, `Q15ToCf32`                 |
| Generator object (`--arg-type void`) | `Q15ToCf32` reads from a file descriptor |
| Field-backed property (`--field`)    | `samples_read`, `samples_written`        |
| Computed read-only property          | `eof` on `Q15ToCf32`                     |
| `pip install -e .` dev workflow      | step 6                                   |
| Wheel build (`just-makeit build`)    | step 8                                   |

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example iqfile
# iqfile: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Pass a custom path to keep the venv somewhere persistent:

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh) -- ~/my-venv
```

Or with `pip` if just-makeit is already installed:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```
