"""ReGain Climb - a Pokemon / Nintendo-DS-style hill-climb racing game driven by ONE muscle (EMG biofeedback for rehab).

Run:   python regain_climb.py demo            (no hardware, Space = squeeze, 0-9 = steady level)
       python regain_climb.py auto            (Arduino, finds the port itself)
       python regain_climb.py /dev/ttyACM1    (Arduino on a specific port; Windows: COM3)
Needs: pip install pygame pyserial numpy scipy, plus emg_core.py and fatigue_monitor.py in the same folder.
Arduino runs emg_stream.ino (raw samples at 500 Hz). Close the Arduino Serial Monitor first.
Works with either hand or any single muscle. Run it again for the other side.

Built around common rehab principles (always under a physiotherapist's plan):
  * Controlled effort, not maximum effort: power is highest in the therapist's SWEET SPOT, and drops if you squeeze too little OR too much.
  * Prescribed hold time: each hill takes the set climb time at best, fuel for ~1.35x that.
  * Prescribed rest: the rest stop only refuels while the muscle is relaxed (<=15%), so rest is actually taken.
  * Gradual loading: the sweet spot rises by a set step on every hill (0 to turn off), and hills get visibly steeper.
  * Safety: a ceiling warning and engine overheating if effort goes too high, plus pain checks before and after.
Reflex monitoring: every hill starts with a GO cue (time until the effort reaches the sweet-spot threshold)
and ends with a RELAX cue (time until the effort drops to 15% or less). Both times are shown after each cue,
summarised at the end, and appended to reflex_log.csv so progress can be compared between sessions.
Fatigue tracking: while you hold, the raw signal is analysed with fatigue_monitor.py. As the muscle tires its
median frequency sinks; the point where it has dropped clearly below its starting value is the FATIGUE ONSET.
Shown live in the Muscle Fatigue box, summarised at the end, and appended to fatigue_log.csv.
Not a medical device.
"""
import sys, time, threading, random, math, csv, os
from collections import deque
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pygame
try:
    import serial
except ImportError:
    serial = None

try:
    import numpy as np
    from scipy.signal import butter, sosfilt, sosfilt_zi
    from fatigue_monitor import FatigueMonitor
except ImportError:
    np = FatigueMonitor = None
FAT_OK = FatigueMonitor is not None
FATIGUE_READY = True

W, H = 1000, 640
NAVY, INK, MUT = (10, 22, 40), (236, 244, 250), (150, 172, 194)
TEAL, AMBER, RED, GREEN = (47, 208, 184), (255, 184, 48), (232, 84, 74), (112, 198, 56)

scr = None
FONTS = {}


def font(n, b=False):
    if (n, b) not in FONTS:
        FONTS[(n, b)] = pygame.font.SysFont("monaco,menlo,couriernew,courier,dejavusansmono", max(8, int(n * 1.1)), bold=True)
    return FONTS[(n, b)]


def text(s, x, y, size=20, col=INK, bold=False, anchor="l", alpha=255):
    img = font(size, bold).render(str(s), False, col)
    if alpha < 255:
        img.set_alpha(alpha)
    r = img.get_rect()
    if anchor == "c": r.center = (x, y)
    elif anchor == "r": r.topright = (x, y)
    else: r.topleft = (x, y)
    scr.blit(img, r)
    return r


def wrap(s, x, y, width, size=19, col=MUT):
    line = ""
    for w in s.split():
        if font(size).size(line + w)[0] > width:
            text(line, x, y, size, col); y += size + 8; line = ""
        line += w + " "
    text(line, x, y, size, col)


def para(s, x, y, width, size=12, col=(64, 64, 64), bold=False, lh=None, anchor="l"):
    """Word-wrap text inside `width` pixels. Returns the y just below the last line."""
    lh = lh or size + 4
    f = font(size, bold)
    lines, cur = [], ""
    for w in str(s).split():
        t = (cur + " " + w).strip()
        if cur and f.size(t)[0] > width:
            lines.append(cur); cur = w
        else:
            cur = t
    if cur: lines.append(cur)
    for i, ln in enumerate(lines):
        text(ln, x, y + i * lh, size, col, bold, anchor)
    return y + len(lines) * lh


def panel(rect, col=NAVY, alpha=205, radius=20, border=None):
    r = pygame.Rect(rect)
    s = pygame.Surface(r.size, pygame.SRCALPHA)
    pygame.draw.rect(s, (*col, alpha), s.get_rect(), border_radius=radius)
    if border:
        pygame.draw.rect(s, (*border, 255), s.get_rect(), 2, border_radius=radius)
    scr.blit(s, r.topleft)


def lerp(a, b, t):
    t = max(0, min(1, t))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def star(cx, cy, r, col):
    pts = [(cx + math.cos(-math.pi / 2 + i * math.pi / 5) * (r if i % 2 == 0 else r * .45),
            cy + math.sin(-math.pi / 2 + i * math.pi / 5) * (r if i % 2 == 0 else r * .45)) for i in range(10)]
    pygame.draw.polygon(scr, col, pts)


SW, SH, CX, CY = W + 12, 720, 6, 48          # window size and where the game screen sits inside it

# ---------------------------------------------------------------- Pokemon / Nintendo-DS style toolkit
FACE, HL, LT, SHD, BLK, WHITE = (248, 248, 240), (255, 255, 255), (232, 232, 224), (136, 144, 164), (32, 32, 44), (255, 255, 255)
INKB, BLUEL, SLATE = (48, 52, 76), (112, 144, 224), (34, 38, 58)
RED1, RED2, YEL = (224, 72, 72), (176, 40, 56), (248, 208, 64)
MOUSE = dict(pos=(0, 0), down=False, clicks=[], press=None, grab=None, block=False)
MENUS = [("Game", [("New Session", "F2", "new"), ("Calibrate...", "F4", "cal"), ("End Session", "Esc", "end"), None, ("Exit", "Alt+F4", "exit")]),
         ("Options", [("Setup...", "F3", "setup"), ("Pixel Mode", "", "pixel")]),
         ("Help", [("How to Play...", "F1", "help"), None, ("About...", "", "about")])]


def rbox(r, fill=FACE, outer=INKB, inner=BLUEL, radius=10):
    r = pygame.Rect(r)
    pygame.draw.rect(scr, outer, r, border_radius=radius)
    pygame.draw.rect(scr, inner, r.inflate(-2, -2), border_radius=max(2, radius - 1))
    pygame.draw.rect(scr, fill, r.inflate(-6, -6), border_radius=max(2, radius - 3))


def bevel(r, raised=True, fill=FACE):
    r = pygame.Rect(r)
    if raised:
        if fill is not None: pygame.draw.rect(scr, SHD, r.move(0, 2), border_radius=8)
        rbox(r, fill if fill is not None else FACE, radius=8)
    else:
        pygame.draw.rect(scr, fill if fill is not None else FACE, r, border_radius=5)
        pygame.draw.rect(scr, INKB, r, 2, border_radius=5)


def hot(r): return pygame.Rect(r).collidepoint(MOUSE["pos"]) and not MOUSE["block"]
def clicked(r): return (not MOUSE["block"]) and any(pygame.Rect(r).collidepoint(c) for c in MOUSE["clicks"])


def focus(r):
    """Red selection triangle to the left of a row, button or icon label."""
    r = pygame.Rect(r); cy = r.centery; x = r.x - 14 + int(2 * math.sin(time.time() * 8))
    pts = [(x, cy - 7), (x, cy + 7), (x + 10, cy)]
    pygame.draw.polygon(scr, INKB, [(a - 1 if i < 2 else a + 1, b) for i, (a, b) in enumerate(pts)]); pygame.draw.polygon(scr, RED1, pts)


def button(r, label, enabled=True, default=False):
    r = pygame.Rect(r); pressed = enabled and hot(r) and MOUSE["down"]
    body = r.move(0, 2 if pressed else 0)
    pygame.draw.rect(scr, SHD, r.move(0, 3), border_radius=9)
    rbox(body, FACE if enabled else (214, 214, 206), INKB, BLUEL if enabled else SHD, 9)
    text(label, body.centerx, body.centery - 1, 14, BLK if enabled else SHD, False, "c")
    if enabled and (default or hot(r)): focus(r.inflate(-8, -8))
    return enabled and clicked(r)


def titlebar(tb, title):
    pygame.draw.rect(scr, RED2, tb, border_radius=7)
    pygame.draw.rect(scr, RED1, (tb.x + 2, tb.y + 2, tb.w - 4, tb.h // 2), border_radius=5)
    text(title, tb.x + 10, tb.y + 3, 13, WHITE, True)
    for i in range(3): pygame.draw.circle(scr, (255, 214, 214), (tb.right - 12 - i * 12, tb.centery), 3)


def window(r, title):
    r = pygame.Rect(r)
    pygame.draw.rect(scr, (20, 22, 34), r.move(0, 5), border_radius=12)
    rbox(r, FACE, INKB, BLUEL, 12)
    titlebar(pygame.Rect(r.x + 5, r.y + 5, r.w - 10, 19), title)
    return pygame.Rect(r.x + 4, r.y + 24, r.w - 8, r.h - 28)


def groupbox(r, label):
    r = pygame.Rect(r)
    pygame.draw.rect(scr, BLUEL, (r.x, r.y + 8, r.w, r.h - 8), 2, border_radius=8)
    wl = font(13).size(label)[0]
    pygame.draw.rect(scr, FACE, (r.x + 8, r.y, wl + 8, 18)); text(label, r.x + 12, r.y + 1, 13, BLK, True)


def progress(r, frac, col=(0, 0, 128)):
    """HP-bar style meter."""
    r = pygame.Rect(r); frac = max(0.0, min(1.0, frac))
    pygame.draw.rect(scr, INKB, r, border_radius=r.h // 2)
    inner = r.inflate(-4, -4); pygame.draw.rect(scr, (92, 100, 120), inner, border_radius=inner.h // 2)
    if col == (0, 0, 128): c = (88, 200, 96) if frac > .5 else (248, 204, 48) if frac > .2 else (228, 72, 64)
    elif col[0] > 150: c = (228, 72, 64)
    elif col[1] > 100: c = (88, 200, 96)
    else: c = (80, 136, 232)
    w = int(inner.w * frac)
    if w > 3:
        pygame.draw.rect(scr, c, (inner.x, inner.y, w, inner.h), border_radius=inner.h // 2)
        pygame.draw.rect(scr, lerp(c, WHITE, .45), (inner.x + 3, inner.y + 2, max(1, w - 6), max(2, inner.h // 4)), border_radius=2)


def trackbar(r, val, lo, hi, step, ident):
    r = pygame.Rect(r); tw = 11
    if MOUSE["press"] and r.inflate(0, 8).collidepoint(MOUSE["press"]) and not MOUSE["block"]: MOUSE["grab"] = ident
    if MOUSE["grab"] == ident and MOUSE["down"]:
        f = max(0.0, min(1.0, (MOUSE["pos"][0] - r.x - tw / 2) / (r.w - tw)))
        val = int(round((lo + f * (hi - lo)) / step) * step); val = max(lo, min(hi, val))
    cy = r.centery - 1; tx = r.x + tw // 2 + int((val - lo) / (hi - lo) * (r.w - tw))
    pygame.draw.rect(scr, INKB, (r.x, cy - 5, r.w, 10), border_radius=5)
    pygame.draw.rect(scr, (92, 100, 120), (r.x + 2, cy - 3, r.w - 4, 6), border_radius=3)
    pygame.draw.rect(scr, (80, 136, 232), (r.x + 2, cy - 3, max(0, tx - r.x - 2), 6), border_radius=3)
    for k in range(int((hi - lo) / step) + 1):
        pygame.draw.circle(scr, SHD, (r.x + tw // 2 + int(k * step / (hi - lo) * (r.w - tw)), r.bottom - 3), 1)
    pygame.draw.circle(scr, (0, 0, 0), (tx, cy), 10)
    pygame.draw.circle(scr, (90, 90, 100), (tx - 3, cy - 3), 2)
    return val


def menu_bar_rects():
    out, x = [], 10
    for name, _ in MENUS:
        w = font(14).size(name)[0] + 22; out.append(pygame.Rect(x, 25, w, 19)); x += w + 6
    return out


def menu_rect(mi):
    bar = menu_bar_rects()[mi]; items = MENUS[mi][1]
    return pygame.Rect(bar.x, 46, 220, sum(8 if it is None else 22 for it in items) + 10)


def menu_item_at(mi, pos):
    mr = menu_rect(mi); y = mr.y + 5
    for it in MENUS[mi][1]:
        h = 8 if it is None else 22
        if it and pygame.Rect(mr.x + 4, y, mr.w - 8, h).collidepoint(pos): return it[2]
        y += h
    return None


def draw_menu(mi, pixel):
    mr = menu_rect(mi); pygame.draw.rect(scr, (20, 22, 34), mr.move(0, 4), border_radius=10); rbox(mr, FACE, INKB, BLUEL, 10); y = mr.y + 5
    for it in MENUS[mi][1]:
        if it is None:
            for x in range(mr.x + 12, mr.right - 12, 6): pygame.draw.line(scr, SHD, (x, y + 4), (x + 2, y + 4))
            y += 8; continue
        row = pygame.Rect(mr.x + 4, y, mr.w - 8, 22); on = row.collidepoint(MOUSE["pos"])
        if on: focus(pygame.Rect(row.x + 14, row.y, 4, 22))
        text(it[0], row.x + 26, row.y + 4, 14, BLK, on); text(it[1], row.right - 8, row.y + 4, 13, SHD, False, "r")
        if it[2] == "pixel" and pixel:
            pygame.draw.lines(scr, (200, 40, 40), False, [(row.right - 40, row.y + 11), (row.right - 36, row.y + 16), (row.right - 28, row.y + 5)], 3)
        y += 22


def chrome(title, menu_idx):
    scr.fill(SLATE); pygame.draw.rect(scr, (86, 94, 130), (0, 0, SW, SH), 3, border_radius=6)
    tb = pygame.Rect(4, 4, SW - 8, 18); titlebar(tb, title)
    for mi, rr in enumerate(menu_bar_rects()):
        on = mi == menu_idx
        pygame.draw.rect(scr, INKB, rr, border_radius=8); pygame.draw.rect(scr, YEL if on else (222, 228, 248), rr.inflate(-2, -2), border_radius=7)
        text(MENUS[mi][0], rr.centerx, rr.centery - 1, 14, BLK, True, "c")
    pygame.draw.rect(scr, (16, 18, 30), (CX - 3, CY - 3, W + 6, H + 6), border_radius=4)
    pygame.draw.rect(scr, (120, 134, 190), (CX - 2, CY - 2, W + 4, H + 4), 1)


def status(fields):
    x = 6
    for t, w in fields:
        w = w or (SW - 6 - x)
        r = pygame.Rect(x, CY + H + 5, w, 22); rbox(r, FACE, INKB, BLUEL, 7); text(t, r.x + 8, r.y + 4, 13, BLK); x += w + 3


cfg = dict(mode="rehab", lo=40, hi=60, hold=6, rest=5, reps=4, step=5, thr=70, ceil=80)
LIMITS = dict(lo=(5, 90, 5), hi=(10, 100, 5), hold=(3, 15, 1), rest=(2, 15, 1), reps=(1, 8, 1), step=(0, 10, 5), thr=(10, 95, 5), ceil=(50, 100, 5))
LABELS = dict(mode="Mode", lo="Sweet spot: low", hi="Sweet spot: high", hold="Climb time per hill", rest="Refuel rest per hill",
              reps="Hills to climb", step="Gradual load: added per hill", thr="Effort threshold", ceil="Ceiling warning")
UNITS = dict(mode="", thr="%", lo="%", hi="%", hold=" s", rest=" s", reps="", step="%", ceil="%")


def rows():
    """Setup rows for the current mode. Recovery = hills with rest stops. Training = one long incline."""
    return ["mode", "lo", "hi", "hold", "rest", "reps", "step", "ceil"] if cfg["mode"] == "rehab" else ["mode", "thr", "ceil"]


BASEY, L, PRE, PLAT, DOWN = 470, 1100, 260, 560, 520
CYCLE = PRE + L + PLAT + DOWN
DS = [260] * 10
CAR_X = 300
TRAIN = False             # True only while a Training run is on screen (switches ground() to the long incline)
TSLOPE, TEASE = 0.30, 400  # training incline: rise per pixel, and the length of the gentle start-up curve
TRAIN_V = 110             # px per second at full power on the training hill
TMARK = 1100              # a milestone sign every 1100 px = 10 s of full-power climbing
TRAIN_GRACE = 2.0         # seconds below the threshold before the training climb ends


RELAX_THR = 0.15          # effort at or below this counts as relaxed (same rule as the rest stop)
RX_TIMEOUT = 4.0          # seconds to respond to a cue before it counts as missed
DIRNAME = os.path.dirname(os.path.abspath(__file__))
RX_LOG = os.path.join(DIRNAME, "reflex_log.csv")
FAT_LOG = os.path.join(DIRNAME, "fatigue_log.csv")
SESSION_ID = time.strftime("%Y%m%d-%H%M%S")
CAL_S = 4
FAT_ONSET_PCT = 12


def log_rx(hill, kind, lat, thr, eff0):
    """Append one reflex measurement to reflex_log.csv."""
    new = not os.path.exists(RX_LOG)
    try:
        with open(RX_LOG, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["session", "clock", "hill", "event", "latency_s", "threshold_pct", "effort_at_cue_pct"])
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


# ---- Reaction spectrum -------------------------------------------------------------------------------------------
RX_BANDS = {
    "on":  [(0.45, "Excellent", (70, 185, 80)), (0.70, "Good", (160, 205, 70)), (1.00, "Average", (245, 205, 60)),
            (1.50, "Slow", (245, 145, 55)), (None, "Very slow", (225, 90, 80))],
    "off": [(0.55, "Excellent", (70, 185, 80)), (0.85, "Good", (160, 205, 70)), (1.20, "Average", (245, 205, 60)),
            (1.80, "Slow", (245, 145, 55)), (None, "Very slow", (225, 90, 80))],
}
RX_SCALE = {"on": 2.0, "off": 2.4}
RX_MIN_HISTORY = 5


def rx_band(kind, lat):
    for i, (lim, _n, _c) in enumerate(RX_BANDS[kind]):
        if lim is None or lat <= lim:
            return i
    return len(RX_BANDS[kind]) - 1


def rx_history(kind, sid):
    ev, out = ("squeeze" if kind == "on" else "relax"), []
    try:
        with open(RX_LOG, newline="") as f:
            for row in list(csv.reader(f))[1:]:
                if len(row) >= 5 and row[0] != sid and row[3] == ev:
                    try: out.append(float(row[4]))
                    except ValueError: pass
    except OSError:
        pass
    return out


def draw_rx_bar(x, y, w, kind, title, avg, best, n, miss, hist):
    bands, top = RX_BANDS[kind], RX_SCALE[kind]
    text(title, x, y, 14, BLK, True)
    if avg is None:
        text("Not measured this session.", x, y + 22, 13, (96, 96, 96))
        return y + 52
    px = lambda v: x + int(min(v, top) / top * w)
    edges = [0.0] + [b[0] for b in bands[:-1]] + [top]
    by, bh = y + 40, 22
    for i, (lim, name, col) in enumerate(bands):
        r_ = pygame.Rect(px(edges[i]), by, px(edges[i + 1]) - px(edges[i]), bh)
        pygame.draw.rect(scr, col, r_)
        text(name, r_.centerx, r_.centery - 1, 11, BLK, True, "c")
    pygame.draw.rect(scr, BLK, (x, by, w, bh), 1)
    for e in edges[1:-1]:
        pygame.draw.line(scr, BLK, (px(e), by), (px(e), by + bh + 3))
        text(f"{e:.2f}s", px(e), by + bh + 4, 11, (64, 64, 64), False, "c")
    text("faster", x, by + bh + 4, 11, (64, 64, 64)); text("slower", x + w, by + bh + 4, 11, (64, 64, 64), False, "r")
    pygame.draw.line(scr, (0, 0, 190), (px(best), by), (px(best), by + bh - 1), 3)
    mx = px(avg)
    pygame.draw.polygon(scr, BLK, [(mx - 7, by - 10), (mx + 7, by - 10), (mx, by - 1)])
    lab = f"you: {avg:.2f} s"
    text(lab, max(x + 36, min(x + w - 36, mx)), by - 24, 12, BLK, True, "c")
    bi, bj = rx_band(kind, avg), rx_band(kind, best)
    text(f"Rank: {bands[bi][1]}", x, by + bh + 22, 13, BLK, True)
    text(f"avg {avg:.2f} s   best {best:.2f} s ({bands[bj][1]}, blue line)   {n} measured / {miss} missed", x + 150, by + bh + 22, 12, BLK)
    if len(hist) >= RX_MIN_HISTORY:
        s_ = sorted(hist); med = s_[len(s_) // 2]
        faster = 100 * sum(1 for h in hist if h > avg) / len(hist)
        msg = f"Vs your earlier sessions: faster than {faster:.0f}% of your {len(hist)} earlier reactions (your usual: {med:.2f} s)."
    else:
        msg = f"Vs your earlier sessions: needs {RX_MIN_HISTORY}+ earlier reactions to compare (have {len(hist)})."
    text(msg, x, by + bh + 40, 12, (64, 64, 64))
    return by + bh + 62


def draw_rx_report(c, res, sid, demo=False):
    rxs = rx_stats(res)
    text("Where your muscle reaction times land", c.x + 16, c.y + 12, 14, BLK, True)
    y = c.y + 38
    for k, lab in ((("on", "Squeeze reaction"),) if res.get("train") else (("on", "Squeeze reaction"), ("off", "Relax reaction"))):
        avg, best, n_, miss = rxs[k]
        y = draw_rx_bar(c.x + 30, y + 14, c.w - 60, k, lab, avg, best, n_, miss, rx_history(k, sid)) + 6
    y = para("Measured from your EMG signal, not a button press: the time from the on-screen cue until your muscle effort "
             "crosses the target threshold. It includes ~0.2 s of smoothing and is slower for higher thresholds, so the bands "
             "are guidelines for tracking your progress, not clinical norms. Compare sessions with the same setup.",
             c.x + 16, y + 4, c.w - 32, 12)
    if demo:
        para("DEMO signal (keyboard-simulated): these ranks are not meaningful.", c.x + 16, y + 6, c.w - 32, 13, (192, 0, 0), True)


# ---- Fatigue integration -----------------------------------------------------------------------------------------
def log_fat(game, label, o):
    new = not os.path.exists(FAT_LOG)
    try:
        with open(FAT_LOG, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["session", "clock", "game", "hold", "hold_s", "mdf_ref_hz", "mdf_slope_pct_per_s",
                            "total_drop_pct", "fatigue_onset_s", "mean_activation_pct"])
            w.writerow([SESSION_ID, time.strftime("%H:%M:%S"), game, label, o["hold_s"], o["mdf_ref_hz"], o["mdf_slope_pct_per_s"],
                        o["total_drop_pct"], "" if o["fatigue_onset_s"] is None else round(o["fatigue_onset_s"], 2), o["mean_activation_pct"]])
    except OSError:
        pass


def fat_open(sig, r, label, game):
    r.setdefault("fat", []); r.setdefault("fat_short", 0); r.setdefault("fat_tracked", False)
    if r.get("fh") or not getattr(sig, "fm_ready", False):
        return
    sig.fm.start_hold()
    r.update(fh=True, fh_flag=False, fh_label=label, fh_game=game, fat_tracked=True)


def fat_close(sig, r):
    if not r or not r.get("fh"):
        return
    r["fh"] = False
    out = sig.fm.end_hold()
    if out:
        r["fat"].append(dict(out, label=r["fh_label"]))
        log_fat(r["fh_game"], r["fh_label"], out)
    else:
        r["fat_short"] += 1


def fat_onset_new(sig, r):
    if not r.get("fh") or r.get("fh_flag"):
        return False
    if sig.fm.status.get("onset_s") is None:
        return False
    r["fh_flag"] = True
    return True


def fat_rows(r):
    f = r.get("fat", [])
    if not f:
        why = "holds too short (<3 s)" if r.get("fat_short") else "not measured" if r.get("fat_tracked") else "tracking off (see Calibration)"
        return [("Fatigue onset", why)]
    onsets = [x["fatigue_onset_s"] for x in f if x["fatigue_onset_s"] is not None]
    drop = sum(x["total_drop_pct"] for x in f) / len(f)
    slope = sum(x["mdf_slope_pct_per_s"] for x in f) / len(f)
    if r.get("train"):
        on = onsets[0] if onsets else None
        ht = r.get("holdT", r.get("hl", 0.0))
        rows = [("Fatigue onset (muscle)", f"{on:.1f} s into hold" if on is not None else "not reached"),
                ("Hold failure (left zone)", f"{ht:.1f} s")]
        if on is not None:
            rows.append(("Held after onset", f"~{max(0.0, ht - on):.1f} s"))
        rows.append(("Median-freq drop", f"{drop:.1f}%"))
        return rows
    return [("Fatigue onset (avg)", f"{sum(onsets) / len(onsets):.1f} s ({len(onsets)}/{len(f)} holds)" if onsets else f"not reached in {len(f)} holds"),
            ("Median-freq drop", f"{drop:.1f}% (avg)"),
            ("Frequency slope", f"{slope:+.2f} %/s")]


def fatigue_hud(sig, r, x, y):
    c = window((x, y, 236, 96), "Muscle Fatigue")
    if not getattr(sig, "fm_ready", False):
        text("Tracking off", c.x + 8, c.y + 4, 13, BLK, True)
        note = getattr(sig, "fm_note", "Calibrate (B, then X)")
        para(note, c.x + 8, c.y + 24, c.w - 16, 11)
        return
    st, live = sig.fm.status, bool(r.get("fh"))
    pct = st["fatigue_pct"] if live else 0.0
    onset = st.get("onset_s") if live else None
    frac = min(1.0, pct / 20.0)
    bar = (c.x + 10, c.y + 6, c.w - 20, 18)
    progress(bar, frac, (192, 0, 0) if (onset is not None or frac >= .6) else (0, 160, 0))
    tx = bar[0] + int(bar[2] * FAT_ONSET_PCT / 20.0)
    pygame.draw.line(scr, BLK, (tx, bar[1] - 2), (tx, bar[1] + bar[3] + 2), 2)
    label = (f"FATIGUED at {onset:.1f} s" if onset is not None else "Tiring" if pct >= 6 else "Fresh") if live else "Waiting for a hold"
    text(label, c.x + 10, c.y + 30, 13, (192, 0, 0) if onset is not None else BLK, True)
    mdf = st.get("mdf")
    text(f"Median freq {mdf:.0f} Hz" if (mdf is not None and math.isfinite(mdf)) else "Median freq --", c.x + 10, c.y + 48, 12, (64, 64, 64))


class Signal:
    """Muscle signal with envelope & raw sample processing for fatigue tracking."""
    GAIN = 5.0
    NOMINAL_FS = 500

    def __init__(self, port):
        self.raw = self.sm = self.v = self.lvl = 0.0
        self.demo = port in (None, "demo")
        self.fm, self.fm_ready = None, False
        self.fm_note = "Calibrate (B, then X) to switch tracking on."
        self.rate = float(self.NOMINAL_FS)
        self._rate_n, self._rate_t0 = 0, time.time()
        self._ring = deque(maxlen=self.NOMINAL_FS * 10)
        self._fcal, self._rng = {}, None
        self.fat_possible = FAT_OK
        if not FAT_OK:
            self.fm_note = "Fatigue off: needs fatigue_monitor.py, numpy and scipy."
        if self.demo:
            if FAT_OK: self._demo_init()
        else:
            from emg_core import EMGSource
            self.src = EMGSource(None if port == "auto" else port)
            self.src.start()
            print("Connecting to the board... keep your arm still for a few seconds.")
            self.src.wait_ready()
            if not hasattr(self.src, "drain_raw"):
                self.fat_possible = False
                self.fm_note = "Fatigue off: replace emg_core.py with latest version."

    def _demo_init(self):
        fs = self.NOMINAL_FS
        self._rng = np.random.default_rng(7)
        self._hi = butter(4, [60, 220], "bandpass", fs=fs, output="sos")
        self._lo = butter(4, [20, 90], "bandpass", fs=fs, output="sos")
        self._hz, self._lz = sosfilt_zi(self._hi) * 0, sosfilt_zi(self._lo) * 0
        probe = self._rng.standard_normal(20000)
        self._hs, self._ls = sosfilt(self._hi, probe).std(), sosfilt(self._lo, probe).std()
        self._carry, self._tired = 0.0, 0.0

    def _demo_raw(self, dt):
        if self._rng is None: return []
        self._carry += dt * self.NOMINAL_FS
        n = int(self._carry); self._carry -= n
        if n <= 0: return []
        self._tired = min(1.0, self._tired + dt * (.4 + self.v) / 7) if self.v > .2 else max(0.0, self._tired - dt / 3)
        hi, self._hz = sosfilt(self._hi, self._rng.standard_normal(n), zi=self._hz)
        lo, self._lz = sosfilt(self._lo, self._rng.standard_normal(n), zi=self._lz)
        x = 512 + 60.0 * self.v * (math.sqrt(1 - self._tired) * hi / self._hs + math.sqrt(self._tired) * lo / self._ls) + self._rng.normal(0, 2, n)
        return x.tolist()

    def update(self, dt, keys):
        if self.demo:
            tgt = 1.0 if keys[pygame.K_SPACE] else self.lvl
            self.v += (tgt - self.v) * min(1, dt * 6)
            self.raw = 80 + self.v * 700 + random.uniform(-.5, .5) * (25 + self.v * 50)
            samples = self._demo_raw(dt)
        else:
            self.raw = self.src.envelope * self.GAIN
            samples = self.src.drain_raw() if hasattr(self.src, "drain_raw") else []
        self.sm += (self.raw - self.sm) * min(1, dt * 10)
        self._pump(samples)

    def _pump(self, samples):
        if samples:
            self._ring.extend(samples)
            self._rate_n += len(samples)
        now = time.time()
        elapsed = now - self._rate_t0
        if elapsed >= 1.0:
            calc_rate = self._rate_n / elapsed
            if 300 <= calc_rate <= 800:
                self.rate = 0.7 * self.rate + 0.3 * calc_rate
            self._rate_n = 0
            self._rate_t0 = now
        if self.fm_ready:
            for v_ in samples: self.fm.feed(v_)

    def fatigue_cal(self, kind):
        if not self.fat_possible: return
        n = int(CAL_S * (self.rate or self.NOMINAL_FS))
        self._fcal[kind] = list(self._ring)[-n:]
        if "base" in self._fcal and "max" in self._fcal:
            self._fat_setup()

    def _fat_setup(self):
        self.fm_ready = False
        fs = int(round(self.rate)) if 350 <= self.rate <= 700 else self.NOMINAL_FS
        try:
            self.fm = FatigueMonitor(fs=fs)
            info = self.fm.calibrate(self._fcal["base"], self._fcal["max"])
        except ValueError as e:
            self.fm_note = f"Calibration note: {e}"
            return
        if not info.get("ok", False):
            self.fm_note = "Max squeeze too close to relaxed. Record max (X) harder."
            return
        self.fm_ready = True
        self.fm_note = "ready"

    def fat_msg(self):
        return f"Fatigue tracking: ready ({self.rate:.0f} Hz signal)" if self.fm_ready else f"Fatigue tracking: {self.fm_note}"


def smooth(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def hsh(k):
    k = (int(k) * 374761393 + 668265263) & 0xffffffff
    k = ((k ^ (k >> 13)) * 1274126177) & 0xffffffff
    return ((k ^ (k >> 16)) & 0xffff) / 65535


def ground(x):
    if TRAIN:
        lx = x - PRE
        h = 0.0 if lx <= 0 else TSLOPE * lx * lx / (2 * TEASE) if lx < TEASE else TSLOPE * (lx - TEASE / 2)
        return BASEY - h + 5 * math.sin(x * .03) + 3 * math.sin(x * .081 + 1)
    i = max(0, min(len(DS) - 1, int(x // CYCLE)))
    lx, D = x - i * CYCLE, DS[i]
    if lx < PRE: h = 0.0
    elif lx < PRE + L: h = D * smooth((lx - PRE) / L)
    elif lx < PRE + L + PLAT: h = D
    else: h = D * (1 - smooth((lx - PRE - L - PLAT) / DOWN))
    return BASEY - h + 5 * math.sin(x * .03) + 3 * math.sin(x * .081 + 1)


def c_start(i): return i * CYCLE + PRE
def c_top(i): return i * CYCLE + PRE + L
def c_station(i): return i * CYCLE + PRE + L + PLAT // 2


class Art:
    def __init__(self):
        self.sky = pygame.Surface((W, H))
        for y in range(H):
            pygame.draw.line(self.sky, lerp((92, 200, 255), (255, 236, 200), y / H), (0, y), (W, y))
        self.T = 1400
        self.mount = []
        for col, hmin, hmax, seed in (((190, 170, 235), 140, 230, 1), ((255, 170, 190), 90, 170, 7)):
            s = pygame.Surface((self.T, 340), pygame.SRCALPHA)
            x = -100
            while x < self.T + 100:
                w = 200 + hsh(x + seed) * 200; h_ = hmin + hsh(x * 3 + seed) * (hmax - hmin)
                pygame.draw.polygon(s, col, [(x, 340), (x + w / 2, 340 - h_), (x + w, 340)])
                pygame.draw.polygon(s, (255, 255, 255, 190), [(x + w / 2, 340 - h_), (x + w / 2 - 22, 340 - h_ + 40), (x + w / 2 + 22, 340 - h_ + 40)])
                x += w * .7
            self.mount.append(s)
        self.clouds = []
        for i in range(8):
            s = pygame.Surface((220, 100), pygame.SRCALPHA)
            for cx, cy, r in ((60, 60, 34), (100, 44, 44), (146, 58, 36), (110, 66, 34)):
                pygame.draw.circle(s, (255, 255, 255, 225), (cx, cy), r)
            sc = .6 + hsh(i) * .6
            self.clouds.append((pygame.transform.smoothscale(s, (int(220 * sc), int(100 * sc))), hsh(i + 50) * (W + 400), 30 + hsh(i + 9) * 230, .1 + hsh(i + 3) * .3))
        self.warn = pygame.Surface((W, 150), pygame.SRCALPHA)
        for y in range(150):
            pygame.draw.line(self.warn, (232, 60, 50, int(150 * (1 - y / 150) ** 2)), (0, y), (W, y))


def make_car(wa, hot):
    S = pygame.Surface((160, 110), pygame.SRCALPHA)
    ol, body = (58, 24, 44), lerp((236, 64, 150), (255, 90, 30), hot)
    pygame.draw.rect(S, ol, (14, 48, 132, 36), border_radius=14)
    pygame.draw.rect(S, body, (18, 51, 124, 29), border_radius=12)
    pygame.draw.polygon(S, ol, [(50, 50), (66, 16), (114, 16), (126, 50)])
    pygame.draw.polygon(S, (150, 228, 255), [(57, 48), (69, 22), (111, 22), (119, 48)])
    pygame.draw.circle(S, (255, 214, 100), (92, 38), 11)
    pygame.draw.circle(S, (30, 30, 40), (96, 35), 2); pygame.draw.circle(S, (30, 30, 40), (88, 35), 2)
    pygame.draw.arc(S, (150, 50, 50), (85, 36, 14, 8), 3.4, 6.0, 2)
    pygame.draw.rect(S, (70, 120, 245), (82, 22, 22, 7), border_radius=3)
    pygame.draw.rect(S, (255, 230, 80), (24, 63, 112, 6), border_radius=3)
    pygame.draw.circle(S, (255, 245, 150), (141, 62), 6); pygame.draw.circle(S, (255, 90, 90), (20, 60), 5)
    pygame.draw.rect(S, (90, 90, 110), (3, 66, 15, 6), border_radius=3)
    for cx in (40, 120):
        pygame.draw.circle(S, (30, 30, 40), (cx, 86), 21)
        pygame.draw.circle(S, (90, 215, 200), (cx, 86), 13)
        for j in range(4):
            a = wa + j * math.pi / 2
            pygame.draw.line(S, (255, 255, 255), (cx, 86), (cx + math.cos(a) * 12, 86 + math.sin(a) * 12), 3)
        pygame.draw.circle(S, (255, 200, 60), (cx, 86), 4)
    return S


def doodle_tree(sx, gy, k):
    cols = ((70, 190, 90), (255, 160, 60), (240, 110, 170), (150, 110, 230))
    s = 30 + hsh(k + 4) * 26; c = cols[int(hsh(k + 2) * 4)]
    pygame.draw.rect(scr, (140, 90, 50), (sx - 6, gy - s * 1.5, 12, s * 1.5 + 4))
    for dx, dy, r in ((0, -s * 1.6, s * .8), (-s * .55, -s * 1.3, s * .55), (s * .55, -s * 1.3, s * .55)):
        pygame.draw.circle(scr, c, (sx + dx, gy + dy), r)
    pygame.draw.circle(scr, lerp(c, (255, 255, 255), .35), (sx - s * .2, gy - s * 1.8), s * .22)


def doodle_flower(sx, gy, k):
    c = ((255, 90, 120), (255, 220, 60), (120, 160, 255), (255, 255, 255))[int(hsh(k + 8) * 4)]
    for j in range(3):
        x = sx + (j - 1) * 14; hh = 20 + hsh(k + j) * 14
        pygame.draw.line(scr, (50, 150, 60), (x, gy), (x, gy - hh), 3)
        for a in range(5):
            pygame.draw.circle(scr, c, (x + math.cos(a * 1.257) * 6, gy - hh + math.sin(a * 1.257) * 6), 4)
        pygame.draw.circle(scr, (255, 190, 40), (x, gy - hh), 3)


def doodle_mush(sx, gy, k):
    pygame.draw.rect(scr, (250, 240, 220), (sx - 5, gy - 16, 10, 18), border_radius=4)
    pygame.draw.ellipse(scr, (235, 60, 70), (sx - 18, gy - 34, 36, 26))
    for dx, dy in ((-8, -26), (4, -30), (9, -22)):
        pygame.draw.circle(scr, (255, 255, 255), (sx + dx, gy + dy), 3)


def doodle_rock(sx, gy, k):
    pygame.draw.ellipse(scr, (130, 134, 150), (sx - 20, gy - 20, 40, 26))
    pygame.draw.ellipse(scr, (176, 180, 196), (sx - 14, gy - 18, 18, 10))


def doodle_cactus(sx, gy, k):
    c = (60, 170, 110)
    pygame.draw.rect(scr, c, (sx - 8, gy - 52, 16, 56), border_radius=8)
    pygame.draw.rect(scr, c, (sx - 24, gy - 36, 12, 8), border_radius=4); pygame.draw.rect(scr, c, (sx - 24, gy - 46, 8, 18), border_radius=4)
    pygame.draw.rect(scr, c, (sx + 12, gy - 28, 12, 8), border_radius=4); pygame.draw.rect(scr, c, (sx + 16, gy - 40, 8, 18), border_radius=4)
    pygame.draw.circle(scr, (255, 120, 170), (sx, gy - 54), 5)


def dirt_doodle(sx, y, kind, k):
    if kind == 0:
        pygame.draw.line(scr, (250, 244, 230), (sx - 14, y), (sx + 14, y + 4), 5)
        for ex in (-14, 14):
            pygame.draw.circle(scr, (250, 244, 230), (sx + ex, y + (0 if ex < 0 else 4) - 3), 5); pygame.draw.circle(scr, (250, 244, 230), (sx + ex, y + (0 if ex < 0 else 4) + 3), 5)
    elif kind == 1: pygame.draw.circle(scr, (150, 120, 100), (sx, y), 9); pygame.draw.circle(scr, (190, 160, 140), (sx - 3, y - 3), 3)
    elif kind == 2: star(sx, y, 10, (255, 214, 70))
    elif kind == 3: pygame.draw.lines(scr, (255, 150, 170), False, [(sx + i * 6, y + (6 if i % 2 else -6)) for i in range(-3, 4)], 5)
    else: pygame.draw.circle(scr, (210, 160, 110), (sx, y), 11, 3); pygame.draw.circle(scr, (210, 160, 110), (sx, y), 5, 2)


def scene(art, cx, cy, t, nh):
    scr.blit(art.sky, (0, 0))
    sx, sy = 880, 90
    for j in range(12):
        a = j * math.pi / 6 + t * .3
        pygame.draw.line(scr, (255, 214, 80), (sx + math.cos(a) * 52, sy + math.sin(a) * 52), (sx + math.cos(a) * 70, sy + math.sin(a) * 70), 5)
    pygame.draw.circle(scr, (255, 226, 90), (sx, sy), 46)
    pygame.draw.circle(scr, (60, 40, 20), (sx - 15, sy - 8), 4); pygame.draw.circle(scr, (60, 40, 20), (sx + 15, sy - 8), 4)
    pygame.draw.arc(scr, (60, 40, 20), (sx - 22, sy - 6, 44, 30), 3.6, 5.8, 4)
    pygame.draw.circle(scr, (255, 170, 150), (sx - 28, sy + 8), 7); pygame.draw.circle(scr, (255, 170, 150), (sx + 28, sy + 8), 7)
    for img, par in zip(art.mount, (.12, .25)):
        off = -(cx * par) % art.T
        yy = 290 + (62 - cy) * .15 * (1 + par)
        scr.blit(img, (off - art.T, yy)); scr.blit(img, (off, yy))
    for img, bx, by, sp in art.clouds:
        scr.blit(img, ((bx - t * 14 - cx * sp * .3) % (W + 400) - 220, by))
    bx = (t * 22 + 200) % (W + 300) - 150; by = 150 + 18 * math.sin(t * .8)
    pygame.draw.ellipse(scr, (255, 90, 110), (bx - 30, by - 44, 60, 70))
    for j, c in enumerate(((255, 220, 70), (80, 190, 255))):
        pygame.draw.ellipse(scr, c, (bx - 30 + 12 + j * 14 - 12, by - 44, 14, 70), 0)
    pygame.draw.line(scr, (90, 60, 40), (bx - 12, by + 22), (bx - 8, by + 38), 2); pygame.draw.line(scr, (90, 60, 40), (bx + 12, by + 22), (bx + 8, by + 38), 2)
    pygame.draw.rect(scr, (170, 110, 60), (bx - 12, by + 38, 24, 14), border_radius=3)
    for j in range(3):
        px = (t * 60 + j * 180) % (W + 200) - 100; py = 80 + j * 34 + 8 * math.sin(t * 3 + j)
        w = 6 * math.sin(t * 9 + j)
        pygame.draw.lines(scr, (60, 60, 90), False, [(px - 10, py - w), (px, py), (px + 10, py - w)], 3)
    pts = [(sx_, ground(cx + sx_) - cy) for sx_ in range(-8, W + 16, 8)]
    pygame.draw.polygon(scr, (160, 104, 60), pts + [(W + 8, H + 10), (-8, H + 10)])
    for dy, wd, c in ((52, 20, (190, 132, 84)), (118, 12, (138, 88, 52)), (176, 16, (206, 150, 98))):
        pygame.draw.lines(scr, c, False, [(a, b + dy) for a, b in pts], wd)
    for k in range(int(cx // 100) - 1, int((cx + W) // 100) + 2):
        wx = k * 100 + hsh(k + 99) * 80
        dirt_doodle(wx - cx, ground(wx) - cy + 56 + hsh(k + 7) * 80, int(hsh(k + 5) * 5), k)
    pygame.draw.lines(scr, (96, 190, 64), False, pts, 20)
    pygame.draw.lines(scr, (150, 232, 100), False, [(a, b - 6) for a, b in pts], 6)
    for k in range(int(cx // 130) - 1, int((cx + W) // 130) + 2):
        r = hsh(k)
        wx = k * 130 + hsh(k + 3) * 90
        lx = wx % CYCLE
        if r > .74 or (PRE + L - 80 < lx < PRE + L + PLAT + 80) or abs(lx - PRE) < 60:
            continue
        f = (doodle_tree, doodle_flower, doodle_mush, doodle_rock, doodle_cactus)[0 if r < .26 else 1 if r < .42 else 2 if r < .55 else 3 if r < .64 else 4]
        f(wx - cx, ground(wx) - cy + 4, k)
    for i in range(nh):
        x = c_start(i) - cx
        if -100 < x < W + 100:
            g = ground(c_start(i)) - cy
            pygame.draw.rect(scr, (130, 84, 46), (x - 4, g - 70, 8, 72)); pygame.draw.rect(scr, (255, 214, 90), (x - 38, g - 100, 76, 34), border_radius=8)
            text(f"HILL {i + 1}", x, g - 83, 18, (90, 50, 20), True, "c")
        x = c_top(i) - cx
        if -100 < x < W + 100:
            g = ground(c_top(i)) - cy
            pygame.draw.rect(scr, (240, 240, 240), (x - 3, g - 120, 6, 122))
            for a in range(4):
                for b in range(3):
                    pygame.draw.rect(scr, (20, 20, 30) if (a + b) % 2 else (255, 255, 255), (x + 3 + a * 12, g - 120 + b * 12, 12, 12))
        x = c_station(i) - cx
        if -200 < x < W + 200:
            g = ground(c_station(i)) - cy
            pygame.draw.rect(scr, (255, 150, 190), (x - 70, g - 96, 140, 98), border_radius=10)
            pygame.draw.polygon(scr, (120, 100, 230), [(x - 84, g - 96), (x, g - 140), (x + 84, g - 96)])
            pygame.draw.rect(scr, (255, 255, 255), (x - 50, g - 82, 100, 30), border_radius=6)
            text("REST STOP", x, g - 67, 17, (90, 50, 120), True, "c")
            pygame.draw.rect(scr, (110, 220, 200), (x - 54, g - 40, 30, 40), border_radius=5)
            pygame.draw.rect(scr, (240, 70, 80), (x + 36, g - 56, 22, 58), border_radius=6)
            pygame.draw.rect(scr, (255, 255, 255), (x + 40, g - 50, 14, 14), border_radius=3)
            pygame.draw.lines(scr, (40, 40, 50), False, [(x + 58, g - 40), (x + 70, g - 30), (x + 68, g - 12)], 3)
    if TRAIN:
        for k in range(max(0, int((cx - PRE) // TMARK) - 1), int((cx + W - PRE) // TMARK) + 2):
            wx = PRE + k * TMARK; x = wx - cx
            if -100 < x < W + 100:
                g = ground(wx) - cy
                pygame.draw.rect(scr, (130, 84, 46), (x - 4, g - 70, 8, 72)); pygame.draw.rect(scr, (255, 214, 90), (x - 38, g - 100, 76, 34), border_radius=8)
                text("START" if k == 0 else f"{k * 10} s", x, g - 83, 18, (90, 50, 20), True, "c")


def draw_car(art, cxw, cy, wa, power, hot, t):
    gb, gf = ground(cxw - 40), ground(cxw + 40)
    ang = math.degrees(math.atan2(gb - gf, 80)) + power * 3
    sc_y = (gb + gf) / 2 - cy - 52 - 2.5 * power * abs(math.sin(t * 15))
    img = pygame.transform.rotozoom(make_car(wa, hot), ang, 1.0)
    scr.blit(img, img.get_rect(center=(CAR_X, sc_y)))
    return sc_y


HELP_LINES = ("Your muscle is the gas pedal.", "", "- Squeeze to the SWEET SPOT (the green zone on the Effort meter) and hold it:",
              "  the car climbs. Too little and it stalls, too much and the power fades.", "- Above the MAX line the engine overheats. Ease off until it cools.",
              "- Reach the checkered flag to win the hill. The fuel bar is your time limit.", "- At the REST STOP, relax your muscle to refuel. The pump only runs",
              "  while you are relaxed, so the rest is really taken.", "- When GO! appears, squeeze fast. When RELAX! appears, let go fast.",
              "  Your reaction times are shown in the Reaction Report.", "- Each hill's sweet spot rises a little (gradual loading).",
              "- TRAINING mode: one very long incline. Hold above threshold as long", "  as you can. Dipping below it for more than 2 s ends climb.",
              "- MUSCLE FATIGUE box: median frequency falls as muscle tires.", "  Fatigue onset is detected & recorded to fatigue_log.csv.", "",
              "Stop if you feel sharp pain. F2: new session   Esc: end session")


def main(sig=None, embedded=False):
    global scr, DS
    port = sys.argv[1] if len(sys.argv) > 1 else "demo"
    if sig is None:
        sig = Signal(port)
    cfg["mode"] = "rehab"
    pygame.init()
    screen = pygame.display.set_mode((SW, SH))
    canvas = pygame.Surface((W, H))
    scr = canvas
    pygame.display.set_caption("ReGain Climb")
    clock = pygame.time.Clock()
    art = Art()
    scene_name, sel = "setup", 0
    pain1 = pain2 = 0
    base = mx = None
    rec = None
    calmsg = "Press B to record relaxed, then X for max squeeze."
    run = res = summary = None
    parts, pops = [], []
    modal, menu, pixel, leave = None, None, True, None
    camy, wa, lastx, scroll = 62.0, 0.0, 0.0, 0.0

    def act():
        if base is None or mx is None:
            return 0.0
        return max(0.0, min(1.0, (sig.sm - base) / max(1.0, mx - base)))

    def calibrated():
        return base is not None and mx is not None and mx - base >= 50 and mx >= base * 1.30

    def burst(x, y, n, cols, g=260):
        for _ in range(n):
            a_, sp = random.uniform(0, 6.28), random.uniform(80, 320)
            parts.append([x, y, math.cos(a_) * sp, math.sin(a_) * sp - 160, 0, random.uniform(.7, 1.3), random.choice(cols), random.randint(3, 6), g])

    def start_run():
        if cfg["mode"] == "train":
            thr = cfg["thr"] / 100
            globals()["DS"] = [260] * 10
            return dict(train=True, ph="ready", pt=0.0, t=0.0, i=0, prog=0.0, fuel=1.0, heat=0.0, stall=False, cx=40.0, x0=0.0, x1=0.0, dur=1.0, fail=False,
                        bands=[(thr, 1.0)], reps=0, coins=0, got=set(), tc=0.0, tin=0.0, effsum=0.0, peak=0.0, over=0.0, rr=0.0, rest_t=0.0, rest_ok=0.0,
                        eff=0.0, power=0.0, inz=False, warn=False, cue="Get ready", ceil=cfg["ceil"] / 100, N=1, thr=thr,
                        hs=None, hl=0.0, slack=0.0,
                        rdy=random.uniform(2.5, 4.0), rx_pending=False, rx_kind="on", rx_t0=0.0, rx_eff0=0.0,
                        rx=dict(on=[], off=[]), rx_miss=dict(on=0, off=0), rx_skip=dict(on=0, off=0))
        bands = []
        for i in range(cfg["reps"]):
            lo = min(cfg["lo"] + i * cfg["step"], 90); hi = min(100, max(cfg["hi"] + i * cfg["step"], lo + 5))
            bands.append((lo / 100, hi / 100))
        DS = [int(190 + (b[0] + b[1]) / 2 * 330) for b in bands] + [260] * 3
        globals()["DS"] = DS
        return dict(ph="ready", pt=0.0, t=0.0, i=0, prog=0.0, fuel=1.0, heat=0.0, stall=False, cx=40.0, x0=0.0, x1=0.0, dur=1.0, fail=False,
                    bands=bands, reps=0, coins=0, got=set(), tc=0.0, tin=0.0, effsum=0.0, peak=0.0, over=0.0, rr=0.0, rest_t=0.0, rest_ok=0.0,
                    eff=0.0, power=0.0, inz=False, warn=False, cue="Get ready", ceil=cfg["ceil"] / 100, N=cfg["reps"],
                    rdy=random.uniform(2.5, 4.0), rx_pending=False, rx_kind="on", rx_t0=0.0, rx_eff0=0.0,
                    rx=dict(on=[], off=[]), rx_miss=dict(on=0, off=0), rx_skip=dict(on=0, off=0))

    def goto(r, ph, x1, dur, fail=False):
        r.update(ph=ph, pt=0.0, x0=r["cx"], x1=x1, dur=dur, fail=fail)

    def rx_arm(r, kind):
        if r["rx_pending"]:
            r["rx_miss"][r["rx_kind"]] += 1
        r["rx_pending"] = False
        eff = r["eff"]
        lo = r["bands"][min(r["i"], len(r["bands"]) - 1)][0]
        if (kind == "on" and eff >= lo) or (kind == "off" and eff <= RELAX_THR):
            r["rx_skip"][kind] += 1
            return
        r.update(rx_pending=True, rx_kind=kind, rx_t0=r["t"], rx_eff0=eff)

    def rx_check(r, eff):
        if not r["rx_pending"]:
            return
        kind, lat = r["rx_kind"], r["t"] - r["rx_t0"]
        thr = r["bands"][min(r["i"], len(r["bands"]) - 1)][0] if kind == "on" else RELAX_THR
        if (kind == "on" and eff >= thr) or (kind == "off" and eff <= thr):
            r["rx_pending"] = False
            r["rx"][kind].append(lat)
            log_rx(r["i"] + 1, kind, lat, thr, r["rx_eff0"])
            pops.append([("Squeeze " if kind == "on" else "Relax ") + f"{lat:.2f} s", 0.0, TEAL, r["cx"] + 40, 0])
        elif lat > RX_TIMEOUT:
            r["rx_pending"] = False
            r["rx_miss"][kind] += 1

    def step_train(r, dt):
        eff = act(); r["eff"] = eff; r["t"] += dt; r["pt"] += dt
        thr, ceil = r["thr"], r["ceil"]
        r["peak"] = max(r["peak"], eff); r["warn"] = eff > ceil; r["power"] = 0.0; r["inz"] = False
        ph, cue = r["ph"], ""
        rx_check(r, eff)
        if fat_onset_new(sig, r): pops.append(["Fatigue onset!", 0.0, AMBER, r["cx"] + 40, 0])
        if r["warn"]:
            r["over"] += dt
        if ph == "ready":
            cue = "Get ready to climb!"; r["cx"] = 40 + (c_start(0) - 40) * smooth(r["pt"] / r["rdy"])
            if r["pt"] >= r["rdy"]:
                r.update(ph="climb", pt=0.0)
                rx_arm(r, "on")
                fat_open(sig, r, 1, "climb-train")
        elif ph == "climb":
            r["heat"] = min(1.0, r["heat"] + .5 * dt) if r["warn"] else max(0.0, r["heat"] - .25 * dt)
            if r["heat"] >= 1: r["stall"] = True
            if r["heat"] < .5: r["stall"] = False
            above = eff >= thr
            p = (1.0 if eff <= ceil else max(0.0, 1 - (eff - ceil) / .3)) if above else max(0.0, 1 - (thr - eff) / .2)
            if r["stall"]: p = 0.0
            r["power"], r["inz"] = p, above
            r["tc"] += dt; r["effsum"] += eff * dt
            if above:
                r["tin"] += dt; r["slack"] = 0.0
                if r["hs"] is None: r["hs"] = r["tc"]
                r["hl"] = r["tc"] - r["hs"]
            elif r["hs"] is not None:
                r["slack"] += dt
            slipping = (not above) and r["hs"] is not None
            r["cx"] = max(c_start(0), r["cx"] + (TRAIN_V * p - (60 * (1 - p) if slipping else 0)) * dt)
            cue = ("Engine overheated! Relax a little" if r["stall"] else "Above your ceiling. Ease off now" if r["warn"] else
                   "Squeeze up to the line and hold" if r["hs"] is None else
                   f"Slipping back! Squeeze now ({max(0.0, TRAIN_GRACE - r['slack']):.1f} s)" if slipping else "Great! Keep holding")
            if r["slack"] >= TRAIN_GRACE:
                pops.append([f"Hold ended: {r['hl']:.1f} s", 0.0, AMBER, r["cx"], 0])
                r.update(ph="end", pt=0.0)
                fat_close(sig, r)
        elif ph == "end":
            cue = f"Hold ended. You held {r['hl']:.1f} s"
            if r["pt"] > 3: return True
        if r["rx_pending"]:
            cue = "GO! Squeeze up to the line and hold"
        r["cue"] = cue
        return False

    def step(r, dt):
        if r.get("train"): return step_train(r, dt)
        eff = act(); r["eff"] = eff; r["t"] += dt; r["pt"] += dt
        i = r["i"]; lo, hi = r["bands"][i]
        r["peak"] = max(r["peak"], eff); r["warn"] = eff > r["ceil"]; r["power"] = 0.0; r["inz"] = False
        ph, cue = r["ph"], ""
        rx_check(r, eff)
        if fat_onset_new(sig, r): pops.append(["Fatigue onset!", 0.0, AMBER, r["cx"] + 40, 0])
        if r["warn"]:
            r["over"] += dt
        if ph == "ready":
            cue = "Get ready to climb!"; r["cx"] = 40 + (c_start(0) - 40) * smooth(r["pt"] / r["rdy"])
            if r["pt"] >= r["rdy"]:
                r.update(ph="climb", pt=0.0)
                rx_arm(r, "on")
                fat_open(sig, r, 1, "climb")
        elif ph == "climb":
            r["heat"] = min(1.0, r["heat"] + .5 * dt) if r["warn"] else max(0.0, r["heat"] - .25 * dt)
            if r["heat"] >= 1: r["stall"] = True
            if r["heat"] < .5: r["stall"] = False
            p = 1.0 if lo <= eff <= hi else max(0.0, 1 - (lo - eff) / .2) if eff < lo else max(0.0, 1 - (eff - hi) / .3)
            if r["stall"]: p = 0.0
            r["power"], r["inz"] = p, lo <= eff <= hi
            r["tc"] += dt; r["effsum"] += eff * dt; r["tin"] += dt if r["inz"] else 0
            r["prog"] = max(0.0, min(1.0, r["prog"] + (1.25 * p - .25) / cfg["hold"] * dt))
            r["fuel"] -= dt / (1.35 * cfg["hold"])
            r["cx"] = c_start(i) + r["prog"] * L
            for k in range(10):
                if (i, k) not in r["got"] and r["prog"] >= .1 + k * .085:
                    r["got"].add((i, k))
                    if p >= .8:
                        r["coins"] += 1; pops.append(["+coin", 0.0, AMBER, r["cx"] + 30, 0])
            cue = ("Engine overheated! Relax a little" if r["stall"] else "Above your ceiling. Ease off now" if r["warn"] else
                   "More gas! Squeeze a bit more" if eff < lo else "Too much gas! Ease off a little" if eff > hi else "Perfect! Hold steady")
            if r["prog"] >= 1:
                r["reps"] += 1; burst(r["cx"], ground(r["cx"]) - 80, 60, [AMBER, TEAL, RED, GREEN, (255, 255, 255)])
                pops.append(["Hill conquered!", 0.0, GREEN, r["cx"], 0]); goto(r, "toStation", c_station(i), 1.5); rx_arm(r, "off"); fat_close(sig, r)
            elif r["fuel"] <= 0:
                pops.append(["Out of fuel! Try again", 0.0, AMBER, r["cx"], 0]); goto(r, "toStation", c_station(i), 2.5, True); rx_arm(r, "off"); fat_close(sig, r)
        elif ph == "toStation":
            r["cx"] = r["x0"] + (r["x1"] - r["x0"]) * smooth(r["pt"] / r["dur"])
            cue = "Out of fuel. Tow truck!" if r["fail"] else "Nice! Roll to the rest stop"
            if r["pt"] >= r["dur"]:
                if i == r["N"] - 1: r.update(ph="finish", pt=0.0); burst(r["cx"], ground(r["cx"]) - 90, 80, [AMBER, TEAL, RED, GREEN, (255, 255, 255)])
                else: r.update(ph="rest", pt=0.0, rr=0.0)
        elif ph == "rest":
            relaxed = eff <= .15
            r["rest_t"] += dt; r["rr"] += dt if relaxed else 0; r["rest_ok"] += dt if relaxed else 0
            r["fuel"] = min(1.0, r["rr"] / cfg["rest"]); r["heat"] = max(0.0, r["heat"] - .4 * dt)
            cue = "Refuelling. Keep relaxed" if relaxed else "Pump paused. Relax your muscle to refuel"
            if r["rr"] >= cfg["rest"]: goto(r, "toNext", c_start(i + 1), 3.0 + random.uniform(0, 1.5))
        elif ph == "toNext":
            r["cx"] = r["x0"] + (r["x1"] - r["x0"]) * smooth(r["pt"] / r["dur"]); cue = "Cruising to the next hill. Stay relaxed"
            if r["pt"] >= r["dur"]:
                r.update(i=i + 1, ph="climb", pt=0.0, prog=0.0, fuel=1.0, heat=0.0, stall=False)
                rx_arm(r, "on")
                fat_open(sig, r, i + 2, "climb")
        elif ph == "finish":
            cue = "All hills climbed! Amazing!"
            if r["pt"] > 3: return True
        if r["rx_pending"]:
            cue = "GO! Squeeze up to sweet spot" if r["rx_kind"] == "on" else "RELAX now! Let go completely"
        r["cue"] = cue
        return False

    def hud(r):
        lo, hi = r["bands"][min(r["i"], len(r["bands"]) - 1)]
        c = window((CX + 12, CY + 12, 236, 170), "Dashboard")
        if r.get("train"):
            text("Hold time", c.x + 10, c.y + 6, 13, BLK); text(f"{r['hl']:.1f} s", c.x + 10, c.y + 22, 26, (0, 0, 160), True)
            text("Engine heat", c.x + 10, c.y + 62, 13, BLK); progress((c.x + 10, c.y + 78, c.w - 20, 20), r["heat"], (192, 0, 0))
            text("Engine power", c.x + 10, c.y + 104, 13, BLK); progress((c.x + 10, c.y + 120, c.w - 20, 20), r["power"], (0, 128, 0))
        else:
            text("Fuel", c.x + 10, c.y + 6, 13, BLK); progress((c.x + 10, c.y + 22, c.w - 20, 20), max(0, r["fuel"]))
            text("Engine heat", c.x + 10, c.y + 48, 13, BLK); progress((c.x + 10, c.y + 64, c.w - 20, 20), r["heat"], (192, 0, 0))
            if r["ph"] == "rest":
                text("Refuel (stay relaxed)", c.x + 10, c.y + 90, 13, BLK); progress((c.x + 10, c.y + 106, c.w - 20, 20), r["rr"] / cfg["rest"], (0, 128, 0))
            else:
                text("Engine power", c.x + 10, c.y + 90, 13, BLK); progress((c.x + 10, c.y + 106, c.w - 20, 20), r["power"], (0, 128, 0))

        # Widen Effort box to 116px so tags stay strictly inside border
        c = window((CX + 12, CY + 194, 116, 340), "Effort")
        top, bot = c.y + 8, c.bottom - 30
        bevel((c.x + 6, top, 46, bot - top), False, FACE)
        nb = (bot - top - 8) // 10
        for k in range(nb):
            lvl = (k + 1) / nb; y = bot - 6 - (k + 1) * 10
            if lvl <= r["eff"] + .5 / nb:
                col = (192, 0, 0) if lvl > r["ceil"] else (230, 150, 0) if lvl > hi else (0, 160, 0) if lvl >= lo else (0, 0, 160)
                pygame.draw.rect(scr, col, (c.x + 9, y, 40, 8))
        def gy(v): return bot - 6 - v * (nb * 10)
        pygame.draw.lines(scr, (0, 128, 0), False, [(c.x + 55, gy(hi)), (c.x + 59, gy(hi)), (c.x + 59, gy(lo)), (c.x + 55, gy(lo))], 2)
        text("goal" if r.get("train") else "spot", c.x + 63, (gy(hi) + gy(lo)) / 2 - 6, 11, (0, 100, 0), True)
        pygame.draw.line(scr, (192, 0, 0), (c.x + 6, gy(r["ceil"])), (c.x + 59, gy(r["ceil"])), 2)
        text("max", c.x + 63, gy(r["ceil"]) - 6, 11, (192, 0, 0), True)
        text(f"{r['eff']*100:.0f}%", c.centerx, c.bottom - 22, 15, BLK, True, "c")

        fatigue_hud(sig, r, CX + W - 248, CY + 12)
        if r["rx_pending"]:
            go = r["rx_kind"] == "on"
            c = window((CX + W // 2 - 160, CY + 14, 320, 100), "GO!" if go else "RELAX")
            text("SQUEEZE NOW!" if go else "RELAX NOW!", c.centerx, c.centery - 2, 32, (0, 128, 0) if go else (192, 0, 0), True, "c")

    def draw_play(r, dt):
        nonlocal camy, wa, lastx
        cxw = r["cx"]; cam_x = cxw - CAR_X
        camy += ((ground(cxw) - 340) - camy) * min(1, dt * 3)
        scene(art, cam_x, camy, r["t"], 0 if r.get("train") else r["N"])
        for k in range(10):
            i = r["i"]
            if not r.get("train") and i < r["N"] and (i, k) not in r["got"]:
                wx = c_start(i) + (.1 + k * .085) * L
                x, y = wx - cam_x, ground(wx) - camy - 64 + 6 * math.sin(r["t"] * 4 + k)
                if -20 < x < W + 20:
                    pygame.draw.circle(scr, (200, 140, 20), (x, y), 13); pygame.draw.circle(scr, (255, 214, 70), (x, y), 11)
                    pygame.draw.circle(scr, (255, 240, 150), (x, y), 6, 2)
        wa += (cxw - lastx) / 20 + r["power"] * dt * 10; lastx = cxw
        if r["ph"] in ("climb", "toStation", "toNext", "ready") and random.random() < .5 + r["power"]:
            hot = r["heat"] > .5 or r["stall"]
            parts.append([cxw - 72, ground(cxw) - 40, -60 - 80 * r["power"], -40, 0, .9, (60, 60, 70) if hot else (235, 235, 245), 6 + int(r["power"] * 6) + 3 * hot, -40])
        if r["power"] > .3 and random.random() < .5:
            parts.append([cxw - 36, ground(cxw - 36) - 2, -90, -60, 0, .5, (190, 140, 90), 4, 200])
        for p in parts[:]:
            p[4] += dt; p[0] += p[2] * dt; p[1] += p[3] * dt; p[3] += p[8] * dt
            if p[4] > p[5]: parts.remove(p); continue
            s_ = pygame.Surface((16, 16), pygame.SRCALPHA)
            pygame.draw.circle(s_, (*p[6], int(230 * (1 - p[4] / p[5]))), (8, 8), min(8, p[7]))
            scr.blit(s_, (p[0] - cam_x - 8, p[1] - camy - 8))
        draw_car(art, cxw, camy, wa, r["power"], r["heat"], r["t"])
        for pp in pops[:]:
            pp[1] += dt
            if pp[1] > 1.4: pops.remove(pp); continue
            px = max(260, min(W - 240, pp[3] - cam_x))
            py = max(130, ground(pp[3]) - camy - 140 - pp[1] * 40)
            text(pp[0], px, py, 26, pp[2], True, "c", int(255 * (1 - pp[1] / 1.4)))

    def stats_for(r):
        if r.get("train"):
            zp = r["tin"] / r["tc"] if r["tc"] else 0
            avg = r["effsum"] / r["tc"] if r["tc"] else 0
            st = [("Mode", "Training: long incline"), ("Longest hold", f"{r['hl']:.1f} s"), ("Effort threshold", f"{r['thr']*100:.0f}%"),
                  ("Time above threshold", f"{zp*100:.0f}%"), ("Distance climbed", f"{max(0, r['cx'] - c_start(0)) / 10:.0f} m"),
                  ("Average effort", f"{avg*100:.0f}%"), ("Peak effort", f"{r['peak']*100:.0f}%"), ("Time above ceiling", f"{r['over']:.1f} s"),
                  ("Pain before to after", f"{pain1} to {pain2}")]
            return st, min(1.0, r["hl"] / 60)
        zp = r["tin"] / r["tc"] if r["tc"] else 0
        avg = r["effsum"] / r["tc"] if r["tc"] else 0
        rp = r["rest_ok"] / r["rest_t"] if r["rest_t"] else 1
        score = .5 * r["reps"] / r["N"] + .5 * zp
        st = [("Hills climbed", f"{r['reps']} / {r['N']}"), ("Time in sweet spot", f"{zp*100:.0f}%"), ("Average effort", f"{avg*100:.0f}%"),
              ("Peak effort", f"{r['peak']*100:.0f}%"), ("Time above ceiling", f"{r['over']:.1f} s"), ("Rest time relaxed", f"{rp*100:.0f}%"),
              ("Coins collected", str(r["coins"])), ("Pain before to after", f"{pain1} to {pain2}")]
        return st, score

    def do(a):
        nonlocal scene_name, run, res, modal, pixel, leave
        if a == "exit": leave = "menu"
        if a == "pixel": pixel = not pixel
        elif a in ("help", "about"): modal = a
        elif a == "end" and scene_name == "play" and run: fat_close(sig, run); res, run, scene_name = run, None, "pain2"
        elif a in ("new", "setup"):
            run = None; globals()["DS"] = [260] * 10; scene_name = "setup"
        elif a == "cal" and scene_name != "play": scene_name = "cal"

    while True:
        if leave: return leave
        dt = min(.05, clock.tick(60) / 1000)
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
            if k == pygame.K_ESCAPE and scene_name == "play":
                fat_close(sig, run); res, run, scene_name = run, None, "pain2"; continue
            if sig.demo and pygame.K_0 <= k <= pygame.K_9:
                sig.lvl = (k - pygame.K_0) / 10
            d = (k == pygame.K_RIGHT) - (k == pygame.K_LEFT)
            if scene_name == "setup":
                R = rows(); sel = min(sel, len(R) - 1)
                if k == pygame.K_DOWN: sel = (sel + 1) % len(R)
                if k == pygame.K_UP: sel = (sel - 1) % len(R)
                if d:
                    n = R[sel]
                    if n == "mode":
                        cfg["mode"] = "train" if cfg["mode"] == "rehab" else "rehab"; sel = 0
                    else:
                        a_, b_, s_ = LIMITS[n]; cfg[n] = max(a_, min(b_, cfg[n] + d * s_))
                        if cfg["hi"] < cfg["lo"] + 5:
                            if n == "lo": cfg["hi"] = cfg["lo"] + 5
                            else: cfg["lo"] = cfg["hi"] - 5
                        if cfg["ceil"] < cfg["thr"] + 5:
                            if n == "thr": cfg["ceil"] = cfg["thr"] + 5
                            else: cfg["thr"] = cfg["ceil"] - 5
                if k == pygame.K_RETURN: scene_name = "pain1"
            elif scene_name == "pain1":
                pain1 = max(0, min(10, pain1 + d))
                if k == pygame.K_RETURN: scene_name = "cal"
            elif scene_name == "cal":
                if k in (pygame.K_b, pygame.K_x) and rec is None:
                    rec = ["base" if k == pygame.K_b else "max", time.time(), []]
                if k == pygame.K_RETURN and calibrated():
                    run = start_run(); parts.clear(); pops.clear(); lastx = 40.0; scene_name = "play"
            elif scene_name == "pain2":
                pain2 = max(0, min(10, pain2 + d))
                if k == pygame.K_RETURN: summary = stats_for(res); scene_name = "sum"
            elif scene_name == "sum":
                if k == pygame.K_RETURN: pain1 = pain2 = 0; globals()["DS"] = [260] * 10; scene_name = "setup"
                if k == pygame.K_r: scene_name = "rx"
                if k == pygame.K_q: leave = "menu"
            elif scene_name == "rx":
                if k in (pygame.K_RETURN, pygame.K_ESCAPE, pygame.K_BACKSPACE): scene_name = "sum"
        if rec:
            rec[2].append(sig.sm)
            if time.time() - rec[1] >= CAL_S:
                xs = sorted(rec[2])
                if rec[0] == "base": base = sum(xs) / len(xs)
                else: mx = xs[int(len(xs) * .9)]
                sig.fatigue_cal(rec[0])
                rec = None
                calmsg = ("Calibrated. 0% = relaxed, 100% = your max. Press Enter." if calibrated() else
                          "Squeeze too close to relaxed. Record max (X) harder.") if (base is not None and mx is not None) else "Good. Now record the other value."
        if scene_name == "play" and run and not (modal or menu is not None) and step(run, dt):
            res, run, scene_name = run, None, "pain2"
        scroll += dt
        paused = bool(modal) or menu is not None
        MOUSE["block"] = False
        for c_ in list(MOUSE["clicks"]):
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
        scr = canvas
        globals()["TRAIN"] = bool(scene_name == "play" and run and run.get("train"))
        if scene_name == "play" and run:
            draw_play(run, 0 if paused else dt)
        else:
            cxm = scroll * 140
            scene(art, cxm, 62, scroll, 0)
            wa += dt * 6
            draw_car(art, cxm + CAR_X, 62, wa, .3, 0, scroll)
        scr = screen
        names = dict(setup="Session Setup", pain1="Pain Check", cal="Calibration", play="Hill Climb", pain2="Pain Check", sum="Results")
        chrome(f"ReGain Climb - [{names.get(scene_name, '')}]", menu)
        if pixel:
            small = pygame.transform.scale(canvas, (W // 3, H // 3)); screen.blit(pygame.transform.scale(small, (W, H)), (CX, CY))
        else:
            screen.blit(canvas, (CX, CY))
        dc = lambda w_, h_: pygame.Rect(CX + (W - w_) // 2, CY + (H - h_) // 2, w_, h_)
        hint = ""
        if scene_name == "play" and run:
            if run["warn"] or run["stall"]:
                w_ = art.warn.copy(); w_.set_alpha(int(150 + 90 * math.sin(time.time() * 10))); screen.blit(w_, (CX, CY))
            hud(run)
            if run.get("train"):
                status([(run["cue"], 400), (f"Hold {run['hl']:.1f} s", 120), (f"Threshold {run['thr']*100:.0f}%", 140), (f"Ceiling {run['ceil']*100:.0f}%", 130), ("Esc: end session", None)])
            else:
                status([(run["cue"], 380), (f"Hill {min(run['i'] + 1, run['N'])} / {run['N']}", 90), (f"Climbed {run['reps']}", 100),
                    (f"Coins {run['coins']}", 84), (f"Target {run['bands'][min(run['i'], run['N'] - 1)][0]*100:.0f}-{run['bands'][min(run['i'], run['N'] - 1)][1]*100:.0f}%", 130), ("Esc: end session", None)])
        elif scene_name == "setup":
            c = window(dc(700, 500), "Session Setup")
            text("The therapist sets the plan, the player drives. Drag a slider or use arrow keys.", c.x + 16, c.y + 12, 13, BLK)
            R = rows(); sel = min(sel, len(R) - 1)
            for i, n in enumerate(R):
                y = c.y + 44 + i * 40; lab = pygame.Rect(c.x + 10, y - 3, 240, 28)
                if clicked(lab): sel = i
                if i == sel: focus(lab)
                text(LABELS[n], c.x + 18, y + 3, 14, BLK)
                if n == "mode":
                    for j, (key, nm) in enumerate((("rehab", "Recovery"), ("train", "Training"))):
                        b_ = pygame.Rect(c.x + 262 + j * 130, y, 124, 24); on = cfg["mode"] == key
                        bevel(b_, on, YEL if on else FACE); text(nm, b_.centerx, b_.centery - 1, 14, BLK, on, "c")
                        if clicked(b_) and not on: cfg["mode"] = key; sel = 0
                    continue
                a_, b_, s_ = LIMITS[n]
                nv = trackbar((c.x + 262, y, 260, 24), cfg[n], a_, b_, s_, n)
                if nv != cfg[n]:
                    cfg[n] = nv; sel = i
                    if cfg["hi"] < cfg["lo"] + 5:
                        if n == "lo": cfg["hi"] = cfg["lo"] + 5
                        else: cfg["lo"] = cfg["hi"] - 5
                    if cfg["ceil"] < cfg["thr"] + 5:
                        if n == "thr": cfg["ceil"] = cfg["thr"] + 5
                        else: cfg["thr"] = cfg["ceil"] - 5
                bevel((c.x + 542, y, 100, 24), False, WHITE); text(f"{cfg[n]}{UNITS[n]}", c.x + 634, y + 4, 14, BLK, False, "r")
            text(f"Signal: {'DEMO (Space = squeeze, 0-9 = steady level)' if sig.demo else 'Arduino'}. Any single muscle.", c.x + 16, c.y + 44 + len(R) * 40 + 4, 13, (64, 64, 64))
            if button((c.right - 210, c.bottom - 40, 96, 28), "Next >", True, True): scene_name = "pain1"
            if button((c.right - 106, c.bottom - 40, 96, 28), "Exit"): do("exit")
            hint = "Up/Down: select    Left/Right: change    Enter: next"
        elif scene_name in ("pain1", "pain2"):
            c = window(dc(560, 350), "Pain Check")
            v = pain1 if scene_name == "pain1" else pain2
            text("How much does it hurt right now?" if scene_name == "pain1" else "And how does it feel after the session?", c.x + 20, c.y + 16, 15, BLK, True)
            text("0 = no pain, 10 = worst imaginable. Rate the target muscle.", c.x + 20, c.y + 40, 13, BLK)
            bevel((c.centerx - 60, c.y + 70, 120, 84), False, WHITE); text(v, c.centerx, c.y + 112, 64, lerp((0, 128, 0), (200, 0, 0), v / 10), True, "c")
            nv = trackbar((c.x + 60, c.y + 172, c.w - 120, 24), v, 0, 10, 1, "pain")
            if scene_name == "pain1": pain1 = nv
            else: pain2 = nv
            text("none", c.x + 60, c.y + 202, 12, BLK); text("worst", c.right - 60, c.y + 202, 12, BLK, False, "r")
            if scene_name == "pain1" and v >= 6:
                pygame.draw.polygon(scr, (255, 220, 0), [(c.x + 24, c.y + 246), (c.x + 40, c.y + 216), (c.x + 56, c.y + 246)]); pygame.draw.polygon(scr, BLK, [(c.x + 24, c.y + 246), (c.x + 40, c.y + 216), (c.x + 56, c.y + 246)], 2)
                text("!", c.x + 40, c.y + 233, 16, BLK, True, "c")
                text("Pain of 6 or more: check with your physiotherapist before", c.x + 68, c.y + 220, 13, BLK); text("loading this muscle.", c.x + 68, c.y + 237, 13, BLK)
            if button((c.right - 210, c.bottom - 40, 96, 28), "OK", True, True):
                if scene_name == "pain1": scene_name = "cal"
                else: summary = stats_for(res); scene_name = "sum"
            if scene_name == "pain1" and button((c.right - 106, c.bottom - 40, 96, 28), "Back"): scene_name = "setup"
            hint = "Left/Right: change    Enter: OK"
        elif scene_name == "cal":
            c = window(dc(680, 440), "Calibration")
            text("Every hill and sweet spot is scaled to your own relaxed and max signal.", c.x + 16, c.y + 12, 13, BLK)
            for idx, (lab, val, key, bl) in enumerate((("1. Relaxed", base, "base", "Record relaxed (B)"), ("2. Max squeeze", mx, "max", "Record max (X)"))):
                gx = c.x + 16 + idx * 318; groupbox((gx, c.y + 44, 304, 130), lab)
                text("Squeeze as hard as is comfortable" if idx else "Relax the muscle fully", gx + 14, c.y + 66, 13, BLK)
                bevel((gx + 14, c.y + 88, 110, 32), False, WHITE); text("-" if val is None else int(val), gx + 118, c.y + 92, 22, BLK, True, "r")
                if button((gx + 14, c.y + 130, 170, 28), bl, rec is None) and rec is None: rec = [key, time.time(), []]
            text("Live signal", c.x + 16, c.y + 190, 13, BLK); progress((c.x + 16, c.y + 208, c.w - 32, 22), min(1, sig.sm / 1000))
            if rec: text(f"Recording...  {max(0.0, CAL_S - (time.time() - rec[1])):.1f} s", c.x + 16, c.y + 242, 15, (0, 0, 160), True)
            else: text(calmsg, c.x + 16, c.y + 242, 14, BLK if calibrated() else (96, 96, 96))
            para(sig.fat_msg(), c.x + 16, c.y + 270, c.w - 32, 12, (0, 100, 0) if sig.fm_ready else (96, 96, 96))
            if button((c.right - 226, c.bottom - 40, 130, 28), "Start session >", calibrated(), True):
                run = start_run(); parts.clear(); pops.clear(); lastx = 40.0; scene_name = "play"
            if button((c.right - 90, c.bottom - 40, 80, 28), "Back"): scene_name = "setup"
            hint = "B: record relaxed    X: record max    Enter: start"
        elif scene_name == "sum" and summary:
            st, score = summary
            c = window(dc(800, 600), "Session Results")
            rows_ = list(st)
            if res:
                rxs = rx_stats(res)
                for k_, lab in ((("on", "Squeeze reaction"),) if res.get("train") else (("on", "Squeeze reaction"), ("off", "Relax reaction"))):
                    avg, best, n_, miss = rxs[k_]
                    rows_.append((lab + " (avg / best)", f"{avg:.2f} s / {best:.2f} s" if avg is not None else "not measured"))
                    rows_.append(("   measured / missed", f"{n_} / {miss}"))
                rows_ += fat_rows(res)

            lb_h = min(280, 24 + 18 * len(rows_) + 4)
            lb = pygame.Rect(c.x + 14, c.y + 8, c.w - 28, lb_h); bevel(lb, False, WHITE)
            bevel((lb.x + 2, lb.y + 2, lb.w - 4, 20), True); text("Measure", lb.x + 10, lb.y + 5, 13, BLK, True); text("Result", lb.x + 420, lb.y + 5, 13, BLK, True)
            row_step = min(18, (lb_h - 28) // max(1, len(rows_)))
            for i, (k_, v_) in enumerate(rows_):
                yy = lb.y + 24 + i * row_step
                if i % 2: pygame.draw.rect(scr, (236, 236, 250), (lb.x + 3, yy, lb.w - 6, row_step))
                text(k_, lb.x + 10, yy + 2, 13, BLK); text(v_, lb.x + 420, yy + 2, 13, BLK, True)
            n_st = 3 if score >= .8 else 2 if score >= .6 else 1 if score >= .35 else 0
            y2 = lb.bottom + 10
            if not (res and res.get("train")):
                text("Rating:", c.x + 16, y2 + 4, 14, BLK, True)
                for i in range(3):
                    star(c.x + 100 + i * 40, y2 + 12, 16, (255, 200, 0) if i < n_st else (150, 150, 150))
            yb = para("Reaction time = cue to threshold crossing (includes ~0.2 s smoothing). Saved to reflex_log.csv & fatigue_log.csv.", c.x + 16, y2 + (6 if res and res.get("train") else 32), c.w - 32, 12)
            if pain2 - pain1 >= 2: para("Pain rose by 2 or more. Consult physiotherapist before next session.", c.x + 16, yb + 4, c.w - 32, 13, (192, 0, 0), True)
            elif res and res["over"] > 2: para("Effort exceeded ceiling for a while. Aim for smoother, lighter effort.", c.x + 16, yb + 4, c.w - 32, 13, (192, 0, 0), True)
            if res and button((c.right - 348, c.bottom - 40, 112, 28), "Reaction >"): scene_name = "rx"
            if button((c.right - 226, c.bottom - 40, 130, 28), "New session", True, True): do("new"); pain1 = pain2 = 0
            if button((c.right - 90, c.bottom - 40, 80, 28), "Exit"): do("exit")
            hint = "Enter: new session    R: reaction report    Q: quit"
        elif scene_name == "rx" and res:
            c = window(dc(780, 540 if not res.get("train") else 400), "Reaction Report")
            draw_rx_report(c, res, SESSION_ID, sig.demo)
            if button((c.right - 126, c.bottom - 40, 112, 28), "< Results", True, True): scene_name = "sum"
            hint = "Enter / Esc: back to results"
        if scene_name != "play":
            status([(hint or "Ready", 560), ("Pixel mode ON" if pixel else "Pixel mode OFF", 130), (f"Signal: {'demo' if sig.demo else 'Arduino'}", None)])
        if menu is not None: draw_menu(menu, pixel)
        if modal:
            MOUSE["block"] = False
            if modal == "about":
                c = window(dc(450, 230), "About ReGain Climb")
                pygame.draw.rect(scr, (255, 90, 120), (c.x + 24, c.y + 36, 56, 26)); pygame.draw.rect(scr, (255, 220, 90), (c.x + 34, c.y + 22, 34, 14))
                pygame.draw.circle(scr, BLK, (c.x + 36, c.y + 66), 10); pygame.draw.circle(scr, BLK, (c.x + 70, c.y + 66), 10)
                text("ReGain Climb", c.x + 110, c.y + 24, 20, BLK, True)
                text("EMG biofeedback for rehab and isometric training.", c.x + 110, c.y + 54, 13, BLK)
                text("Not a medical device. Use it under your", c.x + 110, c.y + 78, 13, BLK); text("physiotherapist's plan.", c.x + 110, c.y + 94, 13, BLK)
            else:
                c = window(dc(660, 430), "How to Play")
                cur_y = c.y + 10
                for ln in HELP_LINES:
                    if not ln: cur_y += 6; continue
                    cur_y = para(ln, c.x + 16, cur_y, c.w - 32, 13, BLK)
            if button((c.centerx - 45, c.bottom - 40, 90, 28), "OK", True, True): modal = None
        pygame.display.flip()


if __name__ == "__main__":
    main()
    pygame.quit()
