# AccF32 named methods
just-makeit method acc_f32 get \
    --module accumulator \
    --arg-type void \
    --return-type float

just-makeit method acc_f32 dump \
    --module accumulator \
    --arg-type void \
    --return-type float

just-makeit method acc_f32 madd \
    --module accumulator \
    --arg-type void \
    --return-type void \
    --param "x:float[]" \
    --param "h:float[]"

just-makeit method acc_f32 add2d \
    --module accumulator \
    --arg-type void \
    --return-type void \
    --param "x:float[]"

just-makeit method acc_f32 madd2d \
    --module accumulator \
    --arg-type void \
    --return-type void \
    --param "x:float[]" \
    --param "h:float[]"

# AccCf64 named methods
just-makeit method acc_cf64 get \
    --module accumulator \
    --arg-type void \
    --return-type "double _Complex"

just-makeit method acc_cf64 dump \
    --module accumulator \
    --arg-type void \
    --return-type "double _Complex"

just-makeit method acc_cf64 madd \
    --module accumulator \
    --arg-type void \
    --return-type void \
    --param "x:double _Complex[]" \
    --param "h:float[]"

just-makeit method acc_cf64 add2d \
    --module accumulator \
    --arg-type void \
    --return-type void \
    --param "x:double _Complex[]"

just-makeit method acc_cf64 madd2d \
    --module accumulator \
    --arg-type void \
    --return-type void \
    --param "x:double _Complex[]" \
    --param "h:float[]"
