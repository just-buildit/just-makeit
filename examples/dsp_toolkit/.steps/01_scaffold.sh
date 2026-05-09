just-makeit new dsp_toolkit \
    --component gain \
    --arg-type float \
    --return-type float \
    --state "gain:float:1.0"
cd dsp_toolkit && make
