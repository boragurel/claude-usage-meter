"""Launcher for claude-usage-meter.

Starts the activity monitor, usage poller, and sprite display as separate
processes. Restarts any that exit unexpectedly. Ctrl+C stops everything.

Usage:
    claude-meter              (starts all three)
    claude-meter --no-sprite  (producers only, no display)
"""

import subprocess
import sys
import time
import signal

COMPONENTS = {
    "activity": "claude_usage_meter.activity",
    "poller":   "claude_usage_meter.poller",
    "sprite":   "claude_usage_meter.sprite",
}

RESTART_DELAY = 3.0   # seconds before restarting a dead process
MAX_RAPID_RESTARTS = 5
RAPID_WINDOW = 60.0   # if it dies this many times within this window, give up


def launch(name, module):
    """Start a component as a subprocess."""
    proc = subprocess.Popen(
        [sys.executable, "-m", module],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    print(f"  [{name}] started (pid {proc.pid})")
    return proc


def main():
    skip_sprite = "--no-sprite" in sys.argv

    components = dict(COMPONENTS)
    if skip_sprite:
        del components["sprite"]

    print(f"claude-usage-meter: starting {len(components)} components")

    procs = {}
    restart_times = {name: [] for name in components}

    for name, module in components.items():
        procs[name] = launch(name, module)

    try:
        while True:
            time.sleep(1.0)
            for name, module in components.items():
                proc = procs[name]
                ret = proc.poll()
                if ret is not None:
                    print(f"  [{name}] exited (code {ret})")

                    # Track rapid restarts
                    now = time.time()
                    times = restart_times[name]
                    times.append(now)
                    # Keep only recent restarts
                    times[:] = [t for t in times if now - t < RAPID_WINDOW]

                    if len(times) >= MAX_RAPID_RESTARTS:
                        print(f"  [{name}] died {MAX_RAPID_RESTARTS} times "
                              f"in {RAPID_WINDOW}s, not restarting")
                        continue

                    time.sleep(RESTART_DELAY)
                    procs[name] = launch(name, module)

    except KeyboardInterrupt:
        print("\nclaude-usage-meter: shutting down")
        for name, proc in procs.items():
            if proc.poll() is None:
                proc.terminate()
                print(f"  [{name}] terminated")

        # Give them a moment, then force-kill stragglers
        deadline = time.time() + 3.0
        for name, proc in procs.items():
            remaining = max(0, deadline - time.time())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                proc.kill()
                print(f"  [{name}] killed")


if __name__ == "__main__":
    main()
