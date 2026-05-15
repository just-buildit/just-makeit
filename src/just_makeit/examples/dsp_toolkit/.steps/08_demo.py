"""Demo: use Gain and Ema together from Python."""

import sys

sys.path.insert(0, "src")

import numpy as np
from dsp_toolkit import Gain, Ema

# A short burst followed by silence
signal = np.ones(20, dtype=np.float32)
signal[10:] = 0.0

gain = Gain(gain=2.0)
ema = Ema(alpha=0.3)

print(f"{'n':>3}  {'input':>7}  {'gained':>7}  {'smoothed':>10}")
print("-" * 36)
for i, x in enumerate(signal):
    y_gain = gain.step(x)
    y_ema = ema.step(float(y_gain))
    print(f"{i:>3}  {x:>7.3f}  {y_gain:>7.3f}  {y_ema:>10.4f}")
