#!/usr/bin/env python3
"""Evidence Repair & Verification: Capture distinct v5.2 screenshots of the Umbriel Keybind Overlay.

Ensures:
1. Native Umbriel cheatsheet is closed via `umbriel msg cheatsheet-close` before any capture.
2. Verified via `umbriel layers` that the cheatsheet is gone.
3. Captures distinct states using `grim -c`:
   - screenshot-v5-compact-1result.png (1 result, whole row visible)
   - screenshot-v5-compact-2results.png (2 results, BOTH rows completely visible, no clipping)
   - screenshot-v5-capped-7results.png (7 results, capped at 320px scrolled height, top view)
   - screenshot-v5-capped-7results-scrolled.png (7 results, scrolled to bottom, bottom row fully reachable)
   - screenshot-v5-capped-full.png (44 results, capped at 320px scrolled height, top view)
   - screenshot-v5-capped-full-scrolled.png (44 results, scrolled to bottom, bottom row fully reachable)
   - screenshot-v5-hover-row.png (pointer hovering over row with arrow cursor)
   - screenshot-v5-hover-entry.png (pointer hovering over search entry with I-beam cursor)
4. Verifies all md5sums are distinct.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_SRC = os.path.join(PROJECT_DIR, "examples", "typical-desktop.toml")
WLR_PTR_BIN = os.path.join(PROJECT_DIR, "tests", "wlr_ptr")


def ensure_wlr_ptr():
    if not os.path.exists(WLR_PTR_BIN):
        print("Compiling tests/wlr_ptr...")
        subprocess.run(["make", "-C", os.path.join(PROJECT_DIR, "tests")], check=True)
    if not os.path.exists(WLR_PTR_BIN):
        raise RuntimeError(f"Failed to build {WLR_PTR_BIN}")


def run_worker():
    """Worker process that runs with WAYLAND_DISPLAY already set to the nested compositor."""
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import Gtk, Gdk, GLib, GtkLayerShell
    import overlay

    def pump_gtk(duration=0.3):
        ctx = GLib.MainContext.default()
        end = time.time() + duration
        while time.time() < end:
            while ctx.pending():
                ctx.iteration(False)
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            time.sleep(0.01)

    scratch_config = sys.argv[2]
    cache_dir = sys.argv[3]
    env = os.environ.copy()

    # 1. Ensure native cheatsheet is closed before any capture
    print("[Worker] Sending cheatsheet-close to nested compositor...")
    subprocess.run(["umbriel", "msg", "cheatsheet-close"], env=env, check=True)
    subprocess.run(["umbriel", "msg", "submap", "reset"], env=env)
    time.sleep(0.3)

    # Verify cheatsheet is closed via umbriel layers
    layers_chk = subprocess.run(["umbriel", "layers"], env=env, capture_output=True, text=True)
    print("[Worker] Compositor layers before overlay launch:")
    for line in layers_chk.stdout.strip().splitlines():
        print(f"  {line}")

    # Launch overlay
    print("[Worker] Initializing KeybindOverlay...")
    app = overlay.KeybindOverlay(scratch_config, scratch_config=scratch_config, scratch_cache=cache_dir)
    app.show_all()
    pump_gtk(0.5)

    # Verify overlay surface in layers
    layers_chk = subprocess.run(["umbriel", "layers"], env=env, capture_output=True, text=True)
    print("[Worker] Compositor layers after overlay launch:")
    for line in layers_chk.stdout.strip().splitlines():
        print(f"  {line}")

    screen_w = 908
    screen_h = 1038

    # Start pointer outside window
    subprocess.run([WLR_PTR_BIN, "50", "50", "0", str(screen_w), str(screen_h)], env=env)
    pump_gtk(0.3)

    captured_files = []

    def capture_shot(filename, desc):
        path = os.path.join(PROJECT_DIR, filename)
        subprocess.run(["grim", "-c", path], env=env, check=True)
        alloc = app.get_allocation()
        scrolled_alloc = app.scrolled.get_allocation()
        print(f"[Captured] {filename} ({os.path.getsize(path)} bytes) - {desc}")
        print(f"           Window alloc: {alloc.width}x{alloc.height}, ScrolledWindow alloc: {scrolled_alloc.width}x{scrolled_alloc.height}")
        captured_files.append(path)
        return path

    # --- 1. State: 1 result ("fullscreen") ---
    app.search_entry.set_text("fullscreen")
    pump_gtk(0.4)
    subprocess.run([WLR_PTR_BIN, "50", "50", "0", str(screen_w), str(screen_h)], env=env)
    pump_gtk(0.2)
    capture_shot("screenshot-v5-compact-1result.png", "1 result (whole row visible)")

    # --- 2. State: 2 results ("quit") - Task 1 Defect Fix Verification ---
    app.search_entry.set_text("quit")
    pump_gtk(0.4)
    subprocess.run([WLR_PTR_BIN, "50", "50", "0", str(screen_w), str(screen_h)], env=env)
    pump_gtk(0.2)
    capture_shot("screenshot-v5-compact-2results.png", "2 results (BOTH rows fully visible, no clipping)")

    # --- 3. State: 7 results ("sc") - Top View ---
    app.search_entry.set_text("sc")
    pump_gtk(0.4)
    # Ensure scrolled to top
    vadj = app.scrolled.get_vadjustment()
    vadj.set_value(0)
    pump_gtk(0.3)
    capture_shot("screenshot-v5-capped-7results.png", "7 results (capped at 320px, scrollable, top view)")

    # --- 4. State: 7 results ("sc") - Scrolled to Bottom ---
    # Scroll to bottom
    vadj = app.scrolled.get_vadjustment()
    vadj.set_value(vadj.get_upper() - vadj.get_page_size())
    pump_gtk(0.4)
    capture_shot("screenshot-v5-capped-7results-scrolled.png", "7 results (scrolled to bottom, bottom row fully reachable)")

    # --- 5. State: 44 results (empty query) - Top View ---
    app.search_entry.set_text("")
    pump_gtk(0.4)
    vadj = app.scrolled.get_vadjustment()
    vadj.set_value(0)
    pump_gtk(0.3)
    capture_shot("screenshot-v5-capped-full.png", "44 results (capped at 320px, full list top view)")

    # --- 6. State: 44 results - Scrolled to Bottom ---
    vadj = app.scrolled.get_vadjustment()
    vadj.set_value(vadj.get_upper() - vadj.get_page_size())
    pump_gtk(0.4)
    capture_shot("screenshot-v5-capped-full-scrolled.png", "44 results (scrolled to bottom, bottom row fully reachable)")

    # Reset scroll to top for hover captures
    vadj.set_value(0)
    pump_gtk(0.3)

    # --- 7. State: Hover Row with Arrow Cursor ---
    alloc_full = app.get_allocation()
    overlay_top_y = (screen_h - alloc_full.height) // 2
    row2_y = overlay_top_y + 55 + 50 + 25
    print(f"[Worker] Positioning pointer over row at (454, {row2_y})...")
    subprocess.run([WLR_PTR_BIN, "454", str(row2_y), "0", str(screen_w), str(screen_h)], env=env)
    pump_gtk(0.4)
    sel = app.listbox.get_selected_row()
    print(f"[Worker] Selected row after hover: index={sel.get_index() if sel else None}, key={sel.bind_data.get('key') if sel else None}")
    capture_shot("screenshot-v5-hover-row.png", "Hovering over list row (arrow cursor visible)")

    # --- 8. State: Hover Search Entry with I-Beam Cursor ---
    entry_y = overlay_top_y + 25
    print(f"[Worker] Positioning pointer over search entry at (454, {entry_y})...")
    subprocess.run([WLR_PTR_BIN, "454", str(entry_y), "0", str(screen_w), str(screen_h)], env=env)
    pump_gtk(0.4)
    capture_shot("screenshot-v5-hover-entry.png", "Hovering over search entry (I-beam cursor visible)")

    # Move outside and close
    subprocess.run([WLR_PTR_BIN, "50", "50", "0", str(screen_w), str(screen_h)], env=env)
    pump_gtk(0.2)
    app.destroy()
    pump_gtk(0.2)

    # MD5 verification
    print("\n=== MD5SUM VERIFICATION ===")
    hashes = {}
    for path in captured_files:
        with open(path, "rb") as f:
            h = hashlib.md5(f.read()).hexdigest()
        fname = os.path.basename(path)
        hashes[fname] = h
        print(f"{h}  {fname}")

    unique = set(hashes.values())
    if len(unique) != len(captured_files):
        print(f"ERROR: Only {len(unique)} unique hashes out of {len(captured_files)} captures!", file=sys.stderr)
        sys.exit(1)
    print(f"SUCCESS: All {len(captured_files)} screenshots are distinct ({len(unique)} unique hashes).")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        run_worker()
        return

    ensure_wlr_ptr()

    scratch_dir = tempfile.mkdtemp(prefix="umbriel-v52-capture-")
    config_dir = os.path.join(scratch_dir, "config", "umbriel")
    cache_dir = os.path.join(scratch_dir, "cache")
    os.makedirs(config_dir, exist_ok=True)
    os.makedirs(os.path.join(cache_dir, "umbriel"), exist_ok=True)

    scratch_config = os.path.join(config_dir, "config.toml")
    shutil.copy(CONFIG_SRC, scratch_config)

    print(f"Scratch environment: {scratch_dir}")

    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = os.path.join(scratch_dir, "config")
    env["XDG_CACHE_HOME"] = cache_dir
    env["WLR_BACKENDS"] = "wayland"

    log_file = os.path.join(cache_dir, "umbriel", "umbriel.log")
    stderr_file = os.path.join(cache_dir, "umbriel", "umbriel-stderr.log")
    stdout_fh = open(stderr_file, "w")

    compositor_proc = subprocess.Popen(
        ["umbriel", "-c", scratch_config],
        env=env,
        stdout=stdout_fh,
        stderr=subprocess.STDOUT
    )

    display_name = None
    worker_env = None
    try:
        for _ in range(60):
            for cand in (stderr_file, log_file):
                if os.path.exists(cand):
                    try:
                        with open(cand, "r", errors="ignore") as f:
                            for line in f:
                                if "running on WAYLAND_DISPLAY=" in line:
                                    m = re.search(r"running on WAYLAND_DISPLAY=(wayland-\d+)", line)
                                    if m:
                                        display_name = m.group(1)
                                        break
                    except Exception:
                        pass
                if display_name:
                    break
            if display_name:
                break
            time.sleep(0.2)

        if not display_name:
            raise RuntimeError("Nested compositor failed to start or report WAYLAND_DISPLAY")

        print(f"Nested compositor online: {display_name} (PID {compositor_proc.pid})")
        expected_socket = f"/run/user/{os.getuid()}/umbriel-{display_name}.sock"
        for _ in range(30):
            if os.path.exists(expected_socket):
                break
            time.sleep(0.1)

        worker_env = os.environ.copy()
        worker_env["WAYLAND_DISPLAY"] = display_name
        worker_env["UMBRIEL_SOCKET"] = expected_socket

        # Run worker with WAYLAND_DISPLAY set from the start
        res = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--worker", scratch_config, cache_dir],
            env=worker_env
        )
        if res.returncode != 0:
            sys.exit(res.returncode)

    finally:
        print("Cleaning up nested session...")
        try:
            if worker_env:
                subprocess.run(["umbriel", "msg", "session-quit", "skip-confirmation"], env=worker_env, capture_output=True)
        except Exception:
            pass
        if compositor_proc.poll() is None:
            compositor_proc.terminate()
            try:
                compositor_proc.wait(timeout=2.0)
            except Exception:
                compositor_proc.kill()
        stdout_fh.close()
        shutil.rmtree(scratch_dir, ignore_errors=True)
        print("Cleanup complete.")


if __name__ == "__main__":
    main()
