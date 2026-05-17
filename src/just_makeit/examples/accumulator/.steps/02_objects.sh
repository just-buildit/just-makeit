just-makeit object acc_f32 \
    --module accumulator \
    --arg-type float \
    --return-type void \
    --state "acc:float:0.0f" \
    --mutable

just-makeit object acc_cf64 \
    --module accumulator \
    --arg-type "double _Complex" \
    --return-type void \
    --state "acc:double _Complex:0.0 + 0.0 * I" \
    --mutable
