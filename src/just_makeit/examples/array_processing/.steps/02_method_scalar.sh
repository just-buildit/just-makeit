# Add a second execute method with a different I/O type.
# This object produces uint32 phase words in addition to float output.
just-makeit method ema quantize \
    --arg-type float \
    --return-type uint32_t
