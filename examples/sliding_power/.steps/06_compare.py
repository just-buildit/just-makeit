"""Drift comparison: recursive O(1) vs calibrated vs standard MA.

Three demonstrations:
  1. Power tracking on a burst signal — all three converge identically
  2. Drift over a long run using a float32 accumulator (what the C code avoids
     by keeping sum_sq as double)
  3. Same long run with a double accumulator — shows why the C code uses double

Run from anywhere — no C build required:
    python3 examples/sliding_power/.steps/06_compare.py
"""
import math
import random
import struct
import sys

N = 64
CAL_EVERY = 1000

def f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]


# ---------------------------------------------------------------------------
# Three accumulator strategies, parametric on accumulator precision

class RecursiveNoCal:
    def __init__(self, double_acc=True):
        self.delay = [f32(0.0)] * N
        self.sum_sq = 0.0
        self.pos = 0
        self._double = double_acc

    def step(self, mag_sq):
        mag_sq = f32(mag_sq)
        delta = float(mag_sq) - float(self.delay[self.pos])
        if self._double:
            self.sum_sq += delta
        else:
            self.sum_sq = f32(f32(self.sum_sq) + f32(delta))  # float32 acc
        self.delay[self.pos] = mag_sq
        self.pos = (self.pos + 1) & (N - 1)
        return self.sum_sq / N


class RecursiveCalibrated:
    def __init__(self, double_acc=True):
        self.delay = [f32(0.0)] * N
        self.sum_sq = 0.0
        self.pos = 0
        self._double = double_acc
        self._tick = 0

    def step(self, mag_sq):
        mag_sq = f32(mag_sq)
        delta = float(mag_sq) - float(self.delay[self.pos])
        if self._double:
            self.sum_sq += delta
        else:
            self.sum_sq = f32(f32(self.sum_sq) + f32(delta))
        self.delay[self.pos] = mag_sq
        self.pos = (self.pos + 1) & (N - 1)
        self._tick += 1
        if self._tick % CAL_EVERY == 0:
            self.sum_sq = sum(float(v) for v in self.delay)
        return self.sum_sq / N


class StandardMA:
    def __init__(self):
        self.delay = [f32(0.0)] * N
        self.pos = 0

    def step(self, mag_sq):
        self.delay[self.pos] = f32(mag_sq)
        self.pos = (self.pos + 1) & (N - 1)
        return sum(float(v) for v in self.delay) / N


# ---------------------------------------------------------------------------
# Demo 1: burst tracking

def demo_burst():
    print("=" * 68)
    print("Demo 1: Power tracking on a burst signal")
    print(f"  Window N={N}  (response time = N/2 ≈ {N//2} samples)")
    print("=" * 68)

    rec = RecursiveNoCal()
    ma  = StandardMA()

    rng = random.Random(42)

    def noise(power):
        return f32(rng.gauss(0, math.sqrt(power)))

    segments = [
        ("noise P=1.0",  200, 1.0),
        ("silence P=0",  100, 0.0),
        ("noise P=0.25", 200, 0.25),
        ("silence P=0",   64, 0.0),
        ("noise P=1.0",  128, 1.0),
    ]

    print(f"  {'sample':>7}  {'segment':<16}  {'recursive':>10}  {'std MA':>10}  {'diff':>10}")
    print("  " + "-" * 60)

    n = 0
    for label, length, pwr in segments:
        for i in range(length):
            x = noise(pwr)
            r = rec.step(x * x)
            m = ma.step(x * x)
        print(f"  {n + length:>7d}  {label:<16}  {r:>10.4f}  {m:>10.4f}  {abs(r-m):>10.2e}")
        n += length

    print()
    print("  → Recursive and standard MA produce identical output (same window sum).")
    print()


# ---------------------------------------------------------------------------
# Demo 2: drift — float32 accumulator

def demo_drift_f32(n_samples=5_000_000, print_every=500_000):
    print("=" * 68)
    print("Demo 2: Drift over long run — float32 accumulator")
    print(f"  {n_samples:,} noise samples, calibration every {CAL_EVERY}")
    print("=" * 68)

    rec = RecursiveNoCal(double_acc=False)
    cal = RecursiveCalibrated(double_acc=False)
    ma  = StandardMA()

    rng = random.Random(0)
    true_power = 1.0  # unit-variance noise

    print(f"  {'sample':>10}  {'no-cal':>10}  {'calibrated':>10}  {'std MA':>10}  {'err(no-cal)':>12}  {'err(cal)':>10}")
    print("  " + "-" * 72)

    for n in range(n_samples):
        x = f32(rng.gauss(0, 1.0))
        msq = f32(x * x)
        r = rec.step(msq)
        c = cal.step(msq)
        m = ma.step(msq)

        if n % print_every == (print_every - 1):
            print(f"  {n+1:>10,d}  {r:>10.4f}  {c:>10.4f}  {m:>10.4f}  {abs(r-m):>12.2e}  {abs(c-m):>10.2e}")

    print()
    print("  → Float32 accumulator drifts measurably. Calibration corrects it.")
    print()


# ---------------------------------------------------------------------------
# Demo 3: same long run with double accumulator (C code's actual behavior)

def demo_drift_f64(n_samples=5_000_000, print_every=500_000):
    print("=" * 68)
    print("Demo 3: Same run — double accumulator (what the C code uses)")
    print(f"  {n_samples:,} noise samples")
    print("=" * 68)

    rec = RecursiveNoCal(double_acc=True)
    ma  = StandardMA()

    rng = random.Random(0)

    print(f"  {'sample':>10}  {'recursive':>10}  {'std MA':>10}  {'err':>12}")
    print("  " + "-" * 50)

    for n in range(n_samples):
        x = f32(rng.gauss(0, 1.0))
        msq = f32(x * x)
        r = rec.step(msq)
        m = ma.step(msq)

        if n % print_every == (print_every - 1):
            print(f"  {n+1:>10,d}  {r:>10.4f}  {m:>10.4f}  {abs(r-m):>12.2e}")

    print()
    print("  → Double accumulator: error stays at float32 quantization noise floor.")
    print("    Calibration is still useful as insurance, not a necessity here.")
    print()


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5_000_000
    demo_burst()
    demo_drift_f32(n)
    demo_drift_f64(n)
