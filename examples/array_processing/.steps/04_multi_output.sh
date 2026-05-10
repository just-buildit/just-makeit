# Two parallel output streams from one call:
# primary: float _Complex (filtered samples)
# secondary: uint8_t (per-sample overflow flag)
just-makeit method hbdecim execute_ovf \
    --arg-type "float _Complex" \
    --return-type "float _Complex" \
    --variable-output \
    --multi-output uint8_t
