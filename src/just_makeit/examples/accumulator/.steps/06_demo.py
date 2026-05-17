"""Quick demo: AccF32 and AccCf64 from Python."""

import sys

sys.path.insert(0, "src")

import numpy as np

from my_acc.accumulator import AccCf64, AccF32

# --- AccF32: step == push ---
f = AccF32()
f.step(np.float32(1.0))
f.step(np.float32(2.0))
f.step(np.float32(3.0))
print(f"AccF32 after push 1+2+3: get() = {f.get()}")  # 6.0

# steps == batch add
f.reset()
f.steps(np.ones(100, dtype=np.float32))
print(f"AccF32 after steps(ones*100): get() = {f.get()}")  # 100.0

# dump: atomic get + reset
f.reset()
f.step(np.float32(42.0))
v = f.dump()
print(f"AccF32 dump() = {v}, get() after = {f.get()}")  # 42.0, 0.0

# madd: weighted sum
f.reset()
x = np.array([1, 2, 3, 4], dtype=np.float32)
h = np.array([0.25, 0.25, 0.25, 0.25], dtype=np.float32)
f.madd(x, h)
print(f"AccF32 madd([1,2,3,4], [0.25]*4): get() = {f.get()}")  # 2.5

# add2d: 2-D shaped accumulate (flattened through C)
f.reset()
mat = np.arange(12, dtype=np.float32).reshape(3, 4)
for row in mat:
    f.add2d(row)
print(f"AccF32 add2d(3x4 arange): get() = {f.get()}")  # 66.0

# --- AccCf64: step with complex ---
c = AccCf64()
c.step(1 + 2j)
c.step(3 + 4j)
g = c.get()
print(f"AccCf64 after push (1+2j)+(3+4j): get() = {g}")  # (4+6j)

# AccCf64 madd: complex signal, real weights
c.reset()
sig = np.array([1 + 1j, 2 + 2j, 3 + 3j], dtype=np.complex128)
w = np.array([1.0, 0.5, 0.25], dtype=np.float32)
c.madd(sig, w)
g2 = c.get()
# (1+1j)*1.0 + (2+2j)*0.5 + (3+3j)*0.25 = (2.75+2.75j)
print(f"AccCf64 madd: get() = {g2}")

# AccCf64 dump: returns value and zeroes
c.reset()
c.step(5 + 6j)
dumped = c.dump()
print(f"AccCf64 dump() = {dumped}, get() after = {c.get()}")
