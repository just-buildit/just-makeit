just-makeit new studio
cd studio

# The object a source composes into: an ordinary jm component whose
# create/step/steps/reset/destroy are exactly what a composer expects.
just-makeit object clip \
    --state "level:double:0.0" \
    --arg-type void \
    --return-type "float _Complex"
