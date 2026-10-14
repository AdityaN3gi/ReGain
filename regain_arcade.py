"""ReGain Arcade - one retro window (Pokemon / Nintendo-DS style), pick a game.

Run:   python3 regain_arcade.py demo          (no hardware: Space = squeeze, 0-9 = steady level)
       python3 regain_arcade.py auto          (Arduino, port auto-detected)
       python3 regain_arcade.py /dev/ttyACM0  (Arduino, port given)

Keep these files in ONE folder:  regain_arcade.py  regain_flappy_retro.py  regain_climb_retro.py  emg_core.py  fatigue_monitor.py
"""
import sys, os, time, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pygame
import regain_climb_retro as C
import regain_flappy_retro as F
from regain_climb_retro import (text, wrap, bevel, button, window, progress, focus, clicked, chrome, status, draw_menu,
                                menu_bar_rects, menu_item_at, MOUSE, BLK, WHITE, FACE, SW, SH, CX, CY, W, H)

import inspect
for _mod in (C, F):
    if "sig" not in inspect.signature(_mod.main).parameters:
        sys.exit("\nOut-of-date game file: " + os.path.abspath(_mod.__file__) +
                 "\nIt does not support the arcade yet. Download the latest " + os.path.basename(_mod.__file__) +
                 " again, replace that file, and keep all files in one folder.\n")

for _mod in (C, F):
    if not getattr(_mod, "FATIGUE_READY", False):
        sys.exit("\nOut-of-date game file: " + os.path.abspath(_mod.__file__) +
                 "\nIt does not have fatigue tracking yet. Download the latest " + os.path.basename(_mod.__file__) +
                 " and replace it. Keep regain_arcade.py, both game files, emg_core.py and fatigue_monitor.py in one folder.\n")
if not C.FAT_OK:
    print("Note: fatigue tracking is OFF. It needs fatigue_monitor.py in this folder, plus numpy and scipy (pip install numpy scipy).")

GAME_MENUS = C.MENUS
GAME_MENUS[0][1][-1] = ("Back to Arcade", "", "exit")
ARCADE_MENUS = [("File", [("Play Selected", "Enter", "play"), None, ("Exit", "Alt+F4", "exit")]),
                ("Options", [("Pixel Mode", "", "pixel")]),
                ("Help", [("About...", "", "about")])]
GAMES = [("ReGain Flap", F, ("Fly a bird through pipes.", "Hold your muscle inside each gap.")),
         ("ReGain Climb", C, ("Drive a car up hills.", "Hit sweet spot, relax to refuel."))]


def make_icon(kind):
    s = pygame.Surface((32, 32), pygame.SRCALPHA)
    d = (60, 36, 20)
    if kind == 0:
        pygame.draw.polygon(s, d, [(3, 14), (0, 10), (1, 19)]); pygame.draw.polygon(s, (255, 150, 40), [(4, 15), (2, 12), (3, 18)])
        pygame.draw.circle(s, d, (15, 17), 13); pygame.draw.circle(s, (255, 206, 50), (15, 17), 11)
        pygame.draw.ellipse(s, (255, 236, 168), (9, 19, 13, 7)); pygame.draw.ellipse(s, d, (5, 15, 11, 8)); pygame.draw.ellipse(s, (255, 150, 40), (6, 16, 9, 6))
        pygame.draw.circle(s, d, (21, 12), 5); pygame.draw.circle(s, WHITE, (21, 12), 4); pygame.draw.circle(s, BLK, (22, 12), 2)
        pygame.draw.polygon(s, d, [(25, 15), (32, 17), (25, 21)]); pygame.draw.polygon(s, (255, 120, 40), [(26, 16), (30, 17), (26, 20)])
    else:
        pygame.draw.polygon(s, (110, 200, 70), [(0, 31), (0, 24), (14, 16), (31, 8), (31, 31)]); pygame.draw.polygon(s, (60, 140, 40), [(0, 24), (14, 16), (31, 8)], 1)
        pygame.draw.polygon(s, (240, 60, 150), [(5, 16), (7, 11), (14, 8), (21, 11), (22, 16)]); pygame.draw.polygon(s, (150, 228, 255), [(8, 15), (9, 12), (14, 10), (18, 12), (19, 15)])
        pygame.draw.rect(s, (240, 60, 150), (2, 15, 22, 7)); pygame.draw.rect(s, d, (2, 15, 22, 7), 1); pygame.draw.rect(s, (255, 230, 80), (4, 18, 18, 1))
        for x in (7, 19):
            pygame.draw.circle(s, BLK, (x, 23), 4); pygame.draw.circle(s, (90, 215, 200), (x, 23), 2)
    return pygame.transform.scale(s, (96, 96))


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "demo"
    sig = C.Signal(port)
    pygame.init()
    screen = pygame.display.set_mode((SW, SH))
    canvas = pygame.Surface((W, H))
    pygame.display.set_caption("ReGain Arcade")
    clock = pygame.time.Clock()
    art = C.Art()
    icons = [make_icon(0), make_icon(1)]
    C.MENUS = ARCADE_MENUS
    sel, last_click, scroll = 0, (None, 0.0), 0.0
    modal, menu, pixel = None, None, True

    def launch(i):
        nonlocal modal, menu
        C.MENUS = GAME_MENUS
        r = GAMES[i][1].main(sig=sig, embedded=True)
        C.MENUS = ARCADE_MENUS
        pygame.display.set_caption("ReGain Arcade")
        MOUSE.update(down=False, grab=None, clicks=[], press=None, block=False)
        modal = menu = None
        if r == "quit":
            pygame.quit(); sys.exit()

    def do(a):
        nonlocal modal, pixel
        if a == "exit": pygame.quit(); sys.exit()
        if a == "pixel": pixel = not pixel
        elif a == "about": modal = "about"
        elif a == "play": launch(sel)

    while True:
        dt = min(.05, clock.tick(60) / 1000)
        sig.update(dt, pygame.key.get_pressed())
        MOUSE["pos"] = pygame.mouse.get_pos(); MOUSE["clicks"] = []; MOUSE["press"] = None
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit(); return
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                MOUSE["down"] = True; MOUSE["press"] = e.pos
            if e.type == pygame.MOUSEBUTTONUP and e.button == 1:
                MOUSE["down"] = False; MOUSE["grab"] = None; MOUSE["clicks"].append(e.pos)
            if e.type != pygame.KEYDOWN:
                continue
            if modal:
                if e.key in (pygame.K_RETURN, pygame.K_ESCAPE, pygame.K_SPACE): modal = None
                continue
            if e.key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN):
                sel = (sel + (1 if e.key in (pygame.K_RIGHT, pygame.K_DOWN) else -1)) % len(GAMES)
            elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER): do("play")
            elif e.key == pygame.K_ESCAPE: do("exit")

        scroll += dt
        MOUSE["block"] = False
        for c_ in list(MOUSE["clicks"]):
            hit = next((mi for mi, rr in enumerate(menu_bar_rects()) if rr.collidepoint(c_)), None)
            if hit is not None:
                menu = None if menu == hit else hit; MOUSE["clicks"] = []; break
            if menu is not None:
                it = menu_item_at(menu, c_); menu = None
                MOUSE["clicks"] = []
                if it: do(it)
                break
        if menu is not None:
            for mi, rr in enumerate(menu_bar_rects()):
                if rr.collidepoint(MOUSE["pos"]): menu = mi
        MOUSE["block"] = bool(modal) or menu is not None

        C.scr = canvas
        cxm = scroll * 140
        C.scene(art, cxm, 62, scroll, 0)
        C.draw_car(art, cxm + C.CAR_X, 62, scroll * 6, .3, 0, scroll)
        C.scr = screen
        chrome("ReGain Arcade - [Choose a game]", menu)
        if pixel:
            small = pygame.transform.scale(canvas, (W // 3, H // 3)); screen.blit(pygame.transform.scale(small, (W, H)), (CX, CY))
        else:
            screen.blit(canvas, (CX, CY))

        c = window(pygame.Rect(CX + (W - 640) // 2, CY + (H - 440) // 2, 640, 440), "ReGain Arcade")
        text("Which game will you play today?  (Play, or double-click)", c.x + 18, c.y + 14, 14, BLK, True)
        text("Each game has its own calibration. The muscle signal stays connected.", c.x + 18, c.y + 34, 13, (64, 64, 64))
        for i, (name, mod, desc) in enumerate(GAMES):
            cell = pygame.Rect(c.x + 20 + i * 300, c.y + 66, 280, 220)
            bevel(cell, False, WHITE)
            if clicked(cell):
                now = time.time()
                if last_click[0] == i and now - last_click[1] < .45: sel = i; do("play")
                sel = i; last_click = (i, now)
            icon = icons[i]
            scr_icon = icon.get_rect(center=(cell.centerx, cell.y + 70))
            screen.blit(icon, scr_icon)
            lab = pygame.Rect(0, 0, C.font(15, True).size(name)[0] + 12, 22); lab.center = (cell.centerx, cell.y + 138)
            if i == sel:
                pygame.draw.rect(screen, (216, 64, 64), lab, border_radius=6); focus(lab.inflate(2, 2))
            text(name, lab.centerx, lab.centery - 1, 15, WHITE if i == sel else BLK, True, "c")
            for j, ln in enumerate(desc):
                text(ln, cell.centerx, cell.y + 166 + j * 18, 13, (64, 64, 64), False, "c")
        text("Live signal", c.x + 20, c.y + 300, 13, BLK)
        progress((c.x + 20, c.y + 318, 360, 20), min(1, sig.sm / 1000))
        text("DEMO: Space = squeeze" if sig.demo else "Arduino connected", c.x + 394, c.y + 321, 13, (64, 64, 64))
        fat_text = "Fatigue tracking: " + ("READY" if sig.fm_ready else "off (needs fatigue_monitor.py, numpy, scipy)" if not C.FAT_OK
                                           else "switches on when you calibrate inside a game")
        wrap(fat_text, c.x + 20, c.y + 346, c.w - 40, 12, (64, 64, 64))
        if button((c.right - 214, c.bottom - 44, 96, 28), "Play", True, True): do("play")
        if button((c.right - 110, c.bottom - 44, 96, 28), "Exit"): do("exit")
        status([("Arrow keys: choose    Enter: play    Esc: exit", 560), ("Pixel mode ON" if pixel else "Pixel mode OFF", 130), (f"Signal: {'demo' if sig.demo else 'Arduino'}", None)])
        if menu is not None: draw_menu(menu, pixel)
        if modal:
            MOUSE["block"] = False
            w = window(pygame.Rect(CX + (W - 450) // 2, CY + (H - 240) // 2, 450, 240), "About ReGain Arcade")
            text("ReGain Arcade", w.x + 24, w.y + 24, 20, BLK, True)
            text("EMG biofeedback games for rehab and isometric training.", w.x + 24, w.y + 58, 13, BLK)
            text("Not a medical device. Use it under your", w.x + 24, w.y + 84, 13, BLK); text("physiotherapist's plan.", w.x + 24, w.y + 100, 13, BLK)
            if button((w.centerx - 45, w.bottom - 40, 90, 28), "OK", True, True): modal = None
        pygame.display.flip()


if __name__ == "__main__":
    main()
