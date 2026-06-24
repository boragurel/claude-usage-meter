"""claude-sprite-monitor.py -- animated sprite driven by activity.json.
Loads a random character from ccstats sprite_art on each launch.
"""

import tkinter as tk
import json
import math
import time
import os
import random
from datetime import date, timedelta

# ---- paths ----
import sqlite3
from claude_usage_meter.paths import ACTIVITY_FILE, USAGE_FILE, LEDGER_DB
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
TARGET_PX = 120    # target sprite width in pixels
WIN_W = 300
WIN_H = 220

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
DOT_BASE_Y_OFFSET = 34  # px below sprite bottom (clears shadow + label)
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
        self.sprite_x = (WIN_W - self.sprite_w) // 2 - 30
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



        self._drag_x = 0
        self._drag_y = 0
        self._boot_time = time.time()
        self._session_pct = None
        self._weekly_pct = None

        # page navigation
        self._page = 0
        self._page_names = ["SPRITE", "CALENDAR", "RHYTHM", "RHYTHM MATRIX", "USAGE LIMITS"]
        self._num_pages = len(self._page_names)
        self._stats_cache = {}
        self._stats_age = 0.0
        self._held_state = "INACTIVE"
        self._display_state = "INACTIVE"

        # nav arrow hit regions (set during draw)
        self._nav_left = (6, WIN_H - 18, 20, 14)
        self._nav_right = (WIN_W - 26, WIN_H - 18, 20, 14)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<Button-3>", lambda e: self.root.destroy())


        self._poll_data()
        self._animate()

    def _in_rect(self, ex, ey, rect):
        rx, ry, rw, rh = rect
        return rx <= ex <= rx + rw and ry <= ey <= ry + rh

    def _on_click(self, e):
        if self._in_rect(e.x, e.y, self._nav_left):
            self._page = (self._page - 1) % self._num_pages
            self._stats_age = 0.0  # force refresh
            return
        if self._in_rect(e.x, e.y, self._nav_right):
            self._page = (self._page + 1) % self._num_pages
            self._stats_age = 0.0
            return
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
        # keep last non-INACTIVE palette so the display stays coloured
        if self.state != "INACTIVE":
            self._held_state = self.state
        self._display_state = self._held_state if self.state == "INACTIVE" else self.state
        pal = PALETTES.get(self._display_state, PALETTES["INACTIVE"])

        # background (shared across all pages)
        self.canvas.create_rectangle(0, 0, WIN_W, WIN_H, fill=pal["bg"],
                                     outline=pal["bg"])

        if self._page == 0:
            self._draw_sprite_page(pal)
        elif self._page == 1:
            self._draw_calendar_page()
        elif self._page == 2:
            self._draw_rhythm_page()
        elif self._page == 3:
            self._draw_matrix_page()
        elif self._page == 4:
            self._draw_usage_page()

        self.root.after(33, self._animate)  # ~30 fps

    def _draw_sprite_page(self, pal):
        now_ms = (time.time() - self._boot_time) * 1000

        # chrome
        label = self.sprite.get("label", "?")
        self._draw_chrome(label, self.state)

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
                dot_base = self.sprite_y + self.sprite_h + DOT_BASE_Y_OFFSET
                dy = dot_base - round(lift)
                self.canvas.create_rectangle(
                    dx, dy, dx + DOT_SIZE, dy + DOT_SIZE,
                    fill=pal["label"], outline="")

    def _draw_chrome(self, title, right_label=""):
        """Common page frame: title top-left, label top-right, nav arrows bottom."""
        pal = PALETTES.get(self._display_state, PALETTES["INACTIVE"])
        # title
        self.canvas.create_text(8, 6, text=title, fill="#cccccc",
                                font=("Consolas", 10, "bold"), anchor="nw")
        # right label
        if right_label:
            self.canvas.create_text(WIN_W - 8, 6, text=right_label,
                                    fill=pal["label"],
                                    font=("Consolas", 8, "bold"), anchor="ne")
        # nav arrows
        arrow_y = WIN_H - 14
        # left arrow
        self.canvas.create_polygon(
            10, arrow_y, 20, arrow_y - 6, 20, arrow_y + 6,
            fill="#666666", outline="")
        # right arrow
        rx = WIN_W - 10
        self.canvas.create_polygon(
            rx, arrow_y, rx - 10, arrow_y - 6, rx - 10, arrow_y + 6,
            fill="#666666", outline="")
        # page dots
        dot_y = WIN_H - 14
        total_w = self._num_pages * 6 + (self._num_pages - 1) * 4
        dot_x = (WIN_W - total_w) // 2
        for i in range(self._num_pages):
            col = "#cccccc" if i == self._page else "#444444"
            self.canvas.create_oval(dot_x, dot_y - 3, dot_x + 6, dot_y + 3,
                                    fill=col, outline="")
            dot_x += 10

    def _query_stats(self):
        """Query DB for stats pages. Cached, refreshed every 60s."""
        now = time.time()
        if self._stats_cache and (now - self._stats_age) < 60:
            return self._stats_cache
        try:
            conn = sqlite3.connect(LEDGER_DB)
            # calendar: peak session_pct per day, last 35 days
            cal = conn.execute(
                "SELECT date(ts, 'localtime') AS d, MAX(session_pct) "
                "FROM usage_readings GROUP BY d ORDER BY d DESC LIMIT 35"
            ).fetchall()
            # rhythm matrix: avg session_pct by weekday x hour
            matrix = conn.execute(
                "SELECT cast(strftime('%w', ts, 'localtime') AS INTEGER) AS dow, "
                "cast(strftime('%H', ts, 'localtime') AS INTEGER) AS hour, "
                "COUNT(*) "
                "FROM state_transitions WHERE state = 'COMPOSING' "
                "GROUP BY dow, hour"
            ).fetchall()
            # usage limits: last 48h of session_pct readings
            usage = conn.execute(
                "SELECT ts, session_pct, weekly_all_pct FROM usage_readings "
                "WHERE ts >= datetime('now', '-48 hours') ORDER BY ts"
            ).fetchall()
            conn.close()
            self._stats_cache = {"calendar": cal, "matrix": matrix, "usage": usage}
            self._stats_age = now
        except Exception:
            pass
        return self._stats_cache

    def _draw_calendar_page(self):
        pal = PALETTES.get(self._display_state, PALETTES["INACTIVE"])
        self._draw_chrome("CALENDAR", "SESSION %")
        data = self._query_stats().get("calendar", [])
        if not data:
            self.canvas.create_text(WIN_W // 2, WIN_H // 2, text="NO DATA",
                                    fill="#666666", font=("Consolas", 10),
                                    anchor="center")
            return

        # build date lookup: "YYYY-MM-DD" -> peak pct
        by_date = {}
        for row in data:
            by_date[row[0]] = row[1]

        # grid: 5 weeks, Mon-Sun, today in the bottom row
        today = date.today()
        dow = today.weekday()  # 0=Mon
        bottom_mon = today - timedelta(days=dow)
        grid_start = bottom_mon - timedelta(weeks=4)

        # layout
        COLS, ROWS = 7, 5
        GRID_X, GRID_W = 10, WIN_W - 20
        CELL_W = (GRID_W - (COLS - 1) * 2) // COLS
        CELL_H = 24
        RGAP = 2
        HDR_Y = 26
        GRID_Y = 40
        DAY_HDRS = ["M", "T", "W", "T", "F", "S", "S"]

        # section label
        self.canvas.create_text(GRID_X, HDR_Y - 2, text="DAILY PEAK",
                                fill="#888888", font=("Consolas", 7),
                                anchor="nw")

        # day-of-week headers
        for c in range(COLS):
            cx = GRID_X + c * (CELL_W + 2) + CELL_W // 2
            self.canvas.create_text(cx, HDR_Y + 8, text=DAY_HDRS[c],
                                    fill="#888888", font=("Consolas", 7),
                                    anchor="center")

        # heat colours: 6 levels from dim to palette body
        body = pal["body"]
        br = int(body[1:3], 16)
        bg_ = int(body[3:5], 16)
        bb = int(body[5:7], 16)
        heat = ["#1a1a1a"]  # level 0: no data
        for lv in range(1, 6):
            f = 0.2 + 0.8 * (lv / 5)
            r = int(br * f)
            g = int(bg_ * f)
            b = int(bb * f)
            heat.append(f"#{r:02x}{g:02x}{b:02x}")

        # draw grid
        for row in range(ROWS):
            for col in range(COLS):
                dt = grid_start + timedelta(days=row * 7 + col)
                key = dt.isoformat()
                cx = GRID_X + col * (CELL_W + 2)
                cy = GRID_Y + row * (CELL_H + RGAP)

                pct = by_date.get(key)
                if dt > today:
                    self.canvas.create_rectangle(
                        cx, cy, cx + CELL_W, cy + CELL_H,
                        fill="", outline="#333333")
                elif pct is None:
                    self.canvas.create_rectangle(
                        cx, cy, cx + CELL_W, cy + CELL_H,
                        fill=heat[0], outline="#333333")
                else:
                    lv = 0 if pct == 0 else min(5, max(1, int(pct / 20) + 1))
                    self.canvas.create_rectangle(
                        cx, cy, cx + CELL_W, cy + CELL_H,
                        fill=heat[lv], outline="")

                # day-of-month number
                if dt <= today:
                    dark_cell = pct is not None and pct >= 60
                    num_col = pal["bg"] if dark_cell else "#aaaaaa"
                    self.canvas.create_text(
                        cx + CELL_W - 3, cy + CELL_H - 3,
                        text=str(dt.day), fill=num_col,
                        font=("Consolas", 7), anchor="se")

                # today outline
                if dt == today:
                    self.canvas.create_rectangle(
                        cx, cy, cx + CELL_W, cy + CELL_H,
                        fill="", outline="#cccccc")

        # legend
        ly = GRID_Y + ROWS * (CELL_H + RGAP) + 8
        lx = GRID_X
        self.canvas.create_text(lx, ly, text="LESS", fill="#888888",
                                font=("Consolas", 7), anchor="nw")
        lx += 30
        for k in range(1, 6):
            self.canvas.create_rectangle(lx, ly, lx + 10, ly + 8,
                                         fill=heat[k], outline="#333333")
            lx += 13
        self.canvas.create_text(lx + 2, ly, text="MORE", fill="#888888",
                                font=("Consolas", 7), anchor="nw")

    def _draw_rhythm_page(self):
        pal = PALETTES.get(self._display_state, PALETTES["INACTIVE"])
        self._draw_chrome("RHYTHM", "PROMPTS")
        data = self._query_stats().get("matrix", [])
        if not data:
            self.canvas.create_text(WIN_W // 2, WIN_H // 2, text="NO DATA",
                                    fill="#666666", font=("Consolas", 10),
                                    anchor="center")
            return

        # aggregate from matrix data: hourly totals and weekday totals
        hours = [0] * 24
        days = [0] * 7
        for dow, hour, count in data:
            row = (dow - 1) % 7
            hours[hour] += count
            days[row] += count

        body = pal["body"]
        accent = pal["shade"]
        BAR_X = 10
        BAR_W = WIN_W - 20

        # --- BY HOUR: 24 bars ---
        self.canvas.create_text(BAR_X, 24, text="BY HOUR \u2022 0-23",
                                fill="#888888", font=("Consolas", 7), anchor="nw")
        h_top = 36
        h_height = 70
        h_mx = max(hours) or 1
        h_bar_w = (BAR_W - 23) // 24  # 23 gaps of 1px
        for i in range(24):
            bx = BAR_X + i * (h_bar_w + 1)
            v = hours[i]
            fill_h = round(h_height * v / h_mx) if v > 0 else 0
            # track
            self.canvas.create_rectangle(
                bx, h_top, bx + h_bar_w, h_top + h_height,
                fill="#1a1a1a", outline="")
            # bar
            if fill_h > 0:
                peak = (v == h_mx)
                col = pal["hi"] if peak else body
                self.canvas.create_rectangle(
                    bx, h_top + h_height - fill_h,
                    bx + h_bar_w, h_top + h_height,
                    fill=col, outline="")
        # hour axis
        ax_y = h_top + h_height + 2
        for h in [0, 6, 12, 18, 23]:
            hx = BAR_X + h * (h_bar_w + 1) + h_bar_w // 2
            self.canvas.create_text(hx, ax_y, text=str(h), fill="#888888",
                                    font=("Consolas", 7), anchor="n")

        # --- BY WEEKDAY: 7 bars ---
        d_top_label = ax_y + 14
        self.canvas.create_text(BAR_X, d_top_label, text="BY WEEKDAY",
                                fill="#888888", font=("Consolas", 7), anchor="nw")
        d_top = d_top_label + 12
        d_height = 50
        d_mx = max(days) or 1
        d_bar_w = (BAR_W - 6 * 6) // 7  # 6 gaps of 6px
        DAY_LABELS = ["M", "T", "W", "T", "F", "S", "S"]
        for i in range(7):
            bx = BAR_X + i * (d_bar_w + 6)
            v = days[i]
            fill_h = round(d_height * v / d_mx) if v > 0 else 0
            # track
            self.canvas.create_rectangle(
                bx, d_top, bx + d_bar_w, d_top + d_height,
                fill="#1a1a1a", outline="")
            # bar
            if fill_h > 0:
                peak = (v == d_mx)
                col = pal["hi"] if peak else body
                self.canvas.create_rectangle(
                    bx, d_top + d_height - fill_h,
                    bx + d_bar_w, d_top + d_height,
                    fill=col, outline="")
            # day label
            self.canvas.create_text(bx + d_bar_w // 2, d_top + d_height + 3,
                                    text=DAY_LABELS[i], fill="#888888",
                                    font=("Consolas", 7), anchor="n")

    def _draw_matrix_page(self):
        pal = PALETTES.get(self._display_state, PALETTES["INACTIVE"])
        self._draw_chrome("RHYTHM MATRIX", "PROMPTS")
        data = self._query_stats().get("matrix", [])
        if not data:
            self.canvas.create_text(WIN_W // 2, WIN_H // 2, text="NO DATA",
                                    fill="#666666", font=("Consolas", 10),
                                    anchor="center")
            return

        # build 7x24 grid: rows=Mon-Sun, cols=0-23h
        # strftime %w: 0=Sun, 1=Mon ... 6=Sat -> remap to Mon=0 ... Sun=6
        grid = [[0] * 24 for _ in range(7)]
        for dow, hour, count in data:
            row = (dow - 1) % 7  # Mon=0 ... Sun=6
            grid[row][hour] = count

        mx = max(max(row) for row in grid) or 1

        # layout
        LABEL_X = 8
        GRID_X = 24
        GRID_W = WIN_W - GRID_X - 8
        CELL_W = (GRID_W - 23) // 24  # 23 gaps of 1px
        CELL_H = 16
        RGAP = 1
        GRID_Y = 38
        DAY_LABELS = ["M", "T", "W", "T", "F", "S", "S"]

        # section label
        self.canvas.create_text(LABEL_X, 24, text="WEEKDAY \u00d7 HOUR",
                                fill="#888888", font=("Consolas", 7),
                                anchor="nw")

        # heat colours from palette
        body = pal["body"]
        br = int(body[1:3], 16)
        bg_ = int(body[3:5], 16)
        bb = int(body[5:7], 16)
        heat = ["#1a1a1a"]
        for lv in range(1, 6):
            f = 0.2 + 0.8 * (lv / 5)
            heat.append(f"#{int(br*f):02x}{int(bg_*f):02x}{int(bb*f):02x}")

        # find busiest cell
        best_r, best_c, best_v = 0, 0, 0
        for r in range(7):
            for c in range(24):
                if grid[r][c] > best_v:
                    best_r, best_c, best_v = r, c, grid[r][c]

        # draw grid
        for r in range(7):
            cy = GRID_Y + r * (CELL_H + RGAP)
            # day label
            self.canvas.create_text(LABEL_X, cy + CELL_H // 2,
                                    text=DAY_LABELS[r], fill="#888888",
                                    font=("Consolas", 7), anchor="w")
            for c in range(24):
                cx = GRID_X + c * (CELL_W + 1)
                v = grid[r][c]
                if v == 0:
                    lv = 0
                else:
                    lv = min(5, max(1, int(v / mx * 5 + 0.5)))
                self.canvas.create_rectangle(
                    cx, cy, cx + CELL_W, cy + CELL_H,
                    fill=heat[lv], outline="")

        # hour axis below grid
        ax_y = GRID_Y + 7 * (CELL_H + RGAP) + 2
        for h in [0, 6, 12, 18, 23]:
            hx = GRID_X + h * (CELL_W + 1) + CELL_W // 2
            self.canvas.create_text(hx, ax_y, text=str(h), fill="#888888",
                                    font=("Consolas", 7), anchor="n")

        # busiest slot label
        if best_v > 0:
            RDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
            h2 = (best_c + 1) % 24
            busy = f"{RDAYS[best_r]} {best_c:02d}-{h2:02d}"
            self.canvas.create_text(LABEL_X, ax_y + 12,
                                    text=f"BUSIEST \u2022 {busy}",
                                    fill="#888888", font=("Consolas", 7),
                                    anchor="nw")

    def _draw_usage_page(self):
        pal = PALETTES.get(self._display_state, PALETTES["INACTIVE"])
        self._draw_chrome("USAGE LIMITS", "48H")
        data = self._query_stats().get("usage", [])
        if not data:
            self.canvas.create_text(WIN_W // 2, WIN_H // 2, text="NO DATA",
                                    fill="#666666", font=("Consolas", 10),
                                    anchor="center")
            return

        # bucket readings into hourly slots, max session_pct per slot
        from datetime import datetime as dt_cls
        now = dt_cls.now().astimezone()
        buckets = [None] * 48
        for ts_str, sess, weekly in data:
            try:
                t = dt_cls.fromisoformat(ts_str)
                age_h = (now - t).total_seconds() / 3600
                idx = 47 - int(age_h)  # 0=oldest, 47=current hour
                if 0 <= idx < 48:
                    if buckets[idx] is None or sess > buckets[idx]:
                        buckets[idx] = sess
            except Exception:
                continue

        # also bucket weekly_all for a second row
        w_buckets = [None] * 48
        for ts_str, sess, weekly in data:
            try:
                t = dt_cls.fromisoformat(ts_str)
                age_h = (now - t).total_seconds() / 3600
                idx = 47 - int(age_h)
                if 0 <= idx < 48:
                    if w_buckets[idx] is None or weekly > w_buckets[idx]:
                        w_buckets[idx] = weekly
            except Exception:
                continue

        BAR_X = 10
        BAR_W = WIN_W - 20
        bar_w = (BAR_W - 47) // 48  # 47 gaps of 1px

        # --- SESSION chart ---
        self.canvas.create_text(BAR_X, 24, text="SESSION PEAK",
                                fill="#888888", font=("Consolas", 7), anchor="nw")
        s_top = 36
        s_height = 60
        for i in range(48):
            bx = BAR_X + i * (bar_w + 1)
            pct = buckets[i]
            # track
            self.canvas.create_rectangle(
                bx, s_top, bx + bar_w, s_top + s_height,
                fill="#1a1a1a", outline="")
            if pct is not None and pct > 0:
                fill_h = round(s_height * min(pct, 100) / 100)
                if pct >= 85:
                    col = "#ef4444"
                elif pct >= 60:
                    col = "#f59e0b"
                else:
                    col = pal["body"]
                self.canvas.create_rectangle(
                    bx, s_top + s_height - fill_h,
                    bx + bar_w, s_top + s_height,
                    fill=col, outline="")

        # --- WEEKLY chart ---
        w_label_y = s_top + s_height + 6
        self.canvas.create_text(BAR_X, w_label_y, text="WEEKLY PEAK",
                                fill="#888888", font=("Consolas", 7), anchor="nw")
        w_top = w_label_y + 12
        w_height = 50
        for i in range(48):
            bx = BAR_X + i * (bar_w + 1)
            pct = w_buckets[i]
            self.canvas.create_rectangle(
                bx, w_top, bx + bar_w, w_top + w_height,
                fill="#1a1a1a", outline="")
            if pct is not None and pct > 0:
                fill_h = round(w_height * min(pct, 100) / 100)
                if pct >= 85:
                    col = "#ef4444"
                elif pct >= 60:
                    col = "#f59e0b"
                else:
                    col = pal["body"]
                self.canvas.create_rectangle(
                    bx, w_top + w_height - fill_h,
                    bx + bar_w, w_top + w_height,
                    fill=col, outline="")

        # time axis
        ax_y = w_top + w_height + 3
        self.canvas.create_text(BAR_X, ax_y, text="48H AGO",
                                fill="#888888", font=("Consolas", 7), anchor="nw")
        mid_x = BAR_X + 23 * (bar_w + 1) + bar_w // 2
        self.canvas.create_text(mid_x, ax_y, text="24H",
                                fill="#888888", font=("Consolas", 7), anchor="n")
        self.canvas.create_text(BAR_X + 47 * (bar_w + 1) + bar_w, ax_y,
                                text="NOW", fill="#888888",
                                font=("Consolas", 7), anchor="ne")

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
        bar_w = 18
        bar_h = 110
        bar_top = 28
        bar_x1 = WIN_W - 68       # session bar
        bar_x2 = WIN_W - 36       # weekly bar
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
