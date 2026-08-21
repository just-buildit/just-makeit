# record_shapes example

One object, three methods, three different *shapes* of result — and the only
thing that changes between them is which key the method declares.

| declares | returns | the C kernel writes |
| ---------------------- | -------------------------------------- | ------------------------------- |
| `single` | ONE record, a named `PyStructSequence` | the struct **by value** |
| `record_dtype` | an ARRAY of records, a structured `ndarray` | `<struct> *out` |
| neither (just `result_fields`) | a `list[tuple]` | `<row> *result, size_t max_results` |

`result_fields` appears in all three, which is exactly why they are worth
seeing side by side: on its own it does not tell you what you get back.

**All three name a struct of yours as the return type** — that part does not
vary, which is what makes the key the only variable. jm never sees a field of
any of them: it writes the prototypes that mention them and, for
`record_dtype`, emits C that builds the numpy dtype at runtime from
`offsetof` and `sizeof`.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example record_shapes
# record_shapes: PASSED
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
