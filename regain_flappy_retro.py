"""ReGain Flap - a Pokemon / Nintendo-DS-style bird game. Fly a bird with your muscle. EMG biofeedback for rehab and isometric training.

Run:   python3 regain.py              (demo mode, no hardware)
       python3 regain.py auto         (real Arduino, port auto-detected)
       python3 regain.py /dev/ttyACM0 (real Arduino, port given)
Needs: pip install pygame pyserial numpy scipy, and emg_core.py + fatigue_monitor.py in the same folder

The Arduino sketch streams raw samples; emg_core.py filters them and computes the envelope.
Not a medical device. Use under a physiotherapist's plan.

How the path works: after calibration, 0% = your relaxed level (bottom of the sky)
and 100% = your max squeeze (top). The gap in each pipe sits at the therapist's
target zone, so the path is always built from YOUR relaxed and max values.
Demo controls: hold SPACE to squeeze, or press 0-9 for a steady 0%-90% level.

Reflex monitoring: every pipe starts with a GO cue (time until your effort reaches the low edge of the
target zone) and ends with a RELAX cue (time until your effort drops to 15% or less). Both times are
shown after each cue, summarised at the end, and appended to reflex_log.csv so progress can be compared
between sessions. In Training mode only the squeeze reflex is measured (one long hold, no rest cues).

Fatigue tracking: while you hold, the raw signal is analysed with fatigue_monitor.py. As the muscle tires its
median frequency sinks; the point where it has dropped clearly below its starting value is the FATIGUE ONSET.
Shown live in the Muscle Fatigue box, summarised at the end, and appended to fatigue_log.csv.
"""
import sys, time, threading, random, os, math, csv
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pygame
try:
    import serial
except ImportError:
    serial = None

W, H = 1000, 640
GROUND = 72
PT, PB = 120, H - GROUND - 34          # y of 100% (max) and 0% (relaxed)
OX, PPS = 230, 150                      # bird x position, scroll speed (px/s)
T0 = (W - OX) / PPS + .6                # first pipe enters from the right edge
NAVY, INK, MUT = (10, 22, 40), (236, 244, 250), (150, 172, 194)
TEAL, AMBER, RED, GREEN = (47, 208, 184), (255, 184, 48), (232, 84, 74), (112, 198, 56)
DIRNAME = os.path.dirname(os.path.abspath(__file__))
BEST_FILE = os.path.join(DIRNAME, "regain_best.txt")
RX_LOG = os.path.join(DIRNAME, "reflex_log.csv")
FAT_LOG = os.path.join(DIRNAME, "fatigue_log.csv")
RELAX_THR = 0.15          # effort at or below this counts as relaxed
RX_TIMEOUT = 4.0          # seconds to respond to a cue before it counts as missed
SESSION_ID = time.strftime("%Y%m%d-%H%M%S")
FATIGUE_READY = True

scr = None
import regain_climb_retro as C
from regain_climb_retro import (text, lerp, star, bevel, focus, button, window, groupbox, progress, trackbar, hot, clicked,
                                menu_bar_rects, menu_item_at, draw_menu, chrome, status, MOUSE, BLK, WHITE, FACE, SW, SH, CX, CY)


def use(surf):
    """Point this module's drawing and the shared retro toolkit at the same surface."""
    global scr
    scr = surf; C.scr = surf


def A(v):                                # activation (0-1) -> screen y
    v_clamped = max(0.0, min(1.0, v))
    return PB - (PB - PT) * v_clamped


GAIN = 4.0   # scales the real envelope (ADC counts) up to the 0-1000 range this UI was designed for


class Signal(C.Signal):
    """The shared signal from regain_climb_retro (envelope, raw-sample tap, fatigue tracking), tuned for this game's gain."""
    GAIN = 4.0

    def __init__(self, port):
        super().__init__("demo" if port is None else port)


# ---------------------------------------------------------------- art assets
class Art:
    def __init__(self):
        self.sky = pygame.Surface((W, H))
        for y in range(H):
            pygame.draw.line(self.sky, lerp((70, 178, 235), (212, 242, 255), y / (H - GROUND)), (0, y), (W, y))
        self.hills = [self._hills((150, 214, 190), 70, 60, 1.0), self._hills((120, 200, 150), 30, 42, 2.4)]
        self.clouds = []
        for i in range(7):
            s = pygame.Surface((220, 100), pygame.SRCALPHA)
            for cx, cy, r in ((60, 60, 34), (100, 44, 44), (146, 58, 36), (110, 66, 34)):
                pygame.draw.circle(s, (255, 255, 255, 215), (cx, cy), r)
            sc = random.uniform(.6, 1.1)
            self.clouds.append((pygame.transform.smoothscale(s, (int(220 * sc), int(100 * sc))),
                                random.uniform(0, W + 300), random.randint(50, 330), random.uniform(.12, .4)))
        self.tile = 48
        g = pygame.Surface((W + self.tile, GROUND))
        g.fill((226, 208, 140))
        for x in range(-GROUND, W + self.tile, self.tile):
            pygame.draw.polygon(g, (214, 194, 120), [(x + 24, 20), (x + 48, 20), (x + 24 + GROUND, GROUND), (x + GROUND, GROUND)])
        pygame.draw.rect(g, (116, 204, 70), (0, 0, W + self.tile, 20))
        pygame.draw.rect(g, (84, 170, 50), (0, 16, W + self.tile, 6))
        self.ground = g
        self.birds = [self._bird(w) for w in (-1, 0, 1, 0)]
        self.warn = pygame.Surface((W, 150), pygame.SRCALPHA)
        for y in range(150):
            pygame.draw.line(self.warn, (232, 60, 50, int(150 * (1 - y / 150) ** 2)), (0, y), (W, y))

    @staticmethod
    def _hills(col, base, amp, ph):
        s = pygame.Surface((W, H), pygame.SRCALPHA)
        pts = [(x, H - GROUND - base - amp * (.5 * math.sin(2 * math.pi * x / W + ph) + .3 * math.sin(4 * math.pi * x / W + ph * 2)))
               for x in range(0, W + 1, 8)]
        pygame.draw.polygon(s, col, pts + [(W, H), (0, H)])
        return s

    @staticmethod
    def _bird(wing):
        S = pygame.Surface((256, 256), pygame.SRCALPHA)
        out = (70, 44, 20)
        pygame.draw.polygon(S, out, [(40, 120), (6, 96), (14, 150)])
        pygame.draw.polygon(S, (255, 170, 40), [(48, 122), (22, 104), (28, 146)])
        pygame.draw.circle(S, out, (124, 130), 96)
        pygame.draw.circle(S, (255, 206, 50), (124, 130), 86)
        pygame.draw.ellipse(S, (255, 236, 168), (84, 150, 120, 62))
        pygame.draw.ellipse(S, out, (44, 118 + wing * 22, 96, 62))
        pygame.draw.ellipse(S, (255, 150, 40), (52, 126 + wing * 22, 80, 46))
        pygame.draw.circle(S, out, (166, 98), 38)
        pygame.draw.circle(S, (255, 255, 255), (166, 98), 31)
        pygame.draw.circle(S, (20, 20, 30), (176, 99), 14)
        pygame.draw.polygon(S, out, [(184, 126), (252, 140), (184, 172)])
        pygame.draw.polygon(S, (255, 120, 40), [(190, 134), (240, 142), (190, 162)])
        return pygame.transform.smoothscale(S, (68, 68))


def backdrop(art, sc):
    scr.blit(art.sky, (0, 0))
    for h, sp in zip(art.hills, (.05, .12)):
        x = -(sc * sp * PPS) % W
        scr.blit(h, (x - W, 0)); scr.blit(h, (x, 0))
    for img, bx, by, sp in art.clouds:
        scr.blit(img, ((bx - sc * sp * PPS) % (W + 400) - 220, by))


def draw_ground(art, sc):
    scr.blit(art.ground, (-((sc * PPS) % art.tile), H - GROUND))


def pipe_part(x0, x1, y0, y1, cap_at, bad):
    if x1 <= x0 or y1 <= y0:
        return
    base = lerp(GREEN, RED, bad)
    hi, sh, ol = lerp((156, 230, 96), (255, 140, 130), bad), lerp((74, 150, 40), (150, 40, 36), bad), lerp((38, 92, 30), (100, 24, 24), bad)
    pygame.draw.rect(scr, base, (x0, y0, x1 - x0, y1 - y0))
    pygame.draw.rect(scr, hi, (x0 + 6, y0, 10, y1 - y0))
    pygame.draw.rect(scr, sh, (x1 - 16, y0, 12, y1 - y0))
    cy = y1 - 30 if cap_at == "bottom" else y0
    pygame.draw.rect(scr, base, (x0 - 6, cy, x1 - x0 + 12, 30), border_radius=7)
    pygame.draw.rect(scr, hi, (x0 - 2, cy + 3, 10, 24), border_radius=4)
    pygame.draw.rect(scr, ol, (x0 - 6, cy, x1 - x0 + 12, 30), 3, border_radius=7)
    pygame.draw.rect(scr, ol, (x0, y0, x1 - x0, y1 - y0), 3)


# ---------------------------------------------------------------- game config
cfg = dict(mode="rehab", lo=40, hi=60, hold=5, rest=3, reps=5, thr=70, ceil=80)
LIMITS = dict(lo=(5, 90, 5), hi=(10, 100, 5), hold=(2, 15, 1), rest=(1, 10, 1), reps=(1, 12, 1), thr=(30, 95, 5), ceil=(50, 100, 5))
LABELS = dict(mode="Mode", lo="Target zone: low", hi="Target zone: high", hold="Hold time",
              rest="Rest time", reps="Repetitions", thr="Effort threshold", ceil="Ceiling warning")
UNITS = dict(lo="%", hi="%", hold=" s", rest=" s", reps="", thr="%", ceil="%")


def rows():
    return ["mode", "lo", "hi", "hold", "rest", "reps", "ceil"] if cfg["mode"] == "rehab" else ["mode", "thr", "ceil"]


def log_rx(hill, kind, lat, thr, eff0):
    """Append one reflex measurement to reflex_log.csv."""
    new = not os.path.exists(RX_LOG)
    try:
        with open(RX_LOG, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["session", "clock", "pipe", "event", "latency_s", "threshold_pct", "effort_at_cue_pct"])
            w.writerow([SESSION_ID, time.strftime("%H:%M:%S"), hill, "squeeze" if kind == "on" else "relax",
                        round(lat, 3), round(thr * 100), round(eff0 * 100)])
    except OSError:
        pass


def rx_stats(r):
    """(average, best, measured, missed) for 'on' (squeeze) and 'off' (relax)."""
    out = {}
    for k in ("on", "off"):
        v = r["rx"][k]
        out[k] = (sum(v) / len(v) if v else None, min(v) if v else None, len(v), r["rx_miss"][k])
    return out


def load_best():
    try:
        return float(open(BEST_FILE).read())
    except Exception:
        return 0.0


HELP_LINES = ("Your muscle moves the bird.", "", "- Squeeze to lift the bird, relax to let it sink.", "- Keep the bird inside the GAP of each green pipe for the whole hold.",
              "  The gap sits at your target zone, built from your own relaxed and max.", "- A rep counts when you stay in the gap at least 80% of the pipe.",
              "- Between pipes, relax fully and breathe.", "- When GO! appears, squeeze fast. When RELAX! appears, let go fast.", "  Your reaction times are shown in the Reaction Report.",
              "- Above the red ceiling line, ease off.", "- Training mode: hold above the threshold as long as you can.",
              "- MUSCLE FATIGUE box: median frequency falls as muscle tires.", "  Fatigue onset is detected & saved to fatigue_log.csv.", "",
              "Stop if you feel sharp pain. F2: new session   Esc: end session")


def main(sig=None, embedded=False):
    port = sys.argv[1] if len(sys.argv) > 1 else None
    if sig is None:
        sig = Signal(port)
    cfg["mode"] = "rehab"                      # always start in Recovery mode when a game is opened
    pygame.init()
    screen = pygame.display.set_mode((SW, SH))
    canvas = pygame.Surface((W, H))
    use(canvas)
    pygame.display.set_caption("ReGain Flap")
    clock = pygame.time.Clock()
    art = Art()

    scene, sel = "setup", 0
    pain1 = pain2 = 0
    base = mx = None
    rec = None
    calmsg = "Press B to record relaxed, then X for max squeeze."
    run = res = summary = None
    by, prev_by, parts, pops = A(0), A(0), [], []
    scroll = 0.0
    modal, menu, pixel, leave = None, None, True, None

    def act():
        if base is None or mx is None:
            return 0.0
        return max(0.0, min(1.0, (sig.sm - base) / max(1.0, mx - base)))

    def calibrated():
        return base is not None and mx is not None and mx - base >= 50 and mx >= base * 1.30

    def burst(x, y, n, cols):
        for _ in range(n):
            a_, sp = random.uniform(0, 6.28), random.uniform(60, 300)
            parts.append([x, y, math.cos(a_) * sp, math.sin(a_) * sp - 80, 0, random.uniform(.5, 1.0), random.choice(cols), random.randint(3, 6)])

    def start_run():
        train = cfg["mode"] == "train"
        segs, t = [], T0
        if train:
            segs.append(dict(s=t, e=t + 600, lo=cfg["thr"] / 100, hi=1.5, hold=True, tin=0, tot=0, ok=False))
        else:
            for _ in range(cfg["reps"]):
                t += cfg["rest"]
                segs.append(dict(s=t, e=t + cfg["hold"], lo=cfg["lo"] / 100, hi=cfg["hi"] / 100, hold=True, tin=0, tot=0, ok=False))
                t += cfg["hold"]
            t += cfg["rest"]
        return dict(train=train, segs=segs, t=0.0, end=1e9 if train else t, reps=0, tin=0.0, tot=0.0, sum=0.0, n=0.0,
                    peak=0.0, over=0.0, trace=[], acc=0.0, out=0.0, entered=False, holdT=0.0, ceil=cfg["ceil"] / 100,
                    cue="Get ready", warn=False, a=0.0, inz=False, lo=cfg["lo"] / 100, hi=cfg["hi"] / 100,
                    rx_pending=False, rx_kind="on", rx_t0=0.0, rx_eff0=0.0, rx_thr=0.0, rx_hill=0,
                    rx=dict(on=[], off=[]), rx_miss=dict(on=0, off=0), rx_skip=dict(on=0, off=0))

    def rx_arm(r, kind, thr, hill):
        """Start timing a reflex. 'on' = squeeze up to thr, 'off' = relax down to thr (RELAX_THR)."""
        if r["rx_pending"]:
            r["rx_miss"][r["rx_kind"]] += 1
        r["rx_pending"] = False
        eff = r["a"]
        if (kind == "on" and eff >= thr) or (kind == "off" and eff <= thr):
            r["rx_skip"][kind] += 1               # already past the threshold when the cue came: nothing to measure
            return
        r.update(rx_pending=True, rx_kind=kind, rx_t0=r["t"], rx_eff0=eff, rx_thr=thr, rx_hill=hill)

    def rx_check(r, a):
        if not r["rx_pending"]:
            return
        kind, lat, thr = r["rx_kind"], r["t"] - r["rx_t0"], r["rx_thr"]
        if (kind == "on" and a >= thr) or (kind == "off" and a <= thr):
            r["rx_pending"] = False
            r["rx"][kind].append(lat)
            log_rx(r["rx_hill"], kind, lat, thr, r["rx_eff0"])
            pops.append([("Squeeze " if kind == "on" else "Relax ") + f"{lat:.2f} s", OX + 40, max(120, by - 60), 0.0])
        elif lat > RX_TIMEOUT:
            r["rx_pending"] = False
            r["rx_miss"][kind] += 1

    def step(r, dt):
        a = act(); r["a"] = a; r["t"] += dt; r["acc"] += dt; r["peak"] = max(r["peak"], a)
        rx_check(r, a)
        if C.fat_onset_new(sig, r): pops.append(["Fatigue onset!", OX + 40, max(120, by - 80), 0.0])
        for k, g in enumerate(r["segs"]):
            if not g.get("rx_on") and r["t"] >= g["s"]:
                g["rx_on"] = True
                rx_arm(r, "on", g["lo"], k + 1)
                C.fat_open(sig, r, k + 1, "flap-train" if r["train"] else "flap")
            if not r["train"] and not g.get("rx_off") and r["t"] >= g["e"]:
                g["rx_off"] = True
                rx_arm(r, "off", RELAX_THR, k + 1)
                C.fat_close(sig, r)
        if r["acc"] >= .05:
            r["trace"].append(a); r["acc"] = 0
        seg = next((g for g in r["segs"] if g["s"] <= r["t"] < g["e"]), None)
        cue, r["inz"] = ("Get ready" if r["t"] < T0 else "Relax and breathe"), False
        if seg:
            inz = seg["lo"] <= a <= seg["hi"]; r["inz"] = inz
            seg["tot"] += dt; seg["tin"] += dt if inz else 0
            r["tot"] += dt; r["tin"] += dt if inz else 0; r["sum"] += a * dt; r["n"] += dt
            if r["train"]:
                if inz:
                    r["entered"] = True; r["out"] = 0; r["holdT"] += dt
                elif r["entered"]:
                    r["out"] += dt
                    if r["out"] > 1.5:
                        return True
            cue = "Squeeze a little more" if a < seg["lo"] else "Ease off a little" if a > seg["hi"] else "Hold steady"
        elif not r["train"] and any(g["s"] - 1.5 < r["t"] < g["s"] for g in r["segs"]):
            cue = "Pipe coming. Get ready"
        if not r["train"]:
            for g in r["segs"]:
                if not g["ok"] and r["t"] >= g["e"]:
                    g["ok"] = True
                    if g["tot"] > 0 and g["tin"] / g["tot"] >= .8:
                        r["reps"] += 1
                        burst(OX, by, 40, [AMBER, TEAL, (255, 255, 255), GREEN])
                        pops.append(["+1 rep", OX + 40, max(120, by - 40), 0.0])
                    else:
                        pops.append(["Missed. Try again", OX + 40, max(120, by - 40), 0.0])
        r["warn"] = a > r["ceil"]
        if r["warn"]:
            r["over"] += dt; cue = "Above ceiling. Ease off now"
        if r["rx_pending"]:
            cue = "GO! Squeeze up to target" if r["rx_kind"] == "on" else "RELAX now! Let go completely"
        r["cue"] = cue
        return (not r["train"]) and r["t"] >= r["end"]

    def draw_play(r, dt, tick):
        nonlocal by, prev_by
        t = r["t"]
        backdrop(art, t)
        for g in r["segs"]:
            x, w = OX + (g["s"] - t) * PPS, (g["e"] - g["s"]) * PPS
            if x > W + 20 or x + w < -20:
                continue
            x0, x1 = max(x, -30), min(x + w, W + 30)
            active = g["s"] <= t < g["e"]
            bad = 1.0 if (active and not r["inz"]) else 0.0
            ytop, ybot = A(min(g["hi"], 1.0)), A(g["lo"])
            gap = pygame.Surface((int(x1 - x0), int(ybot - ytop)), pygame.SRCALPHA)
            gap.fill((255, 255, 255, 110 if (active and r["inz"]) else 45))
            scr.blit(gap, (x0, ytop))
            if g["hi"] <= 1.0:
                pipe_part(x0, x1, -40, ytop, "bottom", bad)
            pipe_part(x0, x1, ybot, H - GROUND + 6, "top", bad)
        draw_ground(art, t)
        # ceiling line
        yc = A(r["ceil"])
        for x in range(0, W, 22):
            pygame.draw.line(scr, (232, 70, 60), (x, yc), (x + 11, yc), 3)
        text("ceiling", W - 14, yc - 22, 14, (200, 40, 40), True, "r")
        # bird clamped within sky bounds
        target_by = max(PT - 10, min(PB + 15, A(r["a"])))
        prev_by, by = by, by + (target_by - by) * min(1, dt * 16)
        by = max(PT - 20, min(PB + 20, by))
        tilt = max(-35, min(35, (prev_by - by) * 18))
        if r["warn"]:
            glow = pygame.Surface((120, 120), pygame.SRCALPHA)
            pygame.draw.circle(glow, (232, 60, 50, 110), (60, 60), 56)
            scr.blit(glow, (OX - 60, by - 60))
        if int(tick * 30) % 2 == 0:
            parts.append([OX - 18, by + random.uniform(-6, 6), -PPS * .5, random.uniform(-20, 20), 0, .5, (255, 255, 255), 4])
        for p in parts[:]:
            p[4] += dt; p[0] += p[2] * dt; p[1] += p[3] * dt; p[3] += 300 * dt * (p[7] > 4)
            if p[4] > p[5]: parts.remove(p); continue
            s_ = pygame.Surface((14, 14), pygame.SRCALPHA)
            pygame.draw.circle(s_, (*p[6], int(255 * (1 - p[4] / p[5]))), (7, 7), p[7])
            scr.blit(s_, (p[0] - 7, p[1] - 7))
        fr = art.birds[int(tick * (14 if tilt > 4 else 8)) % 4]
        img = pygame.transform.rotozoom(fr, tilt, 1.0)
        scr.blit(img, img.get_rect(center=(OX, by)))
        for pp in pops[:]:
            pp[3] += dt; pp[2] -= 40 * dt
            if pp[3] > 1.3: pops.remove(pp); continue
            px = max(240, min(W - 250, pp[1]))
            py = max(90, pp[2])
            text(pp[0], px, py, 24, (255, 255, 255), True, "l", int(255 * (1 - pp[3] / 1.3)))

    def hud(r):
        zl, zh = (cfg["thr"] / 100, 1.0) if r["train"] else (r["lo"], r["hi"])
        # Widen Effort box to 116px so tags stay strictly inside border
        c = window((CX + 12, CY + 12, 116, 340), "Effort")
        top, bot = c.y + 8, c.bottom - 30
        bevel((c.x + 6, top, 46, bot - top), False, FACE)
        nb = (bot - top - 8) // 10
        for k in range(nb):
            lvl = (k + 1) / nb; y = bot - 6 - (k + 1) * 10
            if lvl <= r["a"] + .5 / nb:
                col = (192, 0, 0) if lvl > r["ceil"] else (230, 150, 0) if lvl > zh else (0, 160, 0) if lvl >= zl else (0, 0, 160)
                pygame.draw.rect(scr, col, (c.x + 9, y, 40, 8))
        def gy(v): return bot - 6 - min(v, 1.0) * (nb * 10)
        pygame.draw.lines(scr, (0, 128, 0), False, [(c.x + 55, gy(zh)), (c.x + 59, gy(zh)), (c.x + 59, gy(zl)), (c.x + 55, gy(zl))], 2)
        text("zone", c.x + 63, (gy(zh) + gy(zl)) / 2 - 6, 11, (0, 100, 0), True)
        pygame.draw.line(scr, (192, 0, 0), (c.x + 6, gy(r["ceil"])), (c.x + 59, gy(r["ceil"])), 2)
        text("max", c.x + 63, gy(r["ceil"]) - 6, 11, (192, 0, 0), True)
        text(f"{r['a']*100:.0f}%", c.centerx, c.bottom - 22, 15, BLK, True, "c")

        C.fatigue_hud(sig, r, CX + W - 248, CY + 12)
        if r["rx_pending"]:
            go = r["rx_kind"] == "on"
            c = window((CX + W // 2 - 160, CY + 14, 320, 100), "GO!" if go else "RELAX")
            text("SQUEEZE NOW!" if go else "RELAX NOW!", c.centerx, c.centery - 2, 32, (0, 128, 0) if go else (192, 0, 0), True, "c")

    def stats_for(r):
        avg = r["sum"] / r["n"] if r["n"] else 0
        pct = r["tin"] / r["tot"] if r["tot"] else 0
        if r["train"]:
            best = max(load_best(), r["holdT"])
            try: open(BEST_FILE, "w").write(str(best))
            except Exception: pass
            st = [("Hold time", f"{r['holdT']:.1f} s"), ("Best on this device", f"{best:.1f} s")]
            title = "Hold training summary"
        else:
            st = [("Time in target zone", f"{pct*100:.0f}%"), ("Successful reps", f"{r['reps']} / {cfg['reps']}")]
            title = "Session summary"
        st += [("Average activation", f"{avg*100:.0f}%"), ("Peak activation", f"{r['peak']*100:.0f}%"),
               ("Time above ceiling", f"{r['over']:.1f} s"), ("Pain before to after", f"{pain1} to {pain2}")]
        return title, st, pct

    def do(a):
        nonlocal scene, run, res, modal, pixel, leave
        if a == "exit": leave = "menu"
        elif a == "pixel": pixel = not pixel
        elif a in ("help", "about"): modal = a
        elif a == "end" and scene == "play" and run: C.fat_close(sig, run); res, run, scene = run, None, "pain2"
        elif a in ("new", "setup"): run = None; scene = "setup"
        elif a == "cal" and scene != "play": scene = "cal"

    # ------------------------------------------------------------ main loop
    while True:
        if leave: return leave
        dt = min(.05, clock.tick(60) / 1000)
        tick = time.time()
        keys = pygame.key.get_pressed()
        sig.update(dt, keys)
        MOUSE["pos"] = pygame.mouse.get_pos(); MOUSE["clicks"] = []; MOUSE["press"] = None
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                return "quit"
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                MOUSE["down"] = True; MOUSE["press"] = e.pos
            if e.type == pygame.MOUSEBUTTONUP and e.button == 1:
                MOUSE["down"] = False; MOUSE["grab"] = None; MOUSE["clicks"].append(e.pos)
            if e.type != pygame.KEYDOWN:
                continue
            k = e.key
            if modal:
                if k in (pygame.K_RETURN, pygame.K_ESCAPE, pygame.K_SPACE): modal = None
                continue
            fk = {pygame.K_F1: "help", pygame.K_F2: "new", pygame.K_F3: "setup", pygame.K_F4: "cal"}.get(k)
            if fk:
                do(fk); continue
            if k == pygame.K_ESCAPE and scene == "play":
                C.fat_close(sig, run); res, run, scene = run, None, "pain2"; continue
            if sig.demo and pygame.K_0 <= k <= pygame.K_9:
                sig.lvl = (k - pygame.K_0) / 10
            d = (k == pygame.K_RIGHT) - (k == pygame.K_LEFT)
            if scene == "setup":
                R = rows(); sel = min(sel, len(R) - 1)
                if k == pygame.K_DOWN: sel = (sel + 1) % len(R)
                if k == pygame.K_UP: sel = (sel - 1) % len(R)
                if d:
                    n = R[sel]
                    if n == "mode":
                        cfg["mode"] = "train" if cfg["mode"] == "rehab" else "rehab"; sel = 0
                    else:
                        a_, b_, st_ = LIMITS[n]; cfg[n] = max(a_, min(b_, cfg[n] + d * st_))
                        if cfg["hi"] < cfg["lo"] + 5:
                            if n == "lo": cfg["hi"] = cfg["lo"] + 5
                            else: cfg["lo"] = cfg["hi"] - 5
                if k == pygame.K_RETURN: scene = "pain1"
            elif scene == "pain1":
                pain1 = max(0, min(10, pain1 + d))
                if k == pygame.K_RETURN: scene = "cal"
            elif scene == "cal":
                if k in (pygame.K_b, pygame.K_x) and rec is None:
                    rec = ["base" if k == pygame.K_b else "max", time.time(), []]
                if k == pygame.K_RETURN and calibrated():
                    run = start_run(); parts.clear(); pops.clear(); by = prev_by = A(0); scene = "play"
            elif scene == "pain2":
                pain2 = max(0, min(10, pain2 + d))
                if k == pygame.K_RETURN:
                    summary = stats_for(res); scene = "sum"
            elif scene == "sum":
                if k == pygame.K_RETURN: pain1 = pain2 = 0; scene = "setup"
                if k == pygame.K_r: scene = "rx"
                if k == pygame.K_q: leave = "menu"
            elif scene == "rx":
                if k in (pygame.K_RETURN, pygame.K_ESCAPE, pygame.K_BACKSPACE): scene = "sum"

        if rec:
            rec[2].append(sig.sm)
            if time.time() - rec[1] >= C.CAL_S:
                xs = sorted(rec[2])
                if rec[0] == "base": base = sum(xs) / len(xs)
                else: mx = xs[int(len(xs) * .9)]
                sig.fatigue_cal(rec[0])
                rec = None
                if base is not None and mx is not None:
                    calmsg = "Calibrated. 0% is relaxed, 100% is your max. Press Enter." if calibrated() else \
                        "Squeeze too close to relaxed. Record max (X) harder."
                else:
                    calmsg = "Good. Now record the other value."

        if scene == "play" and run and not (modal or menu is not None):
            if step(run, dt):
                C.fat_close(sig, run); res, run, scene = run, None, "pain2"

        scroll += dt
        paused = bool(modal) or menu is not None
        MOUSE["block"] = False
        for c_ in list(MOUSE["clicks"]):                       # menu bar clicks first
            hit = next((mi for mi, rr in enumerate(menu_bar_rects()) if rr.collidepoint(c_)), None)
            if hit is not None:
                menu = None if menu == hit else hit; MOUSE["clicks"] = []; break
            if menu is not None:
                it = menu_item_at(menu, c_); menu = None
                if it: do(it)
                MOUSE["clicks"] = []; break
        if menu is not None:
            for mi, rr in enumerate(menu_bar_rects()):
                if rr.collidepoint(MOUSE["pos"]): menu = mi
        MOUSE["block"] = bool(modal) or menu is not None
        use(canvas)
        if scene == "play" and run:
            draw_play(run, 0 if paused else dt, tick)
        else:
            backdrop(art, scroll); draw_ground(art, scroll)
        use(screen)
        names = dict(setup="Session Setup", pain1="Pain Check", cal="Calibration", play="Flight Session", pain2="Pain Check", sum="Results")
        chrome(f"ReGain Flap - [{names.get(scene, '')}]", menu)
        if pixel:
            small = pygame.transform.scale(canvas, (W // 3, H // 3)); screen.blit(pygame.transform.scale(small, (W, H)), (CX, CY))
        else:
            screen.blit(canvas, (CX, CY))
        dc = lambda w_, h_: pygame.Rect(CX + (W - w_) // 2, CY + (H - h_) // 2, w_, h_)
        hint = ""
        if scene == "play" and run:
            if run["warn"]:
                w_ = art.warn.copy(); w_.set_alpha(int(150 + 90 * math.sin(time.time() * 10))); screen.blit(w_, (CX, CY))
            hud(run)
            zp = run["tin"] / run["tot"] * 100 if run["tot"] else 0
            status([(run["cue"], 400), (f"Hold {run['holdT']:.1f} s" if run["train"] else f"Reps {run['reps']}/{cfg['reps']}", 110), (f"In zone {zp:.0f}%", 110),
                    (f"Time {max(0, run['t'] - T0):.0f} s", 90), ("Esc: end session", None)])
        elif scene == "setup":
            c = window(dc(700, 500), "Session Setup")
            text("The therapist sets the plan, the player flies. Drag a slider or use the arrow keys.", c.x + 16, c.y + 12, 13, BLK)
            R = rows(); sel = min(sel, len(R) - 1)
            for i, n in enumerate(R):
                y = c.y + 44 + i * 40; lab = pygame.Rect(c.x + 10, y - 3, 240, 28)
                if clicked(lab): sel = i
                if i == sel: focus(lab)
                text(LABELS[n], c.x + 18, y + 3, 14, BLK)
                if n == "mode":
                    for j, (key, nm) in enumerate((("rehab", "Recovery"), ("train", "Training"))):
                        b_ = pygame.Rect(c.x + 262 + j * 130, y, 124, 24); on = cfg["mode"] == key
                        bevel(b_, on, C.YEL if on else FACE); text(nm, b_.centerx, b_.centery - 1, 14, BLK, on, "c")
                        if clicked(b_) and not on: cfg["mode"] = key; sel = 0
                    continue
                a_, b_, s_ = LIMITS[n]
                nv = trackbar((c.x + 262, y, 260, 24), cfg[n], a_, b_, s_, n)
                if nv != cfg[n]:
                    cfg[n] = nv; sel = i
                    if cfg["hi"] < cfg["lo"] + 5:
                        if n == "lo": cfg["hi"] = cfg["lo"] + 5
                        else: cfg["lo"] = cfg["hi"] - 5
                bevel((c.x + 542, y, 100, 24), False, WHITE); text(f"{cfg[n]}{UNITS[n]}", c.x + 634, y + 4, 14, BLK, False, "r")
            text(f"Signal: {'DEMO (Space = squeeze, 0-9 = steady level)' if sig.demo else 'Arduino'}.", c.x + 16, c.y + 44 + 7 * 40 + 4, 13, (64, 64, 64))
            if button((c.right - 210, c.bottom - 40, 96, 28), "Next >", True, True): scene = "pain1"
            if button((c.right - 106, c.bottom - 40, 96, 28), "Exit"): do("exit")
            hint = "Up/Down: select    Left/Right: change    Enter: next"
        elif scene in ("pain1", "pain2"):
            c = window(dc(560, 350), "Pain Check")
            v = pain1 if scene == "pain1" else pain2
            text("How much does it hurt right now?" if scene == "pain1" else "And how does it feel after the session?", c.x + 20, c.y + 16, 15, BLK, True)
            text("0 = no pain, 10 = worst imaginable. Rate the injured area.", c.x + 20, c.y + 40, 13, BLK)
            bevel((c.centerx - 60, c.y + 70, 120, 84), False, WHITE); text(v, c.centerx, c.y + 112, 64, lerp((0, 128, 0), (200, 0, 0), v / 10), True, "c")
            nv = trackbar((c.x + 60, c.y + 172, c.w - 120, 24), v, 0, 10, 1, "pain")
            if scene == "pain1": pain1 = nv
            else: pain2 = nv
            text("none", c.x + 60, c.y + 202, 12, BLK); text("worst", c.right - 60, c.y + 202, 12, BLK, False, "r")
            if scene == "pain1" and v >= 6:
                pygame.draw.polygon(scr, (255, 220, 0), [(c.x + 24, c.y + 246), (c.x + 40, c.y + 216), (c.x + 56, c.y + 246)]); pygame.draw.polygon(scr, BLK, [(c.x + 24, c.y + 246), (c.x + 40, c.y + 216), (c.x + 56, c.y + 246)], 2)
                text("!", c.x + 40, c.y + 233, 16, BLK, True, "c")
                text("Pain of 6 or more: check with your physiotherapist before", c.x + 68, c.y + 220, 13, BLK); text("loading this muscle.", c.x + 68, c.y + 237, 13, BLK)
            if button((c.right - 210, c.bottom - 40, 96, 28), "OK", True, True):
                if scene == "pain1": scene = "cal"
                else: summary = stats_for(res); scene = "sum"
            if scene == "pain1" and button((c.right - 106, c.bottom - 40, 96, 28), "Back"): scene = "setup"
            hint = "Left/Right: change    Enter: OK"
        elif scene == "cal":
            c = window(dc(680, 440), "Calibration")
            text("Every pipe is placed using your own relaxed and max signal.", c.x + 16, c.y + 12, 13, BLK)
            for idx, (lab, val, key, bl) in enumerate((("1. Relaxed", base, "base", "Record relaxed (B)"), ("2. Max squeeze", mx, "max", "Record max (X)"))):
                gx = c.x + 16 + idx * 318; groupbox((gx, c.y + 44, 304, 130), lab)
                text("Squeeze as hard as is comfortable" if idx else "Relax the muscle fully", gx + 14, c.y + 66, 13, BLK)
                bevel((gx + 14, c.y + 88, 110, 32), False, WHITE); text("-" if val is None else int(val), gx + 118, c.y + 92, 22, BLK, True, "r")
                if button((gx + 14, c.y + 130, 170, 28), bl, rec is None) and rec is None: rec = [key, time.time(), []]
            text("Live signal", c.x + 16, c.y + 190, 13, BLK); progress((c.x + 16, c.y + 208, c.w - 32, 22), min(1, sig.sm / 1000))
            if rec: text(f"Recording...  {max(0.0, C.CAL_S - (time.time() - rec[1])):.1f} s", c.x + 16, c.y + 242, 15, (0, 0, 160), True)
            else: text(calmsg, c.x + 16, c.y + 242, 14, BLK if calibrated() else (96, 96, 96))
            C.para(sig.fat_msg(), c.x + 16, c.y + 270, c.w - 32, 12, (0, 100, 0) if sig.fm_ready else (96, 96, 96))
            if button((c.right - 226, c.bottom - 40, 130, 28), "Start session >", calibrated(), True):
                run = start_run(); parts.clear(); pops.clear(); by = prev_by = A(0); scene = "play"
            if button((c.right - 90, c.bottom - 40, 80, 28), "Back"): scene = "setup"
            hint = "B: record relaxed    X: record max    Enter: start"
        elif scene == "sum" and summary:
            title, st, pct = summary
            c = window(dc(800, 600), "Session Results")
            rows_ = list(st); rxs = rx_stats(res)
            for k_, lab in (("on", "Squeeze reaction"), ("off", "Relax reaction")):
                avg, best, n_, miss = rxs[k_]
                rows_.append((lab + " (avg / best)", f"{avg:.2f} s / {best:.2f} s" if avg is not None else "not measured"))
                rows_.append(("   measured / missed", f"{n_} / {miss}"))
            rows_ += C.fat_rows(res)

            lb_h = min(220, 24 + 17 * len(rows_) + 4)
            lb = pygame.Rect(c.x + 14, c.y + 8, c.w - 28, lb_h); bevel(lb, False, WHITE)
            bevel((lb.x + 2, lb.y + 2, lb.w - 4, 18), True); text("Measure", lb.x + 10, lb.y + 4, 13, BLK, True); text("Result", lb.x + 420, lb.y + 4, 13, BLK, True)
            row_step = min(17, (lb_h - 26) // max(1, len(rows_)))
            for i, (k_, v_) in enumerate(rows_):
                yy = lb.y + 22 + i * row_step
                if i % 2: pygame.draw.rect(scr, (236, 236, 250), (lb.x + 3, yy, lb.w - 6, row_step))
                text(k_, lb.x + 10, yy + 2, 12, BLK); text(v_, lb.x + 420, yy + 2, 12, BLK, True)

            y2 = lb.bottom + 8
            if not res["train"]:
                n_st = 3 if pct >= .8 else 2 if pct >= .6 else 1 if pct >= .4 else 0
                text("Rating:", c.x + 16, y2 + 2, 14, BLK, True)
                for i in range(3): star(c.x + 100 + i * 40, y2 + 10, 16, (255, 200, 0) if i < n_st else (150, 150, 150))
            yb = C.para("Reaction time = cue to threshold crossing (includes ~0.2 s smoothing). Saved to reflex_log.csv.", c.x + 16, y2 + 26, c.w - 32, 12)
            if pain2 - pain1 >= 2: yb = C.para("Pain rose by 2 or more. Consult physiotherapist before next session.", c.x + 16, yb + 2, c.w - 32, 13, (192, 0, 0), True)
            elif res["over"] > 2: yb = C.para("Effort exceeded ceiling for a while. Aim for smoother, lighter holds.", c.x + 16, yb + 2, c.w - 32, 13, (192, 0, 0), True)

            cb_top = yb + 12
            cb_h = max(50, c.bottom - 46 - cb_top)
            cb = pygame.Rect(c.x + 14, cb_top, c.w - 28, cb_h); bevel(cb, False, WHITE)
            Y2 = lambda v: cb.bottom - 4 - min(v, 1.0) * (cb.h - 8)
            if not res["train"]:
                pygame.draw.rect(scr, (200, 240, 200), (cb.x + 3, Y2(res["hi"]), cb.w - 6, Y2(res["lo"]) - Y2(res["hi"])))
            pygame.draw.line(scr, (192, 0, 0), (cb.x + 3, Y2(res["ceil"])), (cb.right - 4, Y2(res["ceil"])))
            T = res["trace"]
            if len(T) > 1:
                pygame.draw.lines(scr, (0, 0, 160), False, [(cb.x + 4 + i / (len(T) - 1) * (cb.w - 8), Y2(v)) for i, v in enumerate(T)], 2)
            text("Effort over the session (green band = target zone, red = ceiling)", cb.x + 6, cb.y + 2, 11, (64, 64, 64))

            if button((c.right - 348, c.bottom - 40, 112, 28), "Reaction >"): scene = "rx"
            if button((c.right - 226, c.bottom - 40, 130, 28), "New session", True, True): do("new"); pain1 = pain2 = 0
            if button((c.right - 90, c.bottom - 40, 80, 28), "Exit"): do("exit")
            hint = "Enter: new session    R: reaction report    Q: back to arcade"
        elif scene == "rx" and res:
            c = window(dc(780, 540), "Reaction Report")
            C.draw_rx_report(c, res, SESSION_ID, sig.demo)
            if button((c.right - 126, c.bottom - 40, 112, 28), "< Results", True, True): scene = "sum"
            hint = "Enter / Esc: back to results"
        if scene != "play":
            status([(hint or "Ready", 560), ("Pixel mode ON" if pixel else "Pixel mode OFF", 130), (f"Signal: {'demo' if sig.demo else 'Arduino'}", None)])
        if menu is not None: draw_menu(menu, pixel)
        if modal:
            MOUSE["block"] = False
            if modal == "about":
                c = window(dc(450, 230), "About ReGain Flap")
                pygame.draw.rect(scr, (255, 90, 120), (c.x + 24, c.y + 36, 56, 26)); pygame.draw.rect(scr, (255, 220, 90), (c.x + 34, c.y + 22, 34, 14))
                pygame.draw.circle(scr, BLK, (c.x + 36, c.y + 66), 10); pygame.draw.circle(scr, BLK, (c.x + 70, c.y + 66), 10)
                text("ReGain Flap", c.x + 110, c.y + 24, 20, BLK, True)
                text("EMG biofeedback for rehab and isometric training.", c.x + 110, c.y + 54, 13, BLK)
                text("Not a medical device. Use it under your", c.x + 110, c.y + 78, 13, BLK); text("physiotherapist's plan.", c.x + 110, c.y + 94, 13, BLK)
            else:
                c = window(dc(660, 430), "How to Play")
                cur_y = c.y + 10
                for ln in HELP_LINES:
                    if not ln: cur_y += 6; continue
                    cur_y = C.para(ln, c.x + 16, cur_y, c.w - 32, 13, BLK)
            if button((c.centerx - 45, c.bottom - 40, 90, 28), "OK", True, True): modal = None
        pygame.display.flip()


if __name__ == "__main__":
    main()
    pygame.quit()
