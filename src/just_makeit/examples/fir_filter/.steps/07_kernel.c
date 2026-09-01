#define FIR_CHUNK 256 /* tuning: samples per scratch-buffer fill */

JM_DEFINE_STEPS (fir_filter, fir_filter_state_t, float _Complex, FIR_LENGTH,
                 FIR_BATCH, FIR_CHUNK)
