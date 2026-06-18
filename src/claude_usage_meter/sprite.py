"""claude-sprite-monitor.py -- animated sprite driven by activity.json.
Loads a random character from ccstats sprite_art on each launch.
"""

import tkinter as tk
import json
import math
import time
import os
import random

# ---- paths ----
from claude_usage_meter.paths import ACTIVITY_FILE, USAGE_FILE
SPRITE_ART_DIR = os.path.join(os.path.dirname(__file__), "sprites")

# ---- load sprites from ccstats source art ----
def load_sprites():
    """Load all sprite definitions from ccstats/tools/sprite_art/."""
    sprites = {}
    if not os.path.isdir(SPRITE_ART_DIR):
        return sprites
    for fname in os.listdir(SPRITE_ART_DIR):
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(SPRITE_ART_DIR, fname)
        ns = {}
        with open(fpath, "r", encoding="utf-8") as f:
            exec(f.read(), ns)
        if "SPRITE" in ns:
            s = ns["SPRITE"]
            sprites[s["name"]] = s
    return sprites

def make_blink(eyes_white):
    """Generate blink slits: horizontal 1-cell bars at the vertical midpoint
    of each eye-white rect."""
    slits = []
    for x, y, w, h in eyes_white:
        mid_y = y + h // 2
        slits.append((x, mid_y, w, 1))
    return tuple(slits)

# Fallback GLOOM if sprite_art dir is missing
FALLBACK = {
    "name": "gloom", "label": "GLOOM", "grid_cells": 16,
    "layers": {
        "fill": ((5,2,5,1),(4,3,7,1),(3,4,9,1),(2,5,11,1),(2,6,11,1),
                 (2,7,11,1),(2,8,11,1),(2,9,11,1),(2,10,11,1),(2,11,11,1),
                 (2,12,2,2),(5,12,2,2),(8,12,2,2),(11,12,2,2)),
        "shade": ((12,5,1,7),(11,12,2,2)),
        "hi": ((4,3,2,1),(3,4,1,2)),
        "eyes_white": ((4,5,2,3),(9,5,2,3)),
        "eyes_pupil": ((5,6,1,2),(9,6,1,2)),
    },
}

# ---- layout ----
TARGET_PX = 96     # target sprite width in pixels
WIN_W = 210
WIN_H = 200

# ---- per-state visuals ----
PALETTES = {
    "INACTIVE":   {"bg":"#1a1a1a","body":"#555555","shade":"#3a3a3a",
                   "hi":"#777777","eye_w":"#999999","eye_p":"#1a1a1a","label":"#666666"},
    "IDLE":       {"bg":"#292929","body":"#ff6422","shade":"#c24c1a",
                   "hi":"#ff8f5c","eye_w":"#d3d3d3","eye_p":"#292929","label":"#ff6422"},
    "COMPOSING":  {"bg":"#1a2a2a","body":"#2dd4bf","shade":"#22a090",
                   "hi":"#5eeadb","eye_w":"#d3d3d3","eye_p":"#1a2a2a","label":"#2dd4bf"},
    "THINKING":   {"bg":"#251a30","body":"#a78bfa","shade":"#7c5fc0",
                   "hi":"#c4b0fc","eye_w":"#d3d3d3","eye_p":"#251a30","label":"#a78bfa"},
    "TOOL_USE":   {"bg":"#2a2010","body":"#f59e0b","shade":"#b87708",
                   "hi":"#fbbf40","eye_w":"#d3d3d3","eye_p":"#2a2010","label":"#f59e0b"},
    "WEB_SEARCH": {"bg":"#1a2030","body":"#3b82f6","shade":"#2c62ba",
                   "hi":"#6ba3f8","eye_w":"#d3d3d3","eye_p":"#1a2030","label":"#3b82f6"},
    "STREAMING":  {"bg":"#1a2a1a","body":"#22c55e","shade":"#1a9447",
                   "hi":"#4fdb80","eye_w":"#d3d3d3","eye_p":"#1a2a1a","label":"#22c55e"},
}

# Layer name -> palette key
LAYER_PEN = {
    "fill": "body", "shade": "shade", "hi": "hi", "dark": "shade",
    "mouth": "eye_p", "eyes_white": "eye_w", "eyes_pupil": "eye_p",
}

def bar_track_colour(bg_hex):
    """Slightly lighter than bg, for bar track backgrounds."""
    r = min(255, int(int(bg_hex[1:3], 16) * 1.6))
    g = min(255, int(int(bg_hex[3:5], 16) * 1.6))
    b = min(255, int(int(bg_hex[5:7], 16) * 1.6))
    return f"#{r:02x}{g:02x}{b:02x}"

# Bob period in ms: 0 = static, lower = faster
BOB_MS = {
    "INACTIVE": 0, "IDLE": 3000, "COMPOSING": 2000,
    "THINKING": 1500, "TOOL_USE": 950, "WEB_SEARCH": 950, "STREAMING": 950,
}
BOB_AMP = 4  # pixels

# Blink: 4.2s cycle, eyes shut for 200ms near the end
BLINK_CYCLE_MS = 4200
BLINK_SHUT_MS = 200

# Thinking dots: three bouncing squares, staggered wave
DOT_CYCLE_MS = 660
DOT_STAGGER_MS = 120
DOT_SIZE = 5           # px
DOT_GAP = 7            # px between dot centres
DOT_BASE_Y = 128       # px from top of window
DOT_LIFT = 5           # px bounce height
DOT_STATES = {"THINKING", "TOOL_USE", "WEB_SEARCH"}

# ---- app ----
class SpriteMonitor:
    def __init__(self):
        self.state = "INACTIVE"

        # load sprites and pick one
        all_sprites = load_sprites()
        if all_sprites:
            self.sprite = random.choice(list(all_sprites.values()))
        else:
            self.sprite = FALLBACK
        self.scale = TARGET_PX // self.sprite["grid_cells"]
        grid = self.sprite["grid_cells"]
        self.sprite_w = grid * self.scale
        self.sprite_h = grid * self.scale  # grid is square
        self.sprite_x = (WIN_W - self.sprite_w) // 2 - 22
        self.sprite_y = 30

        # pre-compute blink slits from eyes_white if present
        ew = self.sprite["layers"].get("eyes_white", ())
        self.blink_slits = make_blink(ew) if ew else ()
        self.has_eyes = bool(ew)

        # draw order (skip eyes, we handle those specially)
        self.body_layers = []
        for name in ("fill", "shade", "hi", "dark", "mouth"):
            rects = self.sprite["layers"].get(name, ())
            if rects:
                self.body_layers.append((name, rects))

        self.root = tk.Tk()
        self.root.title(self.sprite.get("label", "?"))
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.geometry(f"{WIN_W}x{WIN_H}+100+100")

        self.canvas = tk.Canvas(self.root, width=WIN_W, height=WIN_H,
                                highlightthickness=0, bd=0)
        self.canvas.pack()

        self.canvas.bind("<Button-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<Button-3>", lambda e: self.root.destroy())

        self._drag_x = 0
        self._drag_y = 0
        self._boot_time = time.time()
        self._session_pct = None
        self._weekly_pct = None

        self._poll_data()
        self._animate()

    def _start_drag(self, e):
        self._drag_x = e.x
        self._drag_y = e.y

    def _on_drag(self, e):
        x = self.root.winfo_x() + e.x - self._drag_x
        y = self.root.winfo_y() + e.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    def _poll_data(self):
        try:
            with open(ACTIVITY_FILE, "r") as f:
                data = json.load(f)
            new_state = data.get("state", "INACTIVE")
            if new_state in PALETTES:
                self.state = new_state
        except (FileNotFoundError, json.JSONDecodeError, PermissionError):
            pass
        # usage.json (less frequent, tolerates missing file)
        try:
            with open(USAGE_FILE, "r") as f:
                udata = json.load(f)
            if udata.get("status") == "ok":
                s = udata.get("session", {})
                w = udata.get("weekly_all", {})
                self._session_pct = s.get("pct")
                self._weekly_pct = w.get("pct")
        except (FileNotFoundError, json.JSONDecodeError, PermissionError):
            pass
        self.root.after(500, self._poll_data)

    def _animate(self):
        self.canvas.delete("all")
        pal = PALETTES.get(self.state, PALETTES["INACTIVE"])
        now_ms = (time.time() - self._boot_time) * 1000

        # background
        self.canvas.create_rectangle(0, 0, WIN_W, WIN_H, fill=pal["bg"],
                                     outline=pal["bg"])

        # bob offset
        period = BOB_MS.get(self.state, 0)
        if period > 0:
            phase = (now_ms % period) / period
            bob_y = round(BOB_AMP * math.sin(2 * math.pi * phase))
        else:
            bob_y = 0

        ox = self.sprite_x
        oy = self.sprite_y + bob_y

        # draw body layers
        for name, rects in self.body_layers:
            pen = LAYER_PEN.get(name, "body")
            self._draw_layer(rects, ox, oy, pal[pen])

        # eyes: blink check (skip when INACTIVE or eyeless)
        if self.has_eyes:
            blink_phase = now_ms % BLINK_CYCLE_MS
            eyes_shut = (self.state != "INACTIVE"
                         and blink_phase > BLINK_CYCLE_MS - BLINK_SHUT_MS)
            if eyes_shut:
                self._draw_layer(self.blink_slits, ox, oy, pal["eye_w"])
            else:
                self._draw_layer(self.sprite["layers"]["eyes_white"],
                                 ox, oy, pal["eye_w"])
                self._draw_layer(self.sprite["layers"]["eyes_pupil"],
                                 ox, oy, pal["eye_p"])

        # shadow (centred on sprite, not window)
        sprite_cx = self.sprite_x + self.sprite_w // 2
        shadow_y = self.sprite_y + self.sprite_h + 6
        sw = self.sprite_w - 20
        self.canvas.create_oval(
            sprite_cx - sw//2, shadow_y, sprite_cx + sw//2, shadow_y + 6,
            fill=pal["shade"], outline="")

        # state label
        label_y = shadow_y + 16
        self.canvas.create_text(sprite_cx, label_y, text=self.state,
                                fill=pal["label"], font=("Consolas", 10, "bold"),
                                anchor="center")

        # usage bars
        self._draw_bars(pal)

        # thinking dots
        if self.state in DOT_STATES:
            for i in range(3):
                dot_phase = ((now_ms - i * DOT_STAGGER_MS) % DOT_CYCLE_MS) / DOT_CYCLE_MS
                lift = DOT_LIFT * math.sin(math.pi * dot_phase)
                dx = sprite_cx + (i - 1) * (DOT_SIZE + DOT_GAP) - DOT_SIZE // 2
                dy = DOT_BASE_Y - round(lift)
                self.canvas.create_rectangle(
                    dx, dy, dx + DOT_SIZE, dy + DOT_SIZE,
                    fill=pal["label"], outline="")

        self.root.after(33, self._animate)  # ~30 fps

    def _draw_layer(self, rects, ox, oy, colour):
        for r in rects:
            x, y, w, h = r
            x1 = ox + x * self.scale
            y1 = oy + y * self.scale
            x2 = x1 + w * self.scale
            y2 = y1 + h * self.scale
            self.canvas.create_rectangle(x1, y1, x2, y2,
                                         fill=colour, outline="")

    def _draw_bars(self, pal):
        """Draw session and weekly usage bars on the right side."""
        bar_w = 14
        bar_h = 80
        bar_top = 32
        bar_x1 = WIN_W - 58       # session bar
        bar_x2 = WIN_W - 32       # weekly bar
        gap = 8

        for bx, label, pct in ((bar_x1, "S", self._session_pct),
                                (bar_x2, "W", self._weekly_pct)):
            # header label
            cx = bx + bar_w // 2
            self.canvas.create_text(cx, bar_top - 6, text=label,
                                    fill="#666666", font=("Consolas", 7, "bold"),
                                    anchor="center")

            # track (empty bar background, muted)
            track = bar_track_colour(pal["bg"])
            self.canvas.create_rectangle(bx, bar_top, bx + bar_w,
                                         bar_top + bar_h,
                                         fill=track, outline="")

            if pct is not None:
                # bar colour by threshold
                if pct >= 85:
                    bar_col = "#ef4444"
                elif pct >= 60:
                    bar_col = "#f59e0b"
                else:
                    bar_col = pal["body"]

                # filled portion (from bottom)
                fill_h = round(bar_h * min(pct, 100) / 100)
                if fill_h > 0:
                    self.canvas.create_rectangle(
                        bx, bar_top + bar_h - fill_h,
                        bx + bar_w, bar_top + bar_h,
                        fill=bar_col, outline="")

                # percentage number
                self.canvas.create_text(cx, bar_top + bar_h + 10,
                                        text=str(round(pct)),
                                        fill=bar_col,
                                        font=("Consolas", 7, "bold"),
                                        anchor="center")
            else:
                self.canvas.create_text(cx, bar_top + bar_h + 10,
                                        text="-",
                                        fill="#666666",
                                        font=("Consolas", 7, "bold"),
                                        anchor="center")

    def run(self):
        self.root.mainloop()

def main():
    SpriteMonitor().run()

if __name__ == "__main__":
    main()
