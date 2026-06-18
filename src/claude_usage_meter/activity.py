#!/usr/bin/env python3
"""
claude-activity-indicator.py

Activity producer for the claude-usage-meter project. Reads Claude Desktop's
state from the Windows UI Automation accessibility tree and writes activity.json
for the widget. Read-only against Claude; no app modification.

COLD START: a fresh Claude process starts with its web accessibility tree off. No
external trigger is needed: connect()'s own UIA access builds it, and the startup
retry rides out the roughly 1-second async build. Narrator is not required.

States:
  INACTIVE   - idle, input empty, app not focused
  IDLE       - idle, input empty, app focused
  COMPOSING  - idle, user has typed in the input box
  THINKING   - active, reasoning summaries arriving (bursty)
  TOOL_USE   - active, an MCP tool token present (underscore, no spaces)
  WEB_SEARCH - active, present-tense "Searching..." text
  STREAMING  - active, no new reasoning entries (answer streaming)
  UNKNOWN    - tree unavailable

Requires: comtypes. Run under Python 3.13: py -3.13 claude-activity-indicator.py
"""

import ctypes, os, sys, time, json, tempfile
from ctypes import wintypes
from datetime import datetime, timezone

import comtypes.client
comtypes.client.GetModule("UIAutomationCore.dll")
import comtypes.gen.UIAutomationClient as UIA

# ---- config -------------------------------------------------------------
from claude_usage_meter.paths import DATA_DIR, ACTIVITY_FILE
POLL       = 0.2     # seconds between probes
HEARTBEAT  = 5.0     # force a write at least this often, even if unchanged
STREAM_LINGER    = 1.0   # hold STREAMING this long after reply text last grew
CONTENT_THROTTLE = 0.3   # min seconds between reply-size scans (cheap now)
TOOL_LINGER      = 1.0   # hold TOOL_USE after structural signal to bridge gaps
CONNECT_BACKOFF = [0.5, 1, 2, 3, 5]   # startup / reconnect retry schedule
# -------------------------------------------------------------------------

PROP_CT, PROP_NAME, PROP_AID = 30003, 30005, 30011
CT_BUTTON, CT_EDIT, CT_TEXT, CT_STATUSBAR, CT_GROUP = 50000, 50004, 50020, 50017, 50026
SCOPE_CHILDREN, SCOPE_DESC = 2, 4
PATTERN_VALUE = 10002

u = ctypes.windll.user32
u.IsWindowVisible.argtypes = [wintypes.HWND]; u.IsWindowVisible.restype = wintypes.BOOL
u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u.GetWindowTextLengthW.argtypes = [wintypes.HWND]; u.GetWindowTextLengthW.restype = ctypes.c_int
u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
GetForegroundWindow = u.GetForegroundWindow; GetForegroundWindow.restype = wintypes.HWND
GetWindowThreadProcessId = u.GetWindowThreadProcessId
GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
ENUM_CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

def pid_of(hwnd):
    p = wintypes.DWORD(0); GetWindowThreadProcessId(hwnd, ctypes.byref(p)); return p.value

def find_claude_windows():
    """Return handles of all visible Chrome_WidgetWin_1 windows titled 'Claude'.
    EnumWindows + visibility filter avoids the FindWindowW bug where an invisible
    helper window is returned first."""
    out = []
    def cb(hwnd, _):
        if not u.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, cls, 256)
        if cls.value != "Chrome_WidgetWin_1":
            return True
        n = u.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, title, n + 1)
        if title.value == "Claude":
            out.append(hwnd)
        return True
    u.EnumWindows(ENUM_CB(cb), 0)
    return out

def create_uia():
    return comtypes.client.CreateObject(
        "{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=UIA.IUIAutomation)


class ActivityMonitor:
    def __init__(self):
        self.uia = create_uia()
        self.cond_sb   = self.uia.CreatePropertyCondition(PROP_CT, CT_STATUSBAR)
        self.cond_tx   = self.uia.CreatePropertyCondition(PROP_CT, CT_TEXT)
        self.cond_edit = self.uia.CreatePropertyCondition(PROP_CT, CT_EDIT)
        self.cond_btn  = self.uia.CreatePropertyCondition(PROP_CT, CT_BUTTON)
        self.main_hwnd = None
        self.claude_pid = None
        self.primary = None
        self.edit = None
        self.model_btn = None
        self._was_active = False
        self._baseline = set()
        # thinking vs streaming: track growth of the trailing reply text
        self._txt_count = None
        self._txt_lastlen = 0
        self._last_growth = 0.0
        self._next_content = 0.0
        self._stream_decision = "THINKING"
        self._result_baseline = 0
        self._last_tool = 0.0

    def connect(self):
        candidates = find_claude_windows()
        if not candidates:
            return False
        cond_df = self.uia.CreatePropertyCondition(PROP_AID, "dframe-main")
        cond_pp = self.uia.CreateAndCondition(
            self.uia.CreatePropertyCondition(PROP_CT, CT_GROUP),
            self.uia.CreatePropertyCondition(PROP_NAME, "Primary pane"))
        for hwnd in candidates:
            try:
                root = self.uia.ElementFromHandle(hwnd)
                df = root.FindAll(SCOPE_DESC, cond_df)
                if not df or df.Length == 0:
                    continue
                dframe = df.GetElement(0)
                pp = dframe.FindAll(SCOPE_DESC, cond_pp)
                if not pp or pp.Length == 0:
                    continue
                self.main_hwnd = hwnd
                self.claude_pid = pid_of(hwnd)
                self.primary = pp.GetElement(0)
                eds = self.primary.FindAll(SCOPE_DESC, self.cond_edit)
                self.edit = eds.GetElement(0) if (eds and eds.Length) else None
                self.model_btn = None
                btns = self.primary.FindAll(SCOPE_DESC, self.cond_btn)
                if btns:
                    for i in range(btns.Length):
                        try:
                            n = btns.GetElement(i).CurrentName or ""
                        except Exception:
                            continue
                        if n.startswith("Model:"):
                            self.model_btn = btns.GetElement(i); break
                return True
            except Exception:
                continue
        self._invalidate()
        return False

    def _invalidate(self):
        self.primary = self.edit = self.model_btn = None

    def _statusbar_texts(self):
        out = []
        sbs = self.primary.FindAll(SCOPE_CHILDREN, self.cond_sb)
        if sbs:
            for i in range(sbs.Length):
                kids = sbs.GetElement(i).FindAll(SCOPE_CHILDREN, self.cond_tx)
                if kids:
                    for j in range(kids.Length):
                        try:
                            n = kids.GetElement(j).CurrentName
                        except Exception:
                            n = None
                        if n:
                            out.append(n)
        return out

    def _edit_value(self):
        if self.edit is None:
            return None
        vp = self.edit.GetCurrentPattern(PATTERN_VALUE).QueryInterface(
            UIA.IUIAutomationValuePattern)
        return vp.CurrentValue

    def _model(self):
        if self.model_btn is None:
            return ""
        return self.model_btn.CurrentName or ""

    def _focused(self):
        fg = GetForegroundWindow()
        return bool(fg) and pid_of(fg) == self.claude_pid

    @staticmethod
    def _has_content(v):
        if not v:
            return False
        c = v.replace("\n", "").replace("\r", "").strip()
        if c == "" or c == "Write your prompt to Claude" or c.startswith("Write a message"):
            return False
        return True

    def _result_count(self):
        cond = self.uia.CreateAndCondition(
            self.uia.CreatePropertyCondition(PROP_CT, CT_BUTTON),
            self.uia.CreatePropertyCondition(PROP_NAME, "Result"))
        found = self.primary.FindAll(SCOPE_DESC, cond)
        return found.Length if found else 0

    def _request_exists(self):
        cond = self.uia.CreateAndCondition(
            self.uia.CreatePropertyCondition(PROP_CT, CT_BUTTON),
            self.uia.CreatePropertyCondition(PROP_NAME, "Request"))
        found = self.primary.FindAll(SCOPE_DESC, cond)
        return bool(found and found.Length > 0)

    def _update_stream(self, now):
        """STREAMING when the trailing reply text is growing: new Text children
        appear at paragraph breaks and the last Text child's length climbs as
        tokens stream. Reasoning renders as Buttons/StatusBars, not Text
        children, so it never trips this. Cheap: one children query, one read."""
        if now >= self._next_content:
            self._next_content = now + CONTENT_THROTTLE
            try:
                tx = self.primary.FindAll(SCOPE_CHILDREN, self.cond_tx)
                cnt = tx.Length if tx else 0
                last_len = 0
                if cnt:
                    try:
                        last_len = len(tx.GetElement(cnt - 1).CurrentName or "")
                    except Exception:
                        last_len = 0
            except Exception:
                cnt, last_len = self._txt_count, self._txt_lastlen
            if self._txt_count is not None and (cnt > self._txt_count or last_len > self._txt_lastlen):
                self._last_growth = now
            self._txt_count, self._txt_lastlen = cnt, last_len
        self._stream_decision = "STREAMING" if (now - self._last_growth) < STREAM_LINGER else "THINKING"
    def probe(self, now):
        if self.primary is None and not self.connect():
            return "UNKNOWN", ""
        try:
            texts = self._statusbar_texts()
            active = "Claude is responding" in texts
            model = self._model()

            if active:
                cur = set(t for t in texts if t not in ("Claude is responding", ""))
                if not self._was_active:
                    self._baseline = cur.copy()
                    self._txt_count = None
                    self._txt_lastlen = 0
                    self._last_growth = 0.0
                    self._next_content = 0.0
                    self._stream_decision = "THINKING"
                    self._result_baseline = self._result_count()
                    self._last_tool = 0.0
                    self._was_active = True
                new = cur - self._baseline

                # --- tool detection: structural first, text fallback ---
                # 1) Result count increased -> tool just completed
                rc = self._result_count()
                if rc > self._result_baseline:
                    self._result_baseline = rc
                    self._last_tool = now
                    return "TOOL_USE", model
                # 2) Request button present -> tool in progress
                if self._request_exists():
                    self._last_tool = now
                    return "TOOL_USE", model
                # 3) Linger after structural signal
                if self._last_tool and (now - self._last_tool) < TOOL_LINGER:
                    return "TOOL_USE", model
                # 4) StatusBar heuristic fallback (web MCPs, R, etc.)
                for t in new:
                    if " " not in t and "_" in t:
                        self._last_tool = now
                        return "TOOL_USE", model

                # --- web search detection ---
                for t in new:
                    if t.startswith("Searching"):
                        return "WEB_SEARCH", model

                # --- thinking vs streaming from reply-text growth ---
                self._update_stream(now)
                return self._stream_decision, model

            if self._was_active:
                self._was_active = False
                self._baseline = set()
                self._result_baseline = 0
                self._last_tool = 0.0
            val = self._edit_value()
            if self._has_content(val):
                return "COMPOSING", model
            return ("IDLE" if self._focused() else "INACTIVE"), model
        except Exception:
            self._invalidate()
            return "UNKNOWN", ""


def write_state(state, model):
    payload = {"updated": datetime.now(timezone.utc).astimezone().isoformat(),
               "state": state, "model": model}
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        for _ in range(3):
            try:
                os.replace(tmp, ACTIVITY_FILE); break
            except PermissionError:
                time.sleep(0.3)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def log(msg):
    try:
        print(f"{datetime.now().strftime('%H:%M:%S')}  {msg}", flush=True)
    except Exception:
        pass


def main():
    mon = ActivityMonitor()
    for delay in CONNECT_BACKOFF + [5] * 1000:
        if mon.connect():
            break
        log("waiting for Claude accessibility tree to build...")
        time.sleep(delay)
    log(f"connected, writing {ACTIVITY_FILE}")

    last_state = None
    last_write = 0.0
    try:
        while True:
            now = time.monotonic()
            state, model = mon.probe(now)
            if state != last_state or (now - last_write) >= HEARTBEAT:
                write_state(state, model)
                last_write = now
                if state != last_state:
                    log(state + (f"   {model}" if model else ""))
                    last_state = state
            time.sleep(max(0.01, POLL - (time.monotonic() - now)))
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
