just-makeit new budget
cd budget

just-makeit object allocator \
    --init-param "capacity:size_t" \
    --init-param "slots:size_t" \
    --state "n_slots:size_t:0" \
    --state "remaining:size_t:0" \
    --state "degraded:bool:false" \
    --arg-type size_t \
    --return-type size_t
