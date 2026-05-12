## 1. Scaffold

```sh
just-makeit new my_chunker
cd my_chunker

just-makeit object chunker \
    --state "chunk_size:int32_t:64" \
    --state "buf:float _Complex[256]" \
    --state "n_buf:int32_t:0" \
    --no-step

just-makeit method chunker push \
    --arg-type "float _Complex" \
    --return-type "float _Complex" \
    --variable-output
```

`--no-step` suppresses `step()` and `steps()` — the only interface is `push()`.

`--variable-output` generates two C stubs in `chunker_core.c`:

| Stub | Called by ext | Your job |
|------|---------------|----------|
| `chunker_push_max_out(state)` | Once at `__init__` | Return max output samples possible |
| `chunker_push(state, in, n_in, out)` | Every Python call | Fill `out[]`, return actual count |
