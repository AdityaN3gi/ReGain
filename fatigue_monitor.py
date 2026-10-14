"""
fatigue_monitor.py - drop-in EMG fatigue tracking for biofeedback rehab games.

Needs raw EMG samples (ADC counts) at a steady rate of 500+ Hz.  pip install numpy scipy

Usage inside your game:

    from fatigue_monitor import FatigueMonitor
    fm = FatigueMonitor(fs=500)             # fs = samples/second your Arduino sends

    # 1) calibration: collect RAW samples while the player relaxes and squeezes
    fm.calibrate(rest_raw, mvc_raw)         # two lists/arrays of raw ADC values

    # 2) in your serial-reading loop, for EVERY sample:
    status = fm.feed(raw)                   # returns a dict about 4x/second, else None

    # 3) when a hold / rep begins and ends:
    fm.start_hold()
    result = fm.end_hold()                  # dict of fatigue metrics, or None if too short

    # any time (e.g. for your HUD):
    fm.status   # {'activation', 'mdf', 'mnf', 'fatigue_pct', 'onset_s'}
"""
import threading
from collections import deque
import numpy as np
from scipy.signal import welch


def _rms(x):
    x = np.asarray(x, dtype=float)
    if len(x) == 0:
        return 0.0
    return float(np.sqrt(np.mean((x - x.mean()) ** 2)))


class FatigueMonitor:
    def __init__(self, fs=500, win_s=0.5, hop_s=0.25, f_lo=20.0, f_hi=245.0,
                 mains=50.0, min_act=0.10, skip_s=0.5, ref_s=2.5,
                 drop=0.12, smooth_n=5, consec=3):
        self.fs = fs
        self.win, self.hop = max(32, int(win_s * fs)), max(16, int(hop_s * fs))
        self.nperseg = min(self.win, int(round(0.256 * fs)))
        self.f_lo = f_lo
        self.f_hi = min(f_hi, fs / 2.0 - 5.0)
        self.mains = mains
        self.min_act = min_act
        self.skip_s = skip_s
        self.ref_s = ref_s
        self.drop = drop
        self.smooth_n = smooth_n
        self.consec = consec

        self._buf = deque(maxlen=self.win)
        self._n = 0                      # sample counter = our clock
        self._lock = threading.Lock()
        self.cal = None
        self._hold = None
        self._status = dict(activation=0.0, mdf=None, mnf=None, fatigue_pct=0.0, onset_s=None)

    # ------------------------------------------------------------ calibration
    def calibrate(self, rest_raw, mvc_raw):
        rest = np.asarray(rest_raw, float)
        mvc = np.asarray(mvc_raw, float)
        min_samples = int(2.5 * self.fs)
        if len(rest) < min_samples or len(mvc) < min_samples:
            raise ValueError(f"Need at least 2.5 s of raw samples (got rest={len(rest)}, mvc={len(mvc)} at {self.fs} Hz).")

        hop = self.hop
        chunks = lambda x: [_rms(x[i:i + hop]) for i in range(0, len(x) - hop + 1, hop)] or [_rms(x)]
        rest_rms = float(np.median(chunks(rest)))
        # Skip initial ramp-up samples if available
        mvc_body = mvc[int(0.5 * self.fs):] if len(mvc) > int(1.5 * self.fs) else mvc
        mvc_rms = float(np.percentile(chunks(mvc_body), 85))

        # Safe noise estimate
        nperseg = min(len(rest), self.nperseg)
        _, noise = welch(rest - rest.mean(), self.fs, nperseg=nperseg)
        self.cal = dict(rest_rms=max(1e-3, rest_rms), mvc_rms=max(mvc_rms, rest_rms * 1.35), noise=noise)
        ok = mvc_rms >= rest_rms * 1.30
        return dict(rest_rms=rest_rms, mvc_rms=mvc_rms, ok=ok)

    # ------------------------------------------------------------- streaming
    def feed(self, raw):
        self._buf.append(float(raw))
        self._n += 1
        if self.cal is None or len(self._buf) < self.win or (self._n % self.hop != 0):
            return None

        x = np.fromiter(self._buf, float, self.win)
        r = _rms(x)
        span = max(1e-4, self.cal["mvc_rms"] - self.cal["rest_rms"])
        act = max(0.0, min(1.5, (r - self.cal["rest_rms"]) / span))

        f, P = welch(x - x.mean(), self.fs, nperseg=self.nperseg)

        # Soft background suppression without crushing valid signal spectrum
        if "noise" in self.cal and len(self.cal["noise"]) == len(P):
            P = np.maximum(P - 0.4 * self.cal["noise"], 0.05 * P)

        m = (f >= self.f_lo) & (f <= self.f_hi)
        for k in range(1, int(self.f_hi // self.mains) + 1):     # ignore mains 50 Hz + harmonics
            m &= np.abs(f - k * self.mains) > 2.0

        mnf = mdf = np.nan
        if P[m].sum() > 1e-8:
            fm, Pm = f[m], P[m]
            total_p = Pm.sum()
            mnf = float((fm * Pm).sum() / total_p)
            cum = np.cumsum(Pm)
            mdf = float(np.interp(0.5 * total_p, cum, fm))

        with self._lock:
            if self._hold is not None:
                self._hold.add(self._n / self.fs, act, mnf, mdf)
            self._status = dict(
                activation=act,
                mdf=mdf if np.isfinite(mdf) else None,
                mnf=mnf if np.isfinite(mnf) else None,
                fatigue_pct=self._hold.fatigue_pct() if self._hold else 0.0,
                onset_s=self._hold.onset if self._hold else None)
            return dict(self._status)

    @property
    def status(self):
        with self._lock:
            return dict(self._status)

    def start_hold(self):
        with self._lock:
            self._hold = _Hold(self.min_act, self.skip_s, self.ref_s,
                               self.drop, self.smooth_n, self.consec)

    def end_hold(self):
        with self._lock:
            h, self._hold = self._hold, None
        return h.summary() if h else None


class _Hold:
    """Collects the MDF values of one hold/rep and turns them into fatigue metrics."""

    def __init__(self, min_act, skip_s, ref_s, drop, smooth_n, consec):
        self.min_act = min_act
        self.skip_s = skip_s
        self.ref_s = ref_s
        self.drop = drop
        self.smooth_n = smooth_n
        self.consec = consec
        self.t, self.mdf, self.act = [], [], []
        self.t0 = None
        self.onset = None

    def add(self, t, act, mnf, mdf):
        if act < self.min_act or not np.isfinite(mdf):     # not contracting or invalid window
            return
        if self.t0 is None:
            self.t0 = t
        self.t.append(t - self.t0)
        self.mdf.append(mdf)
        self.act.append(act)
        self._check_onset()

    def _ref(self):
        """Dynamic early reference calculation: provides baseline frequency as soon as valid samples arrive."""
        if not self.t:
            return None
        t = np.array(self.t)
        m = np.array(self.mdf)
        # Windows after initial entry transient
        sel = (t >= self.skip_s) & (t <= self.skip_s + self.ref_s)
        if sel.sum() >= 2:
            return float(np.median(m[sel]))
        # Fallback to earliest valid windows
        if len(m) >= 2:
            return float(np.median(m[:min(4, len(m))]))
        return float(m[0]) if len(m) > 0 else None

    def _smooth(self):
        if not self.mdf:
            return np.array([])
        m = np.array(self.mdf)
        w = max(1, self.smooth_n)
        return np.array([np.median(m[max(0, i - w + 1):i + 1]) for i in range(len(m))])

    def fatigue_pct(self):
        if not self.t or len(self.mdf) < 2:
            return 0.0
        ref = self._ref()
        if ref is None or ref <= 0:
            return 0.0
        sm = self._smooth()
        if len(sm) == 0:
            return 0.0
        drop_pct = 100.0 * (ref - sm[-1]) / ref
        return max(0.0, min(100.0, float(drop_pct)))

    def _check_onset(self):
        if self.onset is not None:
            return
        ref = self._ref()
        if ref is None or ref <= 0 or len(self.t) < 4:
            return
        t = np.array(self.t)
        sm = self._smooth()
        # Look for steady drop below threshold after the initial reference window begins
        below = (sm < (1.0 - self.drop) * ref) & (t >= self.skip_s + 0.5)
        run = 0
        for i, b in enumerate(below):
            run = run + 1 if b else 0
            if run >= self.consec:
                self.onset = round(float(t[i - self.consec + 1]), 2)
                return

    def summary(self):
        if len(self.t) < 4:
            return None
        t, m = np.array(self.t), np.array(self.mdf)
        use = t >= self.skip_s
        if use.sum() < 3:
            use = np.ones(len(t), dtype=bool)

        ref = self._ref()
        if ref is None or ref <= 0:
            return None

        # Regression slope for frequency shift over time
        if len(t[use]) >= 2 and (t[use][-1] - t[use][0]) > 0.2:
            slope = float(np.polyfit(t[use], m[use], 1)[0])
        else:
            slope = 0.0

        recent_m = m[-min(6, len(m)):]
        total_drop = max(0.0, 100.0 * (ref - float(np.median(recent_m))) / ref)

        return dict(
            hold_s=round(float(t[-1]), 1),
            mdf_ref_hz=round(ref, 1),
            mdf_slope_hz_per_s=round(slope, 3),
            mdf_slope_pct_per_s=round(100.0 * slope / ref, 3),
            total_drop_pct=round(total_drop, 1),
            fatigue_onset_s=self.onset,
            mean_activation_pct=round(100.0 * float(np.mean(self.act)), 1))
