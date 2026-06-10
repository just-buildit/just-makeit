just-makeit new stream_source_demo
cd stream_source_demo

just-makeit object ramp \
    --arg-type void \
    --return-type float \
    --mutable \
    --streamable \
    --stream-block 256 \
    --state value:float:0.0 \
    --state step_inc:float:1.0
