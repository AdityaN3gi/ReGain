"""ReGain Climb - a hill-climb racing game driven by ONE muscle (EMG biofeedback for rehab).

Run:   python regain_climb.py demo            (no hardware, Space = squeeze, 0-9 = steady level)
       python regain_climb.py auto            (Arduino, finds the port itself)
       python regain_climb.py /dev/ttyACM1    (Arduino on a specific port; Windows: COM3)
Needs: pip install pygame pyserial numpy scipy, plus emg_core.py in the same folder.
Arduino runs emg_stream.ino (raw samples at 500 Hz). Close the Arduino Serial Monitor first.
Works with either hand or any single muscle. Run it again for the other side.

Built around common rehab principles (always under a physiotherapist's plan):
  * Controlled effort, not maximum effort: power is highest in the therapist's SWEET SPOT, and drops if you squeeze too little OR too much.
  * Prescribed hold time: each hill takes the set climb time at best, fuel for ~1.35x that.
  * Prescribed rest: the rest stop only refuels while the muscle is relaxed (<=15%), so rest is actually taken.
  * Gradual loading: the sweet spot rises by a set step on every hill (0 to turn off), and hills get visibly steeper.
  * Safety: a ceiling warning and engine overheating if effort goes too high, plus pain checks before and after.
Not a medical device.
"""
import sys, time, threading, random, math
import pygame
try:
    import serial
except ImportError:
    serial = None

W, H = 1000, 640
NAVY, INK, MUT = (10, 22, 40), (236, 244, 250), (150, 172, 194)
TEAL, AMBER, RED, GREEN = (47, 208, 184), (255, 184, 48), (232, 84, 74), (112, 198, 56)

scr = None
FONTS = {}


def font(n, b=False):
    if (n, b) not in FONTS:
        FONTS[(n, b)] = pygame.font.SysFont("avenir next,helveticaneue,segoe ui,helvetica,arial", n, bold=b)
    return FONTS[(n, b)]


def text(s, x, y, size=20, col=INK, bold=False, anchor="l", alpha=255):
    img = font(size, bold).render(str(s), True, col)
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


cfg = dict(lo=40, hi=60, hold=6, rest=5, reps=4, step=5, ceil=80)
LIMITS = dict(lo=(5, 90, 5), hi=(10, 100, 5), hold=(3, 15, 1), rest=(2, 15, 1), reps=(1, 8, 1), step=(0, 10, 5), ceil=(50, 100, 5))
LABELS = dict(lo="Sweet spot: low", hi="Sweet spot: high", hold="Climb time per hill", rest="Refuel rest per hill",
              reps="Hills to climb", step="Gradual load: added per hill", ceil="Ceiling warning")
UNITS = dict(lo="%", hi="%", hold=" s", rest=" s", reps="", step="%", ceil="%")
ROWS = list(LABELS)

BASEY, L, PRE, PLAT, DOWN = 470, 1100, 260, 560, 520
CYCLE = PRE + L + PLAT + DOWN
DS = [260] * 10
CAR_X = 300


class Signal:
    """Muscle signal.
    Hardware mode: uses emg_core.py, which reads the raw 500 Hz samples that emg_stream.ino sends,
    filters them and gives a smooth envelope. (The Arduino no longer needs to print the envelope itself.)
    Demo mode: keyboard only, no hardware."""
    GAIN = 5.0   # scales our envelope into the ~0-1000 range this game was written for.
                 # Raise it if calibration says "too close to relaxed" but your signal clearly moves.

    def __init__(self, port):
        self.raw = self.sm = self.v = self.lvl = 0.0
        self.demo = port in (None, "demo")
        if not self.demo:
            from emg_core import EMGSource
            self.src = EMGSource(None if port == "auto" else port)
            self.src.start()
            print("Connecting to the board... keep your arm still for a few seconds.")
            self.src.wait_ready()

    def update(self, dt, keys):
        if self.demo:
            tgt = 1.0 if keys[pygame.K_SPACE] else self.lvl
            self.v += (tgt - self.v) * min(1, dt * 6)
            self.raw = 80 + self.v * 700 + random.uniform(-.5, .5) * (25 + self.v * 50)
        else:
            self.raw = self.src.envelope * self.GAIN
        self.sm += (self.raw - self.sm) * min(1, dt * 10)


def smooth(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def hsh(k):
    k = (int(k) * 374761393 + 668265263) & 0xffffffff
    k = ((k ^ (k >> 13)) * 1274126177) & 0xffffffff
    return ((k ^ (k >> 16)) & 0xffff) / 65535


def ground(x):
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
    # smiling sun
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
    bx = (t * 22 + 200) % (W + 300) - 150; by = 150 + 18 * math.sin(t * .8)   # hot-air balloon
    pygame.draw.ellipse(scr, (255, 90, 110), (bx - 30, by - 44, 60, 70))
    for j, c in enumerate(((255, 220, 70), (80, 190, 255))):
        pygame.draw.ellipse(scr, c, (bx - 30 + 12 + j * 14 - 12, by - 44, 14, 70), 0)
    pygame.draw.line(scr, (90, 60, 40), (bx - 12, by + 22), (bx - 8, by + 38), 2); pygame.draw.line(scr, (90, 60, 40), (bx + 12, by + 22), (bx + 8, by + 38), 2)
    pygame.draw.rect(scr, (170, 110, 60), (bx - 12, by + 38, 24, 14), border_radius=3)
    for j in range(3):                                                          # birds
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
        x = c_start(i) - cx                                                      # start sign
        if -100 < x < W + 100:
            g = ground(c_start(i)) - cy
            pygame.draw.rect(scr, (130, 84, 46), (x - 4, g - 70, 8, 72)); pygame.draw.rect(scr, (255, 214, 90), (x - 38, g - 100, 76, 34), border_radius=8)
            text(f"HILL {i + 1}", x, g - 83, 18, (90, 50, 20), True, "c")
        x = c_top(i) - cx                                                        # checkered flag
        if -100 < x < W + 100:
            g = ground(c_top(i)) - cy
            pygame.draw.rect(scr, (240, 240, 240), (x - 3, g - 120, 6, 122))
            for a in range(4):
                for b in range(3):
                    pygame.draw.rect(scr, (20, 20, 30) if (a + b) % 2 else (255, 255, 255), (x + 3 + a * 12, g - 120 + b * 12, 12, 12))
        x = c_station(i) - cx                                                    # rest stop
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


def draw_car(art, cxw, cy, wa, power, hot, t):
    gb, gf, gc = ground(cxw - 40), ground(cxw + 40), ground(cxw)
    ang = math.degrees(math.atan2(gb - gf, 80)) + power * 3
    sc_y = (gb + gf) / 2 - cy - 52 - 2.5 * power * abs(math.sin(t * 15))
    img = pygame.transform.rotozoom(make_car(wa, hot), ang, 1.0)
    scr.blit(img, img.get_rect(center=(CAR_X, sc_y)))
    return sc_y


def main():
    global scr, DS
    port = sys.argv[1] if len(sys.argv) > 1 else "demo"
    sig = Signal(port)
    pygame.init()
    scr = pygame.display.set_mode((W, H))
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
    camy, wa, lastx, scroll = 62.0, 0.0, 0.0, 0.0

    def act():
        if base is None or mx is None:
            return 0.0
        return max(0.0, min(1.0, (sig.sm - base) / (mx - base)))

    def calibrated():
        return base is not None and mx is not None and mx - base >= 80 and mx >= base * 1.4

    def burst(x, y, n, cols, g=260):
        for _ in range(n):
            a_, sp = random.uniform(0, 6.28), random.uniform(80, 320)
            parts.append([x, y, math.cos(a_) * sp, math.sin(a_) * sp - 160, 0, random.uniform(.7, 1.3), random.choice(cols), random.randint(3, 6), g])

    def start_run():
        bands = []
        for i in range(cfg["reps"]):
            lo = min(cfg["lo"] + i * cfg["step"], 90); hi = min(100, max(cfg["hi"] + i * cfg["step"], lo + 5))
            bands.append((lo / 100, hi / 100))
        DS = [int(190 + (b[0] + b[1]) / 2 * 330) for b in bands] + [260] * 3
        globals()["DS"] = DS
        return dict(ph="ready", pt=0.0, t=0.0, i=0, prog=0.0, fuel=1.0, heat=0.0, stall=False, cx=40.0, x0=0.0, x1=0.0, dur=1.0, fail=False,
                    bands=bands, reps=0, coins=0, got=set(), tc=0.0, tin=0.0, effsum=0.0, peak=0.0, over=0.0, rr=0.0, rest_t=0.0, rest_ok=0.0,
                    eff=0.0, power=0.0, inz=False, warn=False, cue="Get ready", ceil=cfg["ceil"] / 100, N=cfg["reps"])

    def goto(r, ph, x1, dur, fail=False):
        r.update(ph=ph, pt=0.0, x0=r["cx"], x1=x1, dur=dur, fail=fail)

    def step(r, dt):
        eff = act(); r["eff"] = eff; r["t"] += dt; r["pt"] += dt
        i = r["i"]; lo, hi = r["bands"][i]
        r["peak"] = max(r["peak"], eff); r["warn"] = eff > r["ceil"]; r["power"] = 0.0; r["inz"] = False
        ph, cue = r["ph"], ""
        if r["warn"]:
            r["over"] += dt
        if ph == "ready":
            cue = "Get ready to climb!"; r["cx"] = 40 + (c_start(0) - 40) * smooth(r["pt"] / 3)
            if r["pt"] >= 3: r.update(ph="climb", pt=0.0)
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
                        r["coins"] += 1; pops.append(["+coin", 0.0, AMBER, r["cx"], 0])
            cue = ("Engine overheated! Relax a little" if r["stall"] else "Above your ceiling. Ease off now" if r["warn"] else
                   "More gas! Squeeze a bit more" if eff < lo else "Too much gas! Ease off a little" if eff > hi else "Perfect! Hold steady")
            if r["prog"] >= 1:
                r["reps"] += 1; burst(r["cx"], ground(r["cx"]) - 80, 60, [AMBER, TEAL, RED, GREEN, (255, 255, 255)])
                pops.append(["Hill conquered!", 0.0, GREEN, r["cx"], 0]); goto(r, "toStation", c_station(i), 1.5)
            elif r["fuel"] <= 0:
                pops.append(["Out of fuel! Try again", 0.0, AMBER, r["cx"], 0]); goto(r, "toStation", c_station(i), 2.5, True)
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
            if r["rr"] >= cfg["rest"]: goto(r, "toNext", c_start(i + 1), 3.0)
        elif ph == "toNext":
            r["cx"] = r["x0"] + (r["x1"] - r["x0"]) * smooth(r["pt"] / r["dur"]); cue = "Cruising to the next hill. Stay relaxed"
            if r["pt"] >= r["dur"]: r.update(i=i + 1, ph="climb", pt=0.0, prog=0.0, fuel=1.0, heat=0.0, stall=False)
        elif ph == "finish":
            cue = "All hills climbed! Amazing!"
            if r["pt"] > 3: return True
        r["cue"] = cue
        return False

    def hud(r):
        lo, hi = r["bands"][min(r["i"], len(r["bands"]) - 1)]
        panel((20, 14, W - 40, 74), NAVY, 205, 22)
        zp = r["tin"] / r["tc"] * 100 if r["tc"] else 0
        for k, (a, b) in enumerate((("HILL", f"{min(r['i'] + 1, r['N'])} / {r['N']}"), ("CLIMBED", str(r["reps"])), ("COINS", str(r["coins"])),
                                    ("IN SWEET SPOT", f"{zp:.0f}%"), ("SWEET SPOT", f"{lo*100:.0f}-{hi*100:.0f}%"))):
            text(a, 44 + k * 118, 24, 12, MUT, True); text(b, 44 + k * 118, 42, 26, INK, True)
        text("ReGain Climb", W - 40, 36, 26, TEAL, True, "r")
        # fuel + heat bars
        panel((20, 100, 250, 64), NAVY, 190, 18)
        text("FUEL", 36, 108, 12, MUT, True)
        pygame.draw.rect(scr, (40, 62, 92), (36, 126, 220, 14), border_radius=7)
        pygame.draw.rect(scr, lerp(RED, GREEN, r["fuel"] * 1.2), (36, 126, int(220 * max(0, r["fuel"])), 14), border_radius=7)
        text("ENGINE HEAT", 36, 142, 11, MUT, True)
        pygame.draw.rect(scr, (40, 62, 92), (130, 145, 126, 8), border_radius=4)
        pygame.draw.rect(scr, lerp(AMBER, RED, r["heat"]), (130, 145, int(126 * r["heat"]), 8), border_radius=4)
        # effort gauge
        gx, y0, y1 = 44, 520, 190
        panel((gx - 26, y1 - 24, 52, y0 - y1 + 70), NAVY, 190, 24)
        pygame.draw.rect(scr, (30, 48, 72), (gx - 11, y1, 22, y0 - y1), border_radius=11)
        fy = y0 - r["eff"] * (y0 - y1)
        pygame.draw.rect(scr, RED if r["warn"] else TEAL, (gx - 11, fy, 22, y0 - fy), border_radius=11)
        z = pygame.Surface((34, max(2, int((hi - lo) * (y0 - y1)))), pygame.SRCALPHA); z.fill((120, 255, 150, 120)); scr.blit(z, (gx - 17, y0 - hi * (y0 - y1)))
        cy_ = y0 - r["ceil"] * (y0 - y1); pygame.draw.line(scr, RED, (gx - 24, cy_), (gx + 24, cy_), 3)
        text("EFFORT", gx, y0 + 18, 12, INK, True, "c")
        cc = RED if (r["warn"] or r["stall"]) else (GREEN if r["inz"] else INK)
        wcue = font(26, True).size(r["cue"])[0] + 50
        panel((W // 2 - wcue // 2, H - 64, wcue, 46), NAVY, 225, 23)
        text(r["cue"], W // 2, H - 41, 26, cc, True, "c")
        if r["ph"] == "rest":
            pygame.draw.rect(scr, (30, 48, 72), (W // 2 - 150, H - 90, 300, 12), border_radius=6)
            pygame.draw.rect(scr, GREEN, (W // 2 - 150, H - 90, int(300 * r["rr"] / cfg["rest"]), 12), border_radius=6)

    def draw_play(r, dt):
        nonlocal camy, wa, lastx
        cxw = r["cx"]; cam_x = cxw - CAR_X
        camy += ((ground(cxw) - 340) - camy) * min(1, dt * 3)
        scene(art, cam_x, camy, r["t"], r["N"])
        for k in range(10):                                                    # coins
            i = r["i"]
            if i < r["N"] and (i, k) not in r["got"]:
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
            text(pp[0], pp[3] - cam_x, ground(pp[3]) - camy - 150 - pp[1] * 40, 30, pp[2], True, "c", int(255 * (1 - pp[1] / 1.4)))
        if r["warn"] or r["stall"]:
            w_ = art.warn.copy(); w_.set_alpha(int(150 + 90 * math.sin(time.time() * 10))); scr.blit(w_, (0, 0))
        hud(r)

    def stats_for(r):
        zp = r["tin"] / r["tc"] if r["tc"] else 0
        avg = r["effsum"] / r["tc"] if r["tc"] else 0
        rp = r["rest_ok"] / r["rest_t"] if r["rest_t"] else 1
        score = .5 * r["reps"] / r["N"] + .5 * zp
        st = [("Hills climbed", f"{r['reps']} / {r['N']}"), ("Time in sweet spot", f"{zp*100:.0f}%"), ("Average effort", f"{avg*100:.0f}%"),
              ("Peak effort", f"{r['peak']*100:.0f}%"), ("Time above ceiling", f"{r['over']:.1f} s"), ("Rest time relaxed", f"{rp*100:.0f}%"),
              ("Coins collected", str(r["coins"])), ("Pain before to after", f"{pain1} to {pain2}")]
        return st, score

    while True:
        dt = min(.05, clock.tick(60) / 1000)
        keys = pygame.key.get_pressed()
        sig.update(dt, keys)
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit(); return
            if e.type != pygame.KEYDOWN:
                continue
            k = e.key
            if k == pygame.K_ESCAPE and scene_name == "play":
                res, run, scene_name = run, None, "pain2"; continue
            if sig.demo and pygame.K_0 <= k <= pygame.K_9:
                sig.lvl = (k - pygame.K_0) / 10
            d = (k == pygame.K_RIGHT) - (k == pygame.K_LEFT)
            if scene_name == "setup":
                if k == pygame.K_DOWN: sel = (sel + 1) % len(ROWS)
                if k == pygame.K_UP: sel = (sel - 1) % len(ROWS)
                if d:
                    n = ROWS[sel]; a_, b_, s_ = LIMITS[n]; cfg[n] = max(a_, min(b_, cfg[n] + d * s_))
                    if cfg["hi"] < cfg["lo"] + 5:
                        if n == "lo": cfg["hi"] = cfg["lo"] + 5
                        else: cfg["lo"] = cfg["hi"] - 5
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
                if k == pygame.K_q: pygame.quit(); return
        if rec:
            rec[2].append(sig.sm)
            if time.time() - rec[1] >= 3:
                xs = sorted(rec[2])
                if rec[0] == "base": base = sum(xs) / len(xs)
                else: mx = xs[int(len(xs) * .9)]
                rec = None
                calmsg = ("Calibrated. 0% = relaxed, 100% = your max. Press Enter." if calibrated() else
                          "Squeeze is too close to relaxed. Check electrodes and record max again (X).") if (base is not None and mx is not None) else "Good. Now record the other value."
        if scene_name == "play" and run and step(run, dt):
            res, run, scene_name = run, None, "pain2"
        scroll += dt
        if scene_name == "play" and run:
            draw_play(run, dt)
        else:
            cxm = scroll * 140
            scene(art, cxm, 62, scroll, 0)
            wa += dt * 6
            draw_car(art, cxm + CAR_X, 62, wa, .3, 0, scroll)
            panel((150, 30, W - 300, H - 60), NAVY, 226, 30)
            text("ReGain", W // 2 - 82, 72, 46, TEAL, True, "c"); text("Climb", W // 2 + 76, 72 + 4 * math.sin(scroll * 4), 40, AMBER, True, "c")
            if scene_name == "setup":
                text("Session setup. The therapist sets the plan, the player drives.", W // 2, 122, 18, MUT, False, "c")
                y = 150
                for i, n in enumerate(ROWS):
                    on = i == sel
                    panel((190, y, W - 380, 48), (22, 42, 68) if on else (16, 32, 54), 255, 14, TEAL if on else None)
                    text(LABELS[n], 210, y + 12, 19, INK, on)
                    a_, b_, _ = LIMITS[n]
                    text(f"{cfg[n]}{UNITS[n]}", W - 210, y + 12, 19, TEAL, True, "r")
                    pygame.draw.rect(scr, (40, 62, 92), (500, y + 21, 170, 7), border_radius=4)
                    pygame.draw.rect(scr, TEAL, (500, y + 21, int(170 * (cfg[n] - a_) / (b_ - a_)), 7), border_radius=4)
                    y += 54
                text(f"Source: {'DEMO. Space = squeeze, 0-9 = steady level' if sig.demo else port}.  Works with either hand or any single muscle.", W // 2, 538, 15, MUT, False, "c")
                text("Up/Down select   Left/Right change   Enter continue", W // 2, 564, 15, MUT, False, "c")
            elif scene_name in ("pain1", "pain2"):
                v = pain1 if scene_name == "pain1" else pain2
                text("How much does it hurt right now?" if scene_name == "pain1" else "And how does it feel after the session?", W // 2, 150, 28, INK, True, "c")
                text("0 = no pain, 10 = worst imaginable. Rate the injured area.", W // 2, 192, 18, MUT, False, "c")
                text(v, W // 2, 300, 110, lerp(GREEN, RED, v / 10), True, "c")
                for i in range(11):
                    pygame.draw.rect(scr, lerp(GREEN, RED, i / 10) if i <= v else (40, 62, 92), (216 + i * 52, 400, 42, 26), border_radius=8)
                if scene_name == "pain1" and v >= 6:
                    text("Pain of 6 or more: check with your physiotherapist before loading this muscle.", W // 2, 456, 17, AMBER, True, "c")
                text("Left/Right change   Enter confirm", W // 2, 560, 16, MUT, False, "c")
            elif scene_name == "cal":
                text("Calibrate to your muscle", W // 2, 140, 28, INK, True, "c")
                text("Every hill and sweet spot is scaled to your own relaxed and max.", W // 2, 178, 18, MUT, False, "c")
                for i, (lab, val, key) in enumerate((("B  Relax fully (3 s)", base, "base"), ("X  Squeeze to your max (3 s)", mx, "max"))):
                    x = 190 + i * 300
                    panel((x, 215, 280, 120), (16, 32, 54), 255, 18, TEAL if (rec and rec[0] == key) else None)
                    text(lab, x + 18, 230, 18, INK, True); text("-" if val is None else int(val), x + 18, 266, 44, TEAL, True)
                pygame.draw.rect(scr, (40, 62, 92), (190, 370, 580, 22), border_radius=11)
                pygame.draw.rect(scr, TEAL, (190, 370, int(580 * max(0, min(1, sig.sm / 1000))), 22), border_radius=11)
                text("live signal", 190, 398, 14, MUT)
                if rec: text(f"Recording... {3 - (time.time() - rec[1]):.1f} s", W // 2, 460, 22, TEAL, True, "c")
                else: text(calmsg, W // 2, 460, 18, INK if calibrated() else MUT, False, "c")
            elif scene_name == "sum" and summary:
                st, score = summary
                text("Session summary", W // 2, 122, 28, INK, True, "c")
                for i, (k_, v_) in enumerate(st):
                    x, y = 190 + (i % 4) * 160, 160 + (i // 4) * 92
                    panel((x, y, 150, 80), (16, 32, 54), 255, 16)
                    text(v_, x + 12, y + 10, 22, TEAL, True); text(k_, x + 12, y + 50, 12, MUT)
                n_st = 3 if score >= .8 else 2 if score >= .6 else 1 if score >= .35 else 0
                for i in range(3):
                    star(W // 2 + (i - 1) * 60, 400, 26, AMBER if i < n_st else (50, 70, 100))
                text("Great work. Share the results with your physiotherapist.", W // 2, 455, 18, MUT, False, "c")
                if pain2 - pain1 >= 2:
                    text("Pain rose by 2 or more. Tell your physiotherapist before the next session.", W // 2, 505, 17, AMBER, True, "c")
                elif res and res["over"] > 2:
                    text("You went above your ceiling for a while. Aim for smoother, lighter effort.", W // 2, 505, 17, AMBER, True, "c")
                text("Enter: new session   Q: quit", W // 2, 560, 15, MUT, False, "c")
        pygame.display.flip()


if __name__ == "__main__":
    main()
