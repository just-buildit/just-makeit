cd ..
just-makeit new my_fir_pure \
    --component fir_pure \
    --pure \
    --state "coeffs:float[16]" \
    --state "delay:float _Complex[16]"
cd my_fir_pure
