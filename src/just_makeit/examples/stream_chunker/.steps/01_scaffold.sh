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