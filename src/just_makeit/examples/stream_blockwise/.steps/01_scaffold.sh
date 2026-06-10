just-makeit new stream_blockwise_demo
cd stream_blockwise_demo

# The object: finite state (total samples, current position), no step().
just-makeit object drainer \
    --arg-type void \
    --return-type "float _Complex" \
    --mutable \
    --streamable \
    --variable-output \
    --state total:int32_t:20 \
    --state pos:int32_t:0
