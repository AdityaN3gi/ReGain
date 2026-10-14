"""
emg_core.py - the one module the whole project uses.

    from emg_core import get_source
    src = get_source()            # real hardware.  get_source(fake=True) = no hardware needed
    src.load_calibration()        # loads calibration.json (made by calibrate.py)
    src.start()
    src.wait_ready()
    ...
    a = src.activation            # 0.0 (relaxed) .. 1.0 (your max). Read it every frame.
    ...
    src.stop()

Fake mode (for building the UI without the board):
    src = get_source(fake=True)
    src.set(0.4)                  # force a value (e.g. from mouse position). src.set(None) = auto sine wave
"""
import json
import math
import threading
import time
from collections import deque

import numpy as np

CAL_FILE = "calibration.json"
FS = 500            # must match SAMPLE_RATE in the Arduino sketch
BAUD = 115200
MAINS_HZ = 50       # India = 50
ENV_MS = 200        # envelope smoothing window


class EMGSource:
    def __init__(self, port=None):
        import serial.tools.list_ports  # noqa: F401  (fail early if pyserial is missing)
        self.port = port or self._find_port()
        self.rest = 0.0
        self.max = 1.0
        self.envelope = 0.0
        self.ready = False
        self.error = None
        self._running = False
        self._thread = None
        self._raw_lock = threading.Lock()
        self._raw_buf = []

    # ---------- connection ----------
    @staticmethod
    def _find_port():
        import serial.tools.list_ports
        devices = [p.device for p in serial.tools.list_ports.comports()]
        # Arduino boards show up as ttyACM* / ttyUSB* (Linux) or COM* (Windows).
        # Skip ttyS* - those are built-in ports and give "permission denied".
        for key in ("ttyACM", "ttyUSB", "COM"):
            for d in sorted(devices, reverse=True):   # newest replug (e.g. ACM1) first
                if key in d:
                    return d
        raise RuntimeError("No Arduino port found. Is the Uno plugged in?")

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)

    def wait_ready(self, timeout=15):
        t0 = time.time()
        while not self.ready:
            if self.error:
                raise RuntimeError(f"Serial problem: {self.error}")
            if time.time() - t0 > timeout:
                raise RuntimeError("No data from the board. Check the port, and close the Arduino Serial Monitor / live_plot.py.")
            time.sleep(0.05)

    def drain_raw(self):
        """Drain and return all accumulated raw ADC samples since last call."""
        with self._raw_lock:
            samples = self._raw_buf
            self._raw_buf = []
        return samples

    # ---------- background thread: read -> filter -> envelope ----------
    def _run(self):
        try:
            import serial
            from scipy import signal

            hp = signal.butter(2, 20, btype="highpass", fs=FS, output="sos")
            hp_z = signal.sosfilt_zi(hp) * 0
            nb, na = signal.iirnotch(MAINS_HZ, 30, fs=FS)
            nz = signal.lfilter_zi(nb, na) * 0

            n = int(FS * ENV_MS / 1000)
            sq = deque([0.0] * n, maxlen=n)
            total = 0.0
            count = 0
            leftover = b""

            ser = serial.Serial(self.port, BAUD, timeout=0.05)
            time.sleep(2)                     # the Uno resets when the port opens
            ser.reset_input_buffer()

            while self._running:
                data = leftover + ser.read(ser.in_waiting or 1)
                lines = data.split(b"\n")
                leftover = lines.pop()
                vals = []
                for line in lines:
                    try:
                        vals.append(int(line.strip()))
                    except ValueError:
                        pass
                if not vals:
                    continue

                # Buffer raw samples for fatigue analysis
                with self._raw_lock:
                    self._raw_buf.extend(vals)
                    if len(self._raw_buf) > FS * 10:  # avoid unbounded memory growth
                        del self._raw_buf[:-FS * 10]

                x = np.array(vals, dtype=float) - 512.0
                x, nz = signal.lfilter(nb, na, x, zi=nz)
                x, hp_z = signal.sosfilt(hp, x, zi=hp_z)
                for v in x:
                    s = v * v
                    total += s - sq[0]
                    sq.append(s)
                count += len(x)
                if count >= FS:               # ignore the first second (filter start-up)
                    self.envelope = math.sqrt(max(total, 0.0) / n)
                    self.ready = True
            ser.close()
        except Exception as e:                # surfaced by wait_ready()
            self.error = e

    # ---------- calibration ----------
    def _record(self, seconds):
        vals = []
        end = time.time() + seconds
        while time.time() < end:
            vals.append(self.envelope)
            time.sleep(0.01)
        v = np.array(vals)
        return v[len(v) // 5:]                # drop the first 20% (reaction time)

    def calibrate_rest(self, seconds=3):
        self.rest = float(np.mean(self._record(seconds)))
        return self.rest

    def calibrate_max(self, seconds=3):
        self.max = float(np.percentile(self._record(seconds), 95))
        return self.max

    def save_calibration(self, path=CAL_FILE):
        with open(path, "w") as f:
            json.dump({"rest": self.rest, "max": self.max}, f)

    def load_calibration(self, path=CAL_FILE):
        try:
            with open(path) as f:
                d = json.load(f)
            self.rest, self.max = float(d["rest"]), float(d["max"])
            return True
        except (OSError, KeyError, ValueError):
            return False

    # ---------- the number everyone uses ----------
    @property
    def activation(self):
        """0.0 = relaxed, 1.0 = your calibrated maximum (clamped)."""
        span = max(self.max - self.rest, 1e-6)
        return float(min(max((self.envelope - self.rest) / span, 0.0), 1.0))


class FakeEMG:
    """Same interface as EMGSource, no hardware. For UI development."""

    def __init__(self):
        self._v = None
        self._t0 = time.time()

    def set(self, value):
        self._v = value

    def start(self): pass
    def stop(self): pass
    def wait_ready(self, timeout=15): pass
    def drain_raw(self): return []
    def load_calibration(self, path=CAL_FILE): return True

    @property
    def activation(self):
        if self._v is not None:
            return max(0.0, min(1.0, float(self._v)))
        return 0.5 + 0.5 * math.sin((time.time() - self._t0) * 0.8)


def get_source(fake=False, port=None):
    return FakeEMG() if fake else EMGSource(port)
