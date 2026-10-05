just-makeit property cf32_to_q15 samples_written \
    --module conv --type uint32_t --field \
    --doc "Total complex samples written since construction."

just-makeit property q15_to_cf32 samples_read \
    --module conv --type uint32_t --field \
    --doc "Total complex samples read since construction."

just-makeit property q15_to_cf32 eof \
    --module conv --type int32_t
