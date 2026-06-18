#!/usr/bin/env python3
"""
claude-usage-poller.py

Keeps a Claude Code session alive in WSL tmux, scrapes the /usage panel
on a randomised timer, and writes the result to usage.json for the widget.

This process is the ONLY thing that touches Claude. It does so through the
official CLI, so the request leaves Anthropic's own client. No token is read,
extracted, or sent by this script.
"""

import subprocess, time, re, json, os, sys, random, tempfile
from datetime import datetime, timezone

# ---- config -------------------------------------------------------------
DISTRO   = "Ubuntu"
SESSION  = "usagecap"
from claude_usage_meter.paths import DATA_DIR, USAGE_FILE, LOG_FILE
POLL_MIN, POLL_MAX = 180, 300      # seconds; randomised each cycle
LAUNCH_WAIT = 9                    # CLI start-up before trust prompt
TRUST_WAIT  = 6                    # after confirming trust, REPL appears
RENDER_TIMEOUT = 8.0               # max wait for /usage panel to paint
# LOG_FILE imported from paths
CREATE_NO_WINDOW = 0x08000000      # hide wsl.exe console windows under pythonw
# -------------------------------------------------------------------------

LABELS = {"Current session": "session",
          "Current week (all models)": "weekly_all",
          "Current week (Sonnet only)": "weekly_sonnet"}

def wsl(cmd, timeout=60):
    p = subprocess.run(["wsl.exe", "-d", DISTRO, "--", "bash", "-lic", cmd],
                       capture_output=True, timeout=timeout,
                       creationflags=CREATE_NO_WINDOW)
    return p.stdout.decode("utf-8", "replace")

def cap():
    return wsl(f'tmux capture-pane -p -t {SESSION}')

def log(msg):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as lf:
            lf.write(line + "\n")
    except Exception:
        pass
    try:
        print(line, flush=True)   # shows with a console; harmless under pythonw
    except Exception:
        pass

# ---- parsing + render check (from the prototype) ------------------------
def parse_usage(text):
    lines = [l.rstrip() for l in text.splitlines()]
    out = {}
    for i, line in enumerate(lines):
        for label, key in LABELS.items():
            if label in line:
                window = "\n".join(lines[i:i+4])
                pct = re.search(r'(\d+)%\s*used', window)
                rst = re.search(r'Resets\s+(.+)', window)
                out[key] = {"pct": int(pct.group(1)) if pct else None,
                            "resets": rst.group(1).strip() if rst else None}
    return out

def frame_valid(text, parsed):
    all_labels = all(lbl in text for lbl in LABELS)
    s = parsed.get("session", {}).get("pct")
    w = parsed.get("weekly_all", {}).get("pct")
    pcts_ok = (isinstance(s, int) and 0 <= s <= 100 and
               isinstance(w, int) and 0 <= w <= 100)
    return all_labels and pcts_ok

# ---- session lifecycle --------------------------------------------------
def session_alive():
    p = subprocess.run(["wsl.exe", "-d", DISTRO, "--",
                        "tmux", "has-session", "-t", SESSION],
                       capture_output=True,
                       creationflags=CREATE_NO_WINDOW)
    return p.returncode == 0

def repl_ready(text):
    return ("? for shortcuts" in text) or ('Try "' in text)

def launch_session():
    log("launching Claude Code session...")
    wsl(f'tmux kill-session -t {SESSION} 2>/dev/null; true')
    wsl(f'tmux new-session -d -s {SESSION} -x 120 -y 40 "cd ~ && bash -lic claude"')
    time.sleep(LAUNCH_WAIT)
    screen = cap()
    if "trust this folder" in screen.lower():
        wsl(f'tmux send-keys -t {SESSION} Enter')   # confirm trust
        time.sleep(TRUST_WAIT)
        screen = cap()
    return repl_ready(screen)

def ensure_session():
    if not session_alive():
        return launch_session()
    if not repl_ready(cap()):              # shell is alive but Claude exited
        return launch_session()
    return True

# ---- one poll -----------------------------------------------------------
AUTH_HINTS = ("log in", "/login", "authenticate", "invalid api", "expired")

def poll_once():
    wsl(f'tmux send-keys -t {SESSION} Escape'); time.sleep(0.3)
    wsl(f'tmux send-keys -t {SESSION} "/usage"'); time.sleep(0.4)
    wsl(f'tmux send-keys -t {SESSION} Enter')
    start = time.time()
    while time.time() - start < RENDER_TIMEOUT:
        text = cap()
        parsed = parse_usage(text)
        if frame_valid(text, parsed):
            wsl(f'tmux send-keys -t {SESSION} Escape')
            return "ok", parsed
        time.sleep(0.4)
    wsl(f'tmux send-keys -t {SESSION} Escape')
    low = text.lower()
    if any(h in low for h in AUTH_HINTS):   # best-effort; exact text unverified
        return "auth", parsed
    return "render_timeout", parsed

# ---- output -------------------------------------------------------------
def write_state(status, data):
    payload = {"updated": datetime.now(timezone.utc).astimezone().isoformat(),
               "status": status,
               "session":       data.get("session"),
               "weekly_all":    data.get("weekly_all"),
               "weekly_sonnet": data.get("weekly_sonnet")}
    # atomic write so the widget never reads a half-written / mid-sync file
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        for attempt in range(3):
            try:
                os.replace(tmp, USAGE_FILE); break
            except PermissionError:        # Drive may briefly lock during sync
                time.sleep(0.5)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

# ---- main loop ----------------------------------------------------------
def main():
    log(f"poller starting, writing to {USAGE_FILE}")
    last_good = {}                          # carried forward on failures
    while True:
        try:
            if not ensure_session():
                write_state("down", last_good)
                log("session not ready; will retry")
            else:
                status, data = poll_once()
                if status == "ok":
                    last_good = data
                    write_state("ok", data)
                    log(f"ok  session={data['session']['pct']}%  "
                        f"weekly={data['weekly_all']['pct']}%")
                else:
                    write_state(status, last_good)   # keep last numbers, mark state
                    log(f"poll status: {status}")
        except Exception as e:
            log(f"error: {type(e).__name__}: {e}")
        time.sleep(random.uniform(POLL_MIN, POLL_MAX))

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("stopped (tmux session left running for fast restart)")
