#!/usr/bin/env python3
"""Tests for UI Refinement Round (v5): Sizing, Pointer, and Mouse Interaction."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, Gdk, GLib, GtkLayerShell

import overlay


WLR_PTR_BIN = os.path.join(PROJECT_DIR, "tests", "wlr_ptr")


def ensure_wlr_ptr():
    if not os.path.exists(WLR_PTR_BIN):
        subprocess.run(["make", "-C", os.path.join(PROJECT_DIR, "tests")], check=True)


def pump_gtk_events(duration=0.3):
    ctx = GLib.MainContext.default()
    end = time.time() + duration
    while time.time() < end:
        while ctx.pending():
            ctx.iteration(False)
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.01)


class TestUIRefinement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 1. Setup isolated scratch directory for nested session
        cls.scratch_dir = tempfile.mkdtemp(prefix="umbriel-v5-test-")
        cls.config_dir = os.path.join(cls.scratch_dir, "config", "umbriel")
        cls.cache_dir = os.path.join(cls.scratch_dir, "cache")
        os.makedirs(cls.config_dir, exist_ok=True)
        os.makedirs(os.path.join(cls.cache_dir, "umbriel"), exist_ok=True)

        # 2. Typical multi-row config
        cls.config_path = os.path.join(cls.config_dir, "config.toml")
        with open(cls.config_path, "w") as f:
            f.write("""# Round 5 Test Configuration
[general]
mod_key = "alt"

[keybinds]
"Mod+Return" = "spawn alacritty"
"Mod+B" = { action = "window-toggle-maximize" }
"Mod+P" = { action = "window-toggle-pinned" }
"Mod+Q" = "window-close"
"Mod+1" = "workspace-switch 1"
"Mod+2" = "workspace-switch 2"
"Mod+3" = "workspace-switch 3"
"Mod+4" = "workspace-switch 4"
"Mod+5" = "workspace-switch 5"
"Mod+6" = "workspace-switch 6"
"Mod+7" = "workspace-switch 7"
"Mod+8" = "workspace-switch 8"
"Mod+9" = "workspace-switch 9"
"Mod+0" = "workspace-switch 10"
"Mod+F" = "window-focus next"
"Mod+H" = "window-focus-left"
"Mod+J" = "window-focus-down"
"Mod+K" = "window-focus-up"
"Mod+L" = "window-focus-right"
"Mod+Escape" = { action = "session-quit skip-confirmation", repeat = false }
"Mod+C" = { action = "config-reload" }
""")

        # 3. Wait for any previous compositor process to exit completely
        for _ in range(30):
            try:
                subprocess.check_output(["pgrep", "-a", "umbriel"])
                time.sleep(0.1)
            except subprocess.CalledProcessError:
                break

        # Launch nested compositor
        cls.env = os.environ.copy()
        cls.env["XDG_CONFIG_HOME"] = os.path.join(cls.scratch_dir, "config")
        cls.env["XDG_CACHE_HOME"] = cls.cache_dir
        cls.env["WLR_BACKENDS"] = "wayland"

        cls.stdout_log = os.path.join(cls.cache_dir, "umbriel", "umbriel-stderr.log")
        cls.stdout_fh = open(cls.stdout_log, "w")
        cls.compositor_proc = subprocess.Popen(
            ["umbriel"],
            env=cls.env,
            stdout=cls.stdout_fh,
            stderr=subprocess.STDOUT
        )

        cls.display_name = None
        stdout_log_main = os.path.join(cls.cache_dir, "umbriel", "umbriel.log")
        for _ in range(60):
            for log_cand in (cls.stdout_log, stdout_log_main):
                if os.path.exists(log_cand):
                    try:
                        with open(log_cand, "r", errors="ignore") as f:
                            for line in f:
                                if "running on WAYLAND_DISPLAY=" in line:
                                    import re
                                    m = re.search(r"running on WAYLAND_DISPLAY=(wayland-\d+)", line)
                                    if m:
                                        cls.display_name = m.group(1)
                                        break
                    except Exception:
                        pass
                if cls.display_name:
                    break
            if cls.display_name:
                break
            time.sleep(0.2)

        if not cls.display_name:
            cls.tearDownClass()
            raise unittest.SkipTest("Nested Umbriel compositor could not be launched (requires host Wayland display access)")

        cls.expected_socket = f"/run/user/{os.getuid()}/umbriel-{cls.display_name}.sock"
        for _ in range(30):
            if os.path.exists(cls.expected_socket):
                break
            time.sleep(0.1)

        os.environ["WAYLAND_DISPLAY"] = cls.display_name
        os.environ["UMBRIEL_SOCKET"] = cls.expected_socket

        # Close native cheatsheet to prevent layer occlusion
        subprocess.run(["umbriel", "msg", "cheatsheet-close"], env=os.environ, capture_output=True)
        ensure_wlr_ptr()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "compositor_proc") and cls.compositor_proc:
            try:
                cls.compositor_proc.terminate()
                cls.compositor_proc.wait(timeout=2)
            except Exception:
                try:
                    cls.compositor_proc.kill()
                except Exception:
                    pass
        if hasattr(cls, "stdout_fh") and cls.stdout_fh:
            cls.stdout_fh.close()
        if hasattr(cls, "expected_socket") and os.path.exists(cls.expected_socket):
            try:
                os.unlink(cls.expected_socket)
            except Exception:
                pass
        if hasattr(cls, "scratch_dir") and os.path.exists(cls.scratch_dir):
            shutil.rmtree(cls.scratch_dir, ignore_errors=True)
        time.sleep(0.5)

    def test_01_cursor_theme_fallback(self):
        """Verify ensure_valid_cursor_theme detects missing themes and applies fallback."""
        settings = Gtk.Settings.get_default()
        overlay.ensure_valid_cursor_theme()
        active_theme = settings.get_property("gtk-cursor-theme-name")
        self.assertIn(active_theme, ["Adwaita", "breeze", "Bibata-Modern-Classic", "default"])
        # Verify theme directory exists on disk
        found = False
        for base in ["/usr/share/icons", os.path.expanduser("~/.icons"), os.path.expanduser("~/.local/share/icons")]:
            if os.path.isdir(os.path.join(base, active_theme)):
                found = True
                break
        self.assertTrue(found, f"Active theme {active_theme} must physically exist on disk")

    def test_02_compact_sizing_extremes(self):
        """Test shrink-to-fit sizing: 1-result vs full 44-bind example config view."""
        typical_cfg = os.path.join(PROJECT_DIR, "examples", "typical-desktop.toml")
        app = overlay.KeybindOverlay(typical_cfg, scratch_config=typical_cfg, scratch_cache=self.cache_dir)
        app.show_all()
        pump_gtk_events(0.4)

        # Full view allocation (44 binds)
        alloc_full = app.get_allocation()
        print(f"[Test 02] Full view (44 rows) allocation: {alloc_full.width}x{alloc_full.height}")
        self.assertEqual(alloc_full.width, 620)
        self.assertLessEqual(alloc_full.height, 460, "Full view height should be capped at max ~440px")
        self.assertGreater(alloc_full.height, 350, "Full view height should accommodate visible list")

        # ScrolledWindow should be scrollable with full items
        vadj = app.scrolled.get_vadjustment()
        self.assertGreater(vadj.get_upper(), vadj.get_page_size(), "ScrolledWindow must be scrollable with 44 rows")

        # Filter to 1 result ("fullscreen")
        app.search_entry.set_text("fullscreen")
        pump_gtk_events(0.4)
        alloc_1 = app.get_allocation()
        print(f"[Test 02] 1-result view allocation: {alloc_1.width}x{alloc_1.height}")
        self.assertEqual(alloc_1.width, 620)
        self.assertLess(alloc_1.height, 200, "1-result view must be a compact popup (< 200px)")
        self.assertEqual(len(app.current_filtered_binds), 1)

        # Filter down to 2 results ("quit") - verify no row clipping
        app.search_entry.set_text("quit")
        pump_gtk_events(0.4)
        alloc_2 = app.get_allocation()
        print(f"[Test 02] 2-result view allocation: {alloc_2.width}x{alloc_2.height}")
        self.assertEqual(alloc_2.width, 620)
        self.assertLess(alloc_2.height, 250, "2-result view must be a compact popup (< 250px)")
        self.assertEqual(len(app.current_filtered_binds), 2)
        # Verify ScrolledWindow height accommodates both rows
        scrolled_alloc_2 = app.scrolled.get_allocation()
        row0 = app.listbox.get_row_at_index(0)
        row1 = app.listbox.get_row_at_index(1)
        r0_h = row0.get_allocation().height
        r1_h = row1.get_allocation().height
        self.assertGreaterEqual(scrolled_alloc_2.height, r0_h + r1_h, "ScrolledWindow must fully contain both rows without clipping")

        # Filter to 0 results ("nonexistent")
        app.search_entry.set_text("nonexistentxyz")
        pump_gtk_events(0.4)
        alloc_0 = app.get_allocation()
        print(f"[Test 02] 0-result view allocation: {alloc_0.width}x{alloc_0.height}")
        self.assertEqual(alloc_0.width, 620)
        self.assertLess(alloc_0.height, 220, "0-result view must be a compact popup (< 220px)")

        app.destroy()
        pump_gtk_events(0.1)

    def test_03_mouse_hover_and_keyboard_sync(self):
        """Verify mouse hover updates selection and keyboard navigation stays in sync."""
        app = overlay.KeybindOverlay(self.config_path, scratch_config=self.config_path, scratch_cache=self.cache_dir)
        app.show_all()
        pump_gtk_events(0.4)

        # Initial selection is row 0
        sel = app.listbox.get_selected_row()
        self.assertIsNotNone(sel)
        self.assertEqual(sel.get_index(), 0)

        # Simulate pointer motion over row index 3
        row3 = app.listbox.get_row_at_index(3)
        self.assertIsNotNone(row3)
        alloc3 = row3.get_allocation()
        hover_y = alloc3.y + alloc3.height // 2

        # Create motion event
        ev = Gdk.EventMotion()
        ev.type = Gdk.EventType.MOTION_NOTIFY
        ev.window = app.listbox.get_window()
        ev.x = 100.0
        ev.y = float(hover_y)
        app._on_listbox_motion(app.listbox, ev)
        pump_gtk_events(0.1)

        sel = app.listbox.get_selected_row()
        self.assertEqual(sel.get_index(), 3, "Hover over row 3 must move selection to row 3")

        # Now test keyboard down arrow from row 3 -> row 4
        key_ev = Gdk.EventKey()
        key_ev.type = Gdk.EventType.KEY_PRESS
        key_ev.keyval = Gdk.KEY_Down
        key_ev.state = Gdk.ModifierType(0)
        app._on_window_key_press(app, key_ev)
        pump_gtk_events(0.1)

        sel = app.listbox.get_selected_row()
        self.assertEqual(sel.get_index(), 4, "Keyboard down arrow must move selection from hover row 3 to row 4")

        # Hover back to row 1
        row1 = app.listbox.get_row_at_index(1)
        alloc1 = row1.get_allocation()
        ev.y = float(alloc1.y + alloc1.height // 2)
        app._on_listbox_motion(app.listbox, ev)
        pump_gtk_events(0.1)

        sel = app.listbox.get_selected_row()
        self.assertEqual(sel.get_index(), 1, "Hover over row 1 must move selection back to row 1")

        # Verify focus remains on search entry
        self.assertTrue(app.search_entry.has_focus(), "Search entry must maintain focus for keyboard typing")

        app.destroy()
        pump_gtk_events(0.1)

    def test_04_single_click_action_and_safety(self):
        """Verify single click activates rows with identical safety gate and refusal behavior."""
        app = overlay.KeybindOverlay(self.config_path, scratch_config=self.config_path, scratch_cache=self.cache_dir)
        app.show_all()
        pump_gtk_events(0.4)

        # 1. Unsafe row: "session-quit"
        app.search_entry.set_text("quit")
        pump_gtk_events(0.2)
        quit_row = app.listbox.get_selected_row()
        self.assertIsNotNone(quit_row)
        self.assertIn("session-quit", quit_row.bind_data.get("action", ""))

        # Trigger row activation on quit row
        app._on_row_activated(app.listbox, quit_row)
        pump_gtk_events(0.2)

        # Verify refusal message in status bar
        status_text = app.status_label.get_text()
        self.assertIn("excluded from overlay execution for safety", status_text)

        app.destroy()
        pump_gtk_events(0.1)


if __name__ == "__main__":
    unittest.main()
