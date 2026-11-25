# AI-written test paragraphs (synthetic)

All 10 paragraphs here were written by an AI model, including the ones that imitate
student writing (typos, simple sentences, non-native grammar...). They test that the
code runs and show how the detector reacts to different AI styles.

They cannot set a threshold on their own: that needs real human writing. Put real
paragraphs in `tests/fixtures/ai_calibration_real/` (see its README) and run

    python scripts/check_ai_thresholds.py --folder tests/fixtures/ai_calibration_real
