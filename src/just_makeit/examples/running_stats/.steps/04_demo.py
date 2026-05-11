import numpy as np
from my_stats import RunningStats

# All defaults are 0 — no arguments needed
s = RunningStats()

# Classic Welford test dataset: mean=5, variance=4
data = np.array([2, 4, 4, 4, 5, 5, 7, 9], dtype=np.complex64)
for x in data:
    y = s.step(x)

print(f"n:        {s.get_n()}")  # 8
print(f"mean:     {s.get_mean():.4f}")  # 5.0000
print(f"variance: {y.imag:.4f}")  # 4.0000  (packed into imag of last step)

# reset and try a single-pass block via steps()
s.reset()
y_all = s.steps(data)
print(f"final mean from steps(): {y_all[-1].real:.4f}")  # 5.0000
print(f"final var  from steps(): {y_all[-1].imag:.4f}")  # 4.0000
