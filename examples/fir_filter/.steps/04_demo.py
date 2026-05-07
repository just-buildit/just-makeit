import numpy as np
from my_fir import FirFilter

f = FirFilter(gain=1.0)

# Load a 3-tap averager into the first three taps
h = np.array([0.25, 0.5, 0.25] + [0.0] * 13, dtype=np.float32)
f.set_coeffs(h)

# Inspect taps without copying — read-only view, zero allocation
view = f.get_coeffs_view()
print("writeable:", view.flags["WRITEABLE"])  # False
print("h[1]:", view[1])  # 0.5

# Feed a unit impulse and read back the impulse response
impulse = np.zeros(16, dtype=np.complex64)
impulse[0] = 1.0
y = f.steps(impulse)
print("impulse response:", y[:4].real)  # [0.25 0.5  0.25 0.  ]

# Snapshot the delay line — independent copy, safe to keep indefinitely
dl = f.get_delay()
print("delay[0]:", dl[0])

# Context manager ensures destroy() on exit
with FirFilter(gain=2.0) as g:
    g.set_coeffs(h)
    y2 = g.steps(impulse)
print("gain=2 response:", y2[:3].real)  # [0.5 1.  0.5]
