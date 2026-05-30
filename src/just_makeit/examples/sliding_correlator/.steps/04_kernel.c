#define CORR_CHUNK 256

JM_DEFINE_STEPS (sliding_correlator, sliding_correlator_state_t, float complex,
                 CORR_LENGTH, CORR_BATCH, CORR_CHUNK)
