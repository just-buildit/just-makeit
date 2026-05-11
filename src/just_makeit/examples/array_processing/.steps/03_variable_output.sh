# A half-band decimator: input block of N complex samples, output ≤ N/2 samples.
# Because the maximum output is known at init time (ceil(block_size / 2)),
# --variable-output pre-allocates the output buffer once and returns a view.
cd ..
just-makeit new my_decim \
    --object hbdecim \
    --arg-type "float _Complex" \
    --return-type "float _Complex" \
    --state "delay:float _Complex[12]"
cd my_decim

just-makeit method hbdecim execute \
    --arg-type "float _Complex" \
    --return-type "float _Complex" \
    --variable-output
