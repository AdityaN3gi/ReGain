"""
ReGain - Steps 2 and 3: read the Uno over USB and show a live EMG plot.

Top plot:    filtered EMG signal (DC offset, drift and 50 Hz hum removed)
Bottom plot: envelope (smoothed muscle activity) - this is the number the game will use

Install once:   pip install pyserial numpy scipy matplotlib
Run:            python live_plot.py            (auto-finds the Uno)
                python live_plot.py COM3       (or give the port yourself)
"""
import sys
import time
from collections import deque

import numpy as np
import serial
import serial.tools.list_ports
from scipy import signal
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

BAUD = 115200
FS = 500                  # must match SAMPLE_RATE in the Arduino sketch
SECONDS_SHOWN = 5
ENVELOPE_MS = 200         # smoothing window for the envelope
MAINS_HZ = 50             # India uses 50 Hz (use 60 in the US)


def find_port():
    ports = list(serial.tools.list_ports.comports())
    for p in ports:
        text = (p.description + " " + p.hwid).lower()
        if "arduino" in text or "ch340" in text or "usb" in text or "acm" in p.device.lower():
            return p.device
    if ports:
        return ports[0].device
    sys.exit("No serial port found. Is the Uno plugged in?")


port = sys.argv[1] if len(sys.argv) > 1 else find_port()
print("Using port:", port)
ser = serial.Serial(port, BAUD, timeout=0)
time.sleep(2)                 # the Uno resets when the port opens
ser.reset_input_buffer()

# --- Filters (run sample by sample as data arrives) ---
hp_sos = signal.butter(2, 20, btype="highpass", fs=FS, output="sos")   # remove drift below 20 Hz
hp_state = signal.sosfilt_zi(hp_sos) * 0
nb, na = signal.iirnotch(MAINS_HZ, 30, fs=FS)                          # remove mains hum
notch_state = signal.lfilter_zi(nb, na) * 0

N = FS * SECONDS_SHOWN
filtered = deque([0.0] * N, maxlen=N)
leftover = b""


def read_new_samples():
    """Read whatever bytes have arrived and turn complete lines into numbers."""
    global leftover
    data = leftover + ser.read(ser.in_waiting or 1)
    lines = data.split(b"\n")
    leftover = lines.pop()    # last piece may be an unfinished line
    values = []
    for line in lines:
        try:
            values.append(int(line.strip()))
        except ValueError:
            pass              # ignore garbled lines
    return values


def process(values):
    global hp_state, notch_state
    x = np.array(values, dtype=float) - 512.0          # centre the ADC reading
    x, notch_state = signal.lfilter(nb, na, x, zi=notch_state)
    x, hp_state = signal.sosfilt(hp_sos, x, zi=hp_state)
    filtered.extend(x)


def envelope(x):
    """RMS over a short window: square, average, square root."""
    n = int(FS * ENVELOPE_MS / 1000)
    return np.sqrt(np.convolve(x ** 2, np.ones(n) / n, mode="full")[: len(x)])


# --- Plot ---
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
t = np.linspace(-SECONDS_SHOWN, 0, N)
(line1,) = ax1.plot(t, np.zeros(N))
(line2,) = ax2.plot(t, np.zeros(N), color="tab:orange")
ax1.set_title("Filtered EMG")
ax2.set_title("Envelope (muscle activity)")
ax2.set_xlabel("seconds")
ax1.set_ylim(-300, 300)
ax2.set_ylim(0, 200)


def update(_):
    new = read_new_samples()
    if new:
        process(new)
    x = np.array(filtered)
    line1.set_ydata(x)
    env = envelope(x)
    line2.set_ydata(env)
    # let the envelope axis grow if the signal gets big
    ax2.set_ylim(0, max(50, env.max() * 1.2))
    return line1, line2


ani = FuncAnimation(fig, update, interval=40, blit=False, cache_frame_data=False)
plt.tight_layout()
plt.show()
ser.close()
