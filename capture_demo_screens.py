#!/usr/bin/env python3
"""Captures screenshots of different states of the keybind overlay."""

import os
import subprocess
import time
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import overlay

SCRATCH_CONFIG = os.environ.get("TEST_UMBRIEL_CONFIG", "/tmp/umbriel-test/config/config.toml")
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

def main():
    env = os.environ.copy()
    display = env.get("WAYLAND_DISPLAY", "wayland-0")

    # Ensure native cheatsheet is closed
    subprocess.run(["umbriel", "msg", "cheatsheet-close"], env=env)
    # Ensure default submap
    subprocess.run(["umbriel", "msg", "submap", "reset"], env=env)
    time.sleep(0.3)

    app = overlay.KeybindOverlay(SCRATCH_CONFIG, scratch_config=SCRATCH_CONFIG)
    app.show_all()

    def process_ui(duration=0.2):
        end = time.time() + duration
        while time.time() < end:
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            time.sleep(0.01)

    process_ui(0.3)

    # 1. Capture Default View
    subprocess.run(["grim", os.path.join(OUT_DIR, "screenshot-overlay-default.png")], env=env)
    print("Captured screenshot-overlay-default.png")

    # 2. Capture Filtered Search View (e.g. "max")
    app.search_entry.set_text("max")
    process_ui(0.3)
    subprocess.run(["grim", os.path.join(OUT_DIR, "screenshot-overlay-search.png")], env=env)
    print("Captured screenshot-overlay-search.png")

    # 3. Capture Safety Hint (Enter on session-quit row)
    app.search_entry.set_text("quit")
    process_ui(0.2)
    session_row = next(b for b in app.current_filtered_binds if "session-quit" in b.get("action", ""))
    app._execute_bind(session_row)
    process_ui(0.3)
    subprocess.run(["grim", os.path.join(OUT_DIR, "screenshot-overlay-safety.png")], env=env)
    print("Captured screenshot-overlay-safety.png")

    # 4. Capture Submap View
    app.search_entry.set_text("")
    subprocess.run(["umbriel", "msg", "submap", "screencapture"], env=env)
    process_ui(0.5)
    subprocess.run(["grim", os.path.join(OUT_DIR, "screenshot-overlay-submap.png")], env=env)
    print("Captured screenshot-overlay-submap.png")

    # Reset
    subprocess.run(["umbriel", "msg", "submap", "reset"], env=env)
    app.destroy()
    process_ui(0.1)

if __name__ == "__main__":
    main()
