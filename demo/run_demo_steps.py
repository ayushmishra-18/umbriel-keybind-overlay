#!/usr/bin/env python3
"""Drives the UI steps for the self-contained demo."""

import json
import os
import subprocess
import sys
import time

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib

import overlay


def pump_events(duration=0.3):
    end = time.time() + duration
    while time.time() < end:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.01)


def main():
    if len(sys.argv) < 5:
        print("Usage: run_demo_steps.py <config_path> <cache_dir> <out_dir> <term_bin>")
        sys.exit(1)

    config_path = os.path.abspath(sys.argv[1])
    cache_dir = os.path.abspath(sys.argv[2])
    out_dir = os.path.abspath(sys.argv[3])
    term_bin = sys.argv[4]

    env = os.environ.copy()
    display = env.get("WAYLAND_DISPLAY")

    print(f"[Demo Steps] Connecting to display={display}, config={os.path.basename(config_path)}")

    # Ensure clean compositor state
    subprocess.run(["umbriel", "msg", "cheatsheet-close"], env=env)
    subprocess.run(["umbriel", "msg", "submap", "reset"], env=env)
    time.sleep(0.3)

    # Initialize overlay
    app = overlay.KeybindOverlay(config_path, scratch_config=config_path, scratch_cache=cache_dir)
    app.show_all()
    pump_events(0.5)

    # 1. Capture default overlay view
    shot1 = os.path.join(out_dir, "01-default-overlay.png")
    subprocess.run(["grim", shot1], env=env, check=True)
    print(f"[Demo Steps] Captured 01-default-overlay.png")

    # 2. Filtered search (e.g. "screenshot")
    app.search_entry.set_text("screenshot")
    pump_events(0.4)
    shot2 = os.path.join(out_dir, "02-search-filter.png")
    subprocess.run(["grim", shot2], env=env, check=True)
    print(f"[Demo Steps] Captured 02-search-filter.png")

    # 3. Active submap banner (enter screencapture)
    app.search_entry.set_text("")
    subprocess.run(["umbriel", "msg", "submap", "screencapture"], env=env, check=True)
    # Allow subscriber thread to receive event
    for _ in range(10):
        time.sleep(0.05)
        pump_events(0.05)
        if app.active_submap == "screencapture":
            break
    shot3 = os.path.join(out_dir, "03-submap-banner.png")
    subprocess.run(["grim", shot3], env=env, check=True)
    print(f"[Demo Steps] Captured 03-submap-banner.png")

    # Reset submap
    subprocess.run(["umbriel", "msg", "submap", "reset"], env=env, check=True)
    for _ in range(10):
        time.sleep(0.05)
        pump_events(0.05)
        if app.active_submap is None:
            break

    # 4. Visible Window Control execution
    # Spawn known safe terminal
    print(f"[Demo Steps] Spawning {term_bin}...")
    subprocess.run(["umbriel", "msg", "spawn", term_bin], env=env, check=True)
    time.sleep(1.2)

    # Record windows before maximize
    win_res = subprocess.run(["umbriel", "windows", "--json"], env=env, capture_output=True, text=True, check=True)
    windows_before = json.loads(win_res.stdout.strip())
    with open(os.path.join(out_dir, "windows-before-maximize.json"), "w") as f:
        json.dump(windows_before, f, indent=2)
    print(f"[Demo Steps] Saved windows-before-maximize.json ({len(windows_before)} window(s))")

    # From overlay, trigger window-toggle-maximize
    max_bind = next((b for b in app.binds_data if b.get("action") == "window-toggle-maximize"), None)
    if not max_bind:
        print("[Demo Steps] Error: window-toggle-maximize binding not found in config!")
        sys.exit(1)

    # Execute bind (mocking Gtk.main_quit so overlay stays open for screenshot)
    quit_called = []
    orig_quit = Gtk.main_quit
    Gtk.main_quit = lambda: quit_called.append(True)
    try:
        app._execute_bind(max_bind)
        pump_events(0.4)
        time.sleep(0.4)
    finally:
        Gtk.main_quit = orig_quit

    # Record windows after maximize
    win_res_after = subprocess.run(["umbriel", "windows", "--json"], env=env, capture_output=True, text=True, check=True)
    windows_after = json.loads(win_res_after.stdout.strip())
    with open(os.path.join(out_dir, "windows-after-maximize.json"), "w") as f:
        json.dump(windows_after, f, indent=2)
    print(f"[Demo Steps] Saved windows-after-maximize.json (maximized={windows_after[0].get('maximized')})")

    # Capture maximized screenshot with overlay
    shot4 = os.path.join(out_dir, "04-window-maximized.png")
    subprocess.run(["grim", shot4], env=env, check=True)
    print(f"[Demo Steps] Captured 04-window-maximized.png")

    # Clean up window
    subprocess.run(["umbriel", "msg", "window-close"], env=env, check=True)
    time.sleep(0.3)

    # Clean up overlay
    app.destroy()
    pump_events(0.1)
    print("[Demo Steps] UI steps completed successfully.")


if __name__ == "__main__":
    main()
