# A half-band decimator: input block of N complex samples, output ≤ N/2 samples.
# The output count is bounded by the input length (ceil(n_in / 2)), so
# --variable-output sizes each call's output array from _max_out(n_in).
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
