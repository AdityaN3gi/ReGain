"""ReGain - fly a bird with your muscle. EMG biofeedback for rehab and isometric training.

Run:   python3 regain.py              (demo mode, no hardware)
       python3 regain.py /dev/cu.usbmodem1101   (real Arduino; Windows: COM3)
Needs: python3 -m pip install pygame pyserial

Arduino must print ONE number per line (the BioAmp envelope) at 115200 baud.
Not a medical device. Use under a physiotherapist's plan.

How the path works: after calibration, 0% = your relaxed level (bottom of the sky)
and 100% = your max squeeze (top). The gap in each pipe sits at the therapist's
target zone, so the path is always built from YOUR relaxed and max values.
Demo controls: hold SPACE to squeeze, or press 0-9 for a steady 0%-90% level.
"""
import sys, time, threading, random, os, math
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
BEST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "regain_best.txt")

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


def A(v):                                # activation (0-1) -> screen y
    return PB - (PB - PT) * v


def star(cx, cy, r, col):
    pts = [(cx + math.cos(-math.pi / 2 + i * math.pi / 5) * (r if i % 2 == 0 else r * .45),
            cy + math.sin(-math.pi / 2 + i * math.pi / 5) * (r if i % 2 == 0 else r * .45)) for i in range(10)]
    pygame.draw.polygon(scr, col, pts)


class Signal:
    """Raw EMG envelope from the Arduino (background thread) or simulated demo input."""
    def __init__(self, port):
        self.raw = self.sm = self.v = self.lvl = 0.0
        self.demo = port is None
        if not self.demo:
            if serial is None:
                sys.exit("Install pyserial first: python3 -m pip install pyserial")
            self.ser = serial.Serial(port, 115200, timeout=1)
            threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while True:
            try:
                self.raw = float(self.ser.readline().decode().strip())
            except (ValueError, UnicodeDecodeError):
                pass
            except Exception:
                break

    def update(self, dt, keys):
        if self.demo:
            tgt = 1.0 if keys[pygame.K_SPACE] else self.lvl
            self.v += (tgt - self.v) * min(1, dt * 6)
            self.raw = 80 + self.v * 700 + random.uniform(-.5, .5) * (25 + self.v * 50)
        self.sm += (self.raw - self.sm) * min(1, dt * 10)


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


def load_best():
    try:
        return float(open(BEST_FILE).read())
    except Exception:
        return 0.0


def main():
    global scr
    port = sys.argv[1] if len(sys.argv) > 1 else None
    sig = Signal(port)
    pygame.init()
    scr = pygame.display.set_mode((W, H))
    pygame.display.set_caption("ReGain")
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

    def act():
        if base is None or mx is None:
            return 0.0
        return max(0.0, min(1.0, (sig.sm - base) / (mx - base)))

    def calibrated():
        return base is not None and mx is not None and mx - base >= 80 and mx >= base * 1.4

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
                    cue="Get ready", warn=False, a=0.0, inz=False, lo=cfg["lo"] / 100, hi=cfg["hi"] / 100)

    def step(r, dt):
        a = act(); r["a"] = a; r["t"] += dt; r["acc"] += dt; r["peak"] = max(r["peak"], a)
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
                    if g["tin"] / g["tot"] >= .8:
                        r["reps"] += 1
                        burst(OX, by, 40, [AMBER, TEAL, (255, 255, 255), GREEN])
                        pops.append(["+1 rep", OX + 40, by - 40, 0.0])
                    else:
                        pops.append(["Missed. Try again", OX + 40, by - 40, 0.0])
        r["warn"] = a > r["ceil"]
        if r["warn"]:
            r["over"] += dt; cue = "Above your ceiling. Ease off now"
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
        text("ceiling", W - 14, yc - 24, 16, (200, 40, 40), True, "r")
        # bird
        prev_by, by = by, by + (A(r["a"]) - by) * min(1, dt * 16)
        tilt = max(-35, min(35, (prev_by - by) * 18))
        if r["warn"]:
            glow = pygame.Surface((120, 120), pygame.SRCALPHA)
            pygame.draw.circle(glow, (232, 60, 50, 110), (60, 60), 56)
            scr.blit(glow, (OX - 60, by - 60))
            w_ = art.warn.copy(); w_.set_alpha(int(160 + 90 * math.sin(tick * 10)))
            scr.blit(w_, (0, 0))
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
            text(pp[0], pp[1], pp[2], 26, (255, 255, 255), True, "l", int(255 * (1 - pp[3] / 1.3)))
        # left gauge
        panel((18, PT - 20, 46, PB - PT + 40), NAVY, 190, 23)
        pygame.draw.rect(scr, (30, 48, 72), (30, PT, 22, PB - PT), border_radius=11)
        top_y = A(r["a"]); pygame.draw.rect(scr, RED if r["warn"] else TEAL, (30, top_y, 22, PB - top_y), border_radius=11)
        if not r["train"]:
            zone = pygame.Surface((30, int(A(r["lo"]) - A(r["hi"]))), pygame.SRCALPHA); zone.fill((255, 255, 255, 90))
            scr.blit(zone, (26, A(r["hi"])))
        text("MAX", 41, PT - 38, 12, INK, True, "c"); text("RELAX", 41, PB + 26, 12, INK, True, "c")
        # HUD
        panel((78, 18, W - 96, 70), NAVY, 200, 22)
        zone_pct = r["tin"] / r["tot"] * 100 if r["tot"] else 0
        items = [("ACTIVATION", f"{r['a']*100:.0f}%"), ("IN ZONE", f"{zone_pct:.0f}%"),
                 ("HOLD" if r["train"] else "REPS", f"{r['holdT']:.1f} s" if r["train"] else f"{r['reps']} / {cfg['reps']}"),
                 ("TIME", f"{max(0, r['t'] - T0):.0f} s")]
        for i, (k_, v_) in enumerate(items):
            text(k_, 104 + i * 215, 28, 12, MUT, True); text(v_, 104 + i * 215, 46, 28, INK, True)
        text("ReGain", W - 40, 36, 26, TEAL, True, "r")
        # cue pill
        cc = RED if r["warn"] else (GREEN if r["inz"] else INK)
        wcue = font(26, True).size(r["cue"])[0] + 50
        panel((W // 2 - wcue // 2, H - GROUND + 12, wcue, 46), NAVY, 225, 23)
        text(r["cue"], W // 2, H - GROUND + 35, 26, cc, True, "c")
        text("Esc: end session", W - 16, H - 30, 15, (90, 70, 30), False, "r")

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

    # ------------------------------------------------------------ main loop
    while True:
        dt = min(.05, clock.tick(60) / 1000)
        tick = time.time()
        keys = pygame.key.get_pressed()
        sig.update(dt, keys)
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit(); return
            if e.type != pygame.KEYDOWN:
                continue
            k = e.key
            if k == pygame.K_ESCAPE and scene == "play":
                res, run, scene = run, None, "pain2"; continue
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
                if k == pygame.K_q: pygame.quit(); return

        if rec:
            rec[2].append(sig.sm)
            if time.time() - rec[1] >= 3:
                xs = sorted(rec[2])
                if rec[0] == "base": base = sum(xs) / len(xs)
                else: mx = xs[int(len(xs) * .9)]
                rec = None
                if base is not None and mx is not None:
                    calmsg = "Calibrated. 0% is relaxed, 100% is your max. Press Enter." if calibrated() else \
                        "Squeeze is too close to relaxed. Check electrodes and record max again (X)."
                else:
                    calmsg = "Good. Now record the other value."

        if scene == "play" and run:
            if step(run, dt):
                res, run, scene = run, None, "pain2"

        scroll += dt
        # -------------------------------------------------------- draw
        if scene == "play" and run:
            draw_play(run, dt, tick)
        else:
            backdrop(art, scroll * 1.0); draw_ground(art, scroll)
            panel((150, 36, W - 300, H - 72), NAVY, 222, 30)
            text("ReGain", W // 2, 84, 46, TEAL, True, "c")
            if scene == "setup":
                text("Session setup", W // 2, 130, 20, MUT, False, "c")
                y = 165
                R = rows()
                for i, n in enumerate(R):
                    on = i == sel
                    panel((190, y, W - 380, 56), (22, 42, 68) if on else (16, 32, 54), 255, 16, TEAL if on else None)
                    text(LABELS[n], 214, y + 15, 20, INK, on)
                    if n == "mode":
                        text("Recovery (therapist range)" if cfg["mode"] == "rehab" else "Training (longest hold)", W - 214, y + 15, 20, TEAL, True, "r")
                    else:
                        lo_, hi_, _ = LIMITS[n]
                        text(f"{cfg[n]}{UNITS[n]}", W - 214, y + 15, 20, TEAL, True, "r")
                        pygame.draw.rect(scr, (40, 62, 92), (430, y + 24, 210, 8), border_radius=4)
                        pygame.draw.rect(scr, TEAL, (430, y + 24, int(210 * (cfg[n] - lo_) / (hi_ - lo_)), 8), border_radius=4)
                    y += 64
                text(f"Source: {'DEMO. Space = squeeze, 0-9 = steady level' if sig.demo else 'Arduino on ' + port}", W // 2, 548, 16, MUT, False, "c")
                text("Up/Down select   Left/Right change   Enter continue", W // 2, 572, 16, MUT, False, "c")
            elif scene in ("pain1", "pain2"):
                v = pain1 if scene == "pain1" else pain2
                text("How much does it hurt right now?" if scene == "pain1" else "And how does it feel after the session?", W // 2, 150, 28, INK, True, "c")
                text("0 = no pain, 10 = worst imaginable. Rate the injured area.", W // 2, 192, 18, MUT, False, "c")
                text(v, W // 2, 300, 110, lerp(GREEN, RED, v / 10), True, "c")
                for i in range(11):
                    c = lerp(GREEN, RED, i / 10) if i <= v else (40, 62, 92)
                    pygame.draw.rect(scr, c, (216 + i * 52, 400, 42, 26), border_radius=8)
                if scene == "pain1" and v >= 6:
                    text("Pain of 6 or more: check with your physiotherapist before loading this muscle.", W // 2, 456, 17, AMBER, True, "c")
                text("Left/Right change   Enter confirm", W // 2, 560, 16, MUT, False, "c")
            elif scene == "cal":
                text("Calibrate to your muscle", W // 2, 140, 28, INK, True, "c")
                text("Your relaxed and max values set the whole path, so every pipe fits you.", W // 2, 180, 18, MUT, False, "c")
                for i, (lab, val, key) in enumerate((("1. Relax fully (3 s)", base, "B"), ("2. Squeeze to your max (3 s)", mx, "X"))):
                    x = 190 + i * 300
                    panel((x, 225, 280, 130), (16, 32, 54), 255, 18, TEAL if (rec and rec[0] == ("base", "max")[i]) else None)
                    text(lab, x + 18, 240, 18, INK, True)
                    text("-" if val is None else int(val), x + 18, 272, 44, TEAL, True)
                    text(f"Press {key}", x + 18, 326, 15, MUT)
                pygame.draw.rect(scr, (40, 62, 92), (190, 385, 580, 22), border_radius=11)
                pygame.draw.rect(scr, TEAL, (190, 385, int(580 * max(0, min(1, sig.sm / 1000))), 22), border_radius=11)
                text("live signal", 190, 412, 14, MUT)
                if rec:
                    text(f"Recording {rec[0]}... {3 - (time.time() - rec[1]):.1f} s", W // 2, 470, 22, TEAL, True, "c")
                else:
                    text(calmsg, W // 2, 470, 19, INK if calibrated() else MUT, False, "c")
                pygame.draw.rect(scr, (40, 62, 92), (810, 150, 24, PB - PT - 150 + 30), border_radius=12)
                if calibrated():
                    a_ = act(); yy = 150 + (PB - PT - 120) * (1 - a_)
                    scr.blit(art.birds[int(tick * 8) % 4], (822 - 34, yy - 34))
            elif scene == "sum" and summary:
                title, st, pct = summary
                text(title, W // 2, 130, 28, INK, True, "c")
                for i, (k_, v_) in enumerate(st):
                    x, y = 190 + (i % 3) * 215, 170 + (i // 3) * 92
                    panel((x, y, 200, 80), (16, 32, 54), 255, 16)
                    text(v_, x + 14, y + 10, 26, TEAL, True); text(k_, x + 14, y + 50, 14, MUT)
                if not res["train"]:
                    n_st = 3 if pct >= .8 else 2 if pct >= .6 else 1 if pct >= .4 else 0
                    for i in range(3):
                        star(W // 2 + (i - 1) * 52, 382, 20, AMBER if i < n_st else (50, 70, 100))
                cy0, ch = 430, 100
                panel((190, cy0 - 10, 620, ch + 20), (16, 32, 54), 255, 14)
                r = res
                def Y2(v): return cy0 + ch - v * ch
                if not r["train"]:
                    zr = pygame.Surface((620, int(Y2(r["lo"]) - Y2(r["hi"]))), pygame.SRCALPHA); zr.fill((47, 208, 184, 55))
                    scr.blit(zr, (190, Y2(r["hi"])))
                pygame.draw.line(scr, RED, (190, Y2(r["ceil"])), (810, Y2(r["ceil"])), 2)
                T = r["trace"]
                if len(T) > 1:
                    pygame.draw.lines(scr, TEAL, False, [(190 + i / (len(T) - 1) * 620, Y2(v)) for i, v in enumerate(T)], 3)
                if pain2 - pain1 >= 2:
                    text("Pain rose by 2 or more. Tell your physiotherapist before the next session.", W // 2, 548, 17, AMBER, True, "c")
                elif r["over"] > 2:
                    text("You went above your ceiling for a while. Aim for smoother, lighter holds.", W // 2, 548, 17, AMBER, True, "c")
                text("Enter: new session   Q: quit", W // 2, 575, 16, MUT, False, "c")
        pygame.display.flip()


if __name__ == "__main__":
    main()