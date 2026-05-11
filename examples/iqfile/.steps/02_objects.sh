just-makeit object cf32_to_q15 \
    --module conv \
    --arg-type "float _Complex" \
    --return-type int32_t \
    --state "scale:float:32767.0f"

just-makeit object q15_to_cf32 \
    --module conv \
    --arg-type void \
    --return-type "float _Complex" \
    --state "fd:int32_t:-1" \
    --state "scale:float:32767.0f"
