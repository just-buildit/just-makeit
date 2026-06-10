just-makeit new stream_source_async_demo
cd stream_source_async_demo

just-makeit object ramp \
    --arg-type void \
    --return-type float \
    --mutable \
    --async-stream \
    --stream-block 256 \
    --state value:float:0.0 \
    --state step_inc:float:1.0
