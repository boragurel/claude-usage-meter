#!/usr/bin/env python3
"""
claude-usage-widget.py

Desktop widget showing Claude activity state and plan usage.
Reads activity.json and usage.json (written by the two producers).
Background colour reflects activity state; usage bars show session and weekly
limits; a status line at the bottom spells out the state. Never contacts
Claude or Anthropic; reads local files only.

Run: pyw -3.13 claude-usage-widget.py  (silent, no console)
  or py -3.13 claude-usage-widget.py   (with console for debugging)
Drag to reposition. Right-click to close.
"""

import json, os, sys
from datetime import datetime, timezone
import tkinter as tk

# ---- config -------------------------------------------------------------
from claude_usage_meter.paths import USAGE_FILE, ACTIVITY_FILE
USAGE_MS        = 20_000   # re-read usage every 20s
ACTIVITY_MS     = 500      # re-read activity every 500ms
STALE_USAGE_S   = 480      # usage older than 8 min = stale
STALE_ACTIVITY_S = 10      # activity older than 10s = stale
# -------------------------------------------------------------------------

# Background colours per activity state: same hue family as the terminal
# monitor's ANSI colours, bright enough to identify the hue at a glance,
# dark enough for white text to read over them.
STATE_BG = {
    "INACTIVE":   "#303035",
    "IDLE":       "#363842",
    "COMPOSING":  "#1a6e6e",
    "THINKING":   "#6e2e6e",
    "TOOL_USE":   "#6e6820",
    "WEB_SEARCH": "#2a3e80",
    "STREAMING":  "#2a6e2e",
    "UNKNOWN":    "#6e2a2a",
}

# Text colour for the state label: brighter, saturated versions.
STATE_FG = {
    "INACTIVE":   "#909090",
    "IDLE":       "#c0c0c8",
    "COMPOSING":  "#60f0f0",
    "THINKING":   "#f070f0",
    "TOOL_USE":   "#f0e850",
    "WEB_SEARCH": "#60a0ff",
    "STREAMING":  "#60f060",
    "UNKNOWN":    "#ff6060",
}

DEFAULT_BG = "#1a1a2e"
FG         = "#e0e0e0"
FG_DIM     = "#b0b0be"
ACCENT     = "#6c63ff"
BAR_TRACK  = "#181828"    # bar track stays fixed, darker than any state bg
GREY_BAR   = "#3a3a52"


def bar_colour(pct):
    if pct < 50:  return "#4ade80"
    if pct < 80:  return "#facc15"
    return "#f87171"


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def age_seconds(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
    except Exception:
        return None


class UsageWidget:
    def __init__(self, root):
        self.root = root
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=DEFAULT_BG)
        root.geometry("380x270")

        self._bg = DEFAULT_BG
        self._drag_x = 0
        self._drag_y = 0
        root.bind("<Button-1>", self._start_drag)
        root.bind("<B1-Motion>", self._do_drag)
        root.bind("<Button-3>", lambda e: root.destroy())

        self._all_widgets = []   # everything that needs bg updates
        self._build_ui()
        self._refresh_activity()
        self._refresh_usage()

    # ---- dragging --------------------------------------------------------
    def _start_drag(self, e):
        self._drag_x, self._drag_y = e.x, e.y

    def _do_drag(self, e):
        x = self.root.winfo_x() + e.x - self._drag_x
        y = self.root.winfo_y() + e.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    # ---- UI build --------------------------------------------------------
    def _label(self, parent, **kw):
        w = tk.Label(parent, bg=DEFAULT_BG, **kw)
        self._all_widgets.append(w)
        return w

    def _frame(self, parent, **kw):
        w = tk.Frame(parent, bg=DEFAULT_BG, **kw)
        self._all_widgets.append(w)
        return w

    def _build_ui(self):
        px = 12
        self.main = self._frame(self.root)
        self.main.pack(fill="both", expand=True)

        self.plan_label = self._label(
            self.main, text="Claude", font=("Segoe UI Semibold", 14),
            fg=ACCENT, anchor="w")
        self.plan_label.pack(fill="x", padx=px, pady=(12, 6))

        self._build_bar_group("Session (5h)", "session")
        self._frame(self.main, height=2).pack()
        self._build_bar_group("Weekly (7d)", "weekly")

        self.usage_status = self._label(
            self.main, text="starting...", font=("Segoe UI", 10),
            fg=FG_DIM, anchor="w")
        self.usage_status.pack(fill="x", padx=px, pady=(4, 0))

        self.activity_label = self._label(
            self.main, text="", font=("Segoe UI Semibold", 12),
            fg=FG_DIM, anchor="w")
        self.activity_label.pack(fill="x", padx=px, pady=(6, 12))

    def _build_bar_group(self, title, prefix):
        px = 12
        row = self._frame(self.main)
        row.pack(fill="x", padx=px, pady=(0, 1))

        self._label(row, text=title, font=("Segoe UI", 11),
                    fg=FG, anchor="w").pack(side="left")
        pct_lbl = self._label(row, text="--", font=("Segoe UI Semibold", 11),
                              fg=FG, anchor="e")
        pct_lbl.pack(side="right")
        setattr(self, f"{prefix}_pct_label", pct_lbl)

        canvas = tk.Canvas(self.main, height=16, bg=BAR_TRACK,
                           highlightthickness=0, bd=0)
        canvas.pack(fill="x", padx=px)
        setattr(self, f"{prefix}_canvas", canvas)

        reset_lbl = self._label(self.main, text="", font=("Segoe UI", 9),
                                fg=FG_DIM, anchor="w")
        reset_lbl.pack(fill="x", padx=px)
        setattr(self, f"{prefix}_reset_label", reset_lbl)

    # ---- background colour -----------------------------------------------
    def _set_bg(self, bg):
        if bg == self._bg:
            return
        self._bg = bg
        self.root.configure(bg=bg)
        for w in self._all_widgets:
            try:
                w.configure(bg=bg)
            except Exception:
                pass

    # ---- bar drawing -----------------------------------------------------
    def _draw_bar(self, prefix, pct, colour):
        canvas = getattr(self, f"{prefix}_canvas")
        canvas.delete("all")
        canvas.update_idletasks()
        w, h = canvas.winfo_width(), canvas.winfo_height()
        if w < 1:
            return
        canvas.create_rectangle(0, 0, w, h, fill=BAR_TRACK, outline="")
        fill_w = max(0, min(w, int(w * pct / 100)))
        if fill_w > 0:
            canvas.create_rectangle(0, 0, fill_w, h, fill=colour, outline="")

    def _update_group(self, prefix, section, stale):
        pct_lbl   = getattr(self, f"{prefix}_pct_label")
        reset_lbl = getattr(self, f"{prefix}_reset_label")
        if not section or section.get("pct") is None:
            pct_lbl.configure(text="--", fg=FG_DIM)
            reset_lbl.configure(text="")
            self._draw_bar(prefix, 0, GREY_BAR)
            return
        pct = section["pct"]
        colour = GREY_BAR if stale else bar_colour(pct)
        pct_lbl.configure(text=f"{pct:.0f}%",
                          fg=(FG_DIM if stale else bar_colour(pct)))
        resets = section.get("resets")
        reset_lbl.configure(text=(f"resets {resets}" if resets else ""))
        self._draw_bar(prefix, pct, colour)

    # ---- refresh loops ---------------------------------------------------
    def _refresh_activity(self):
        data = read_json(ACTIVITY_FILE)
        if data:
            age = age_seconds(data.get("updated", ""))
            stale = age is None or age > STALE_ACTIVITY_S
            state = data.get("state", "UNKNOWN") if not stale else "UNKNOWN"
        else:
            state = "UNKNOWN"

        bg = STATE_BG.get(state, DEFAULT_BG)
        fg = STATE_FG.get(state, FG_DIM)
        self._set_bg(bg)

        if state == "UNKNOWN" and not data:
            label = "waiting for activity monitor"
        elif state == "UNKNOWN":
            label = "activity stale"
        else:
            label = state.replace("_", " ").lower()
        self.activity_label.configure(text=label, fg=fg)

        self.root.after(ACTIVITY_MS, self._refresh_activity)

    def _refresh_usage(self):
        state = read_json(USAGE_FILE)
        now_str = datetime.now().strftime("%H:%M")

        if state is None:
            self._update_group("session", None, True)
            self._update_group("weekly", None, True)
            self.usage_status.configure(text="waiting for poller...")
        else:
            status = state.get("status", "?")
            age = age_seconds(state.get("updated", ""))
            stale = (status != "ok") or (age is None) or (age > STALE_USAGE_S)

            self._update_group("session", state.get("session"), stale)
            self._update_group("weekly", state.get("weekly_all"), stale)

            if status == "ok" and not stale:
                self.usage_status.configure(text=f"updated {now_str}",
                                            fg=FG_DIM)
            elif status == "auth":
                self.usage_status.configure(
                    text="needs login in Claude Code", fg="#f87171")
            elif status in ("down", "render_timeout"):
                self.usage_status.configure(
                    text=f"poller {status} - last known", fg="#facc15")
            else:
                self.usage_status.configure(
                    text="stale - poller stopped?", fg="#facc15")

        self.root.after(USAGE_MS, self._refresh_usage)


def main():
    root = tk.Tk()
    UsageWidget(root)
    root.mainloop()


if __name__ == "__main__":
    main()
