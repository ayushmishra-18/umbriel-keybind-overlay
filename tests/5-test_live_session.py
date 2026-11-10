#!/usr/bin/env python3
"""Live integration tests against the nested Umbriel session.

Covers all review findings and milestone requirements:
1. Fix #1: Safety gate refusal on mismatched UMBRIEL_SOCKET.
2. Fix #4: Submap-after execution (flags.submap == "reset" on row "1").
3. Fix #5: Single-instance advisory lock enforcement.
4. Fix #6: Status clearing on new search text.
5. The Milestone: Visible window control with Alacritty (before/after maximize & pin).
"""

import json
import os
import subprocess
import sys
import time
import unittest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib

import overlay


class TestLiveOverlayFixes(unittest.TestCase):
    SCRATCH_CONFIG = os.environ.get("TEST_UMBRIEL_CONFIG", "/tmp/umbriel-test/config/config.toml")
    SCRATCH_CACHE = os.environ.get("TEST_UMBRIEL_CACHE", "/tmp/umbriel-test/cache")

    @classmethod
    def setUpClass(cls):
        # 1. Verify nested compositor process is alive
        chk = subprocess.run(
            ["pgrep", "-af", f"umbriel -c {cls.SCRATCH_CONFIG}"],
            capture_output=True,
            text=True
        )
        if chk.returncode != 0 or not chk.stdout.strip():
            raise unittest.SkipTest("Nested Umbriel session is not running!")

        # 2. Verify WAYLAND_DISPLAY is set
        if not os.environ.get("WAYLAND_DISPLAY"):
            raise unittest.SkipTest("WAYLAND_DISPLAY is not set")

    def _pump_events(self, duration=0.2):
        end = time.time() + duration
        while time.time() < end:
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            time.sleep(0.01)

    def test_fix1_wrong_socket_refusal(self):
        """Fix #1 Regression: with scratch session running, launch overlay with a
        deliberately wrong UMBRIEL_SOCKET value; Enter on an executable row must refuse
        with a clear message.
        """
        env = os.environ.copy()
        orig_socket = env.get("UMBRIEL_SOCKET")
        os.environ["UMBRIEL_SOCKET"] = "/run/user/1000/umbriel-bogus.sock"

        try:
            app = overlay.KeybindOverlay(
                self.SCRATCH_CONFIG,
                scratch_config=self.SCRATCH_CONFIG,
                scratch_cache=self.SCRATCH_CACHE
            )
            self.addCleanup(app.destroy)
            app.show_all()
            self._pump_events(0.2)

            # Pick an executable action row (e.g. window-toggle-maximize)
            max_bind = next(b for b in app.binds_data if b.get("action") == "window-toggle-maximize")
            app._execute_bind(max_bind)
            self._pump_events(0.2)

            status_text = app.status_label.get_text()
            print("\n[Fix 1 Test Output] Status on wrong socket:", status_text)
            self.assertIn("Safety gate: UMBRIEL_SOCKET path mismatch", status_text)
            self.assertIn("umbriel-bogus.sock", status_text)

            app.destroy()
            self._pump_events(0.1)
        finally:
            if orig_socket:
                os.environ["UMBRIEL_SOCKET"] = orig_socket
            else:
                os.environ.pop("UMBRIEL_SOCKET", None)

    def test_fix4_submap_after_execution(self):
        """Fix #4 Regression: enter screencapture (Alt+S in session), execute the '1' row
        from the overlay, then umbriel submap --json must report no active submap (reset).
        """
        env = os.environ.copy()

        # Enter screencapture submap
        subprocess.run(["umbriel", "msg", "submap", "screencapture"], env=env, check=True)
        time.sleep(0.2)

        # Confirm submap active in compositor
        sub_check = subprocess.run(["umbriel", "submap", "--json"], env=env, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(sub_check.stdout.strip()), "screencapture")

        # Create overlay
        app = overlay.KeybindOverlay(
            self.SCRATCH_CONFIG,
            scratch_config=self.SCRATCH_CONFIG,
            scratch_cache=self.SCRATCH_CACHE
        )
        self.addCleanup(app.destroy)
        app.show_all()
        self._pump_events(0.3)

        # Find row '1' (which carries flags.submap == 'reset')
        one_bind = next(b for b in app.binds_data if b.get("key") == "1")
        self.assertEqual(one_bind.get("flags", {}).get("submap"), "reset")

        # Execute row '1'
        quit_called = []
        orig_quit = Gtk.main_quit
        Gtk.main_quit = lambda: quit_called.append(True)
        try:
            app._execute_bind(one_bind)
            self._pump_events(0.3)
            self.assertTrue(quit_called, "Overlay should close after executing submap member")
        finally:
            Gtk.main_quit = orig_quit

        # Verify umbriel submap --json reports no active submap
        sub_after = subprocess.run(["umbriel", "submap", "--json"], env=env, capture_output=True, text=True, check=True)
        sub_data = json.loads(sub_after.stdout.strip())
        print("\n[Fix 4 Test Output] Submap state after executing '1':", sub_data)
        self.assertIn(sub_data, (None, "default"), "Submap must be reset after executing row '1'")

        app.destroy()
        self._pump_events(0.1)

    def test_fix5_single_instance(self):
        """Fix #5 Regression: two launches never produce two surfaces; second launch exits gracefully."""
        env = os.environ.copy()
        display = env.get("WAYLAND_DISPLAY", "wayland-0")

        # Launch instance 1
        app = overlay.KeybindOverlay(
            self.SCRATCH_CONFIG,
            scratch_config=self.SCRATCH_CONFIG,
            scratch_cache=self.SCRATCH_CACHE
        )
        self.addCleanup(app.destroy)
        # Lock acquired by overlay.acquire_single_instance_lock
        lock = overlay.acquire_single_instance_lock(display)
        app.show_all()
        self._pump_events(0.2)

        # Launch instance 2 via subprocess
        res = subprocess.run(
            [sys.executable, os.path.join(PROJECT_DIR, "overlay.py"), "-c", self.SCRATCH_CONFIG],
            env=env,
            capture_output=True,
            text=True
        )
        print("\n[Fix 5 Test Output] Second instance exit code:", res.returncode)
        print("[Fix 5 Test Output] Second instance stderr:", res.stderr.strip())
        self.assertEqual(res.returncode, 0)
        self.assertIn("Another instance of umbriel-overlay is already running", res.stderr)

        # Verify umbriel layers only has 1 overlay surface
        layers_res = subprocess.run(["umbriel", "layers", "--json"], env=env, capture_output=True, text=True, check=True)
        layers = [l for l in json.loads(layers_res.stdout.strip()) if l.get("namespace") == "umbriel-keybind-overlay"]
        print("[Fix 5 Test Output] Active overlay layer count:", len(layers))
        self.assertEqual(len(layers), 1)

        app.destroy()
        self._pump_events(0.1)

    def test_fix6_status_clearing(self):
        """Fix #6 Regression: after a refusal, typing a new query clears the warning."""
        app = overlay.KeybindOverlay(
            self.SCRATCH_CONFIG,
            scratch_config=self.SCRATCH_CONFIG,
            scratch_cache=self.SCRATCH_CACHE
        )
        self.addCleanup(app.destroy)
        app.show_all()
        self._pump_events(0.2)

        # Refusal on session-quit
        quit_bind = next(b for b in app.binds_data if "session-quit" in b.get("action", ""))
        app._execute_bind(quit_bind)
        self._pump_events(0.2)

        refusal_text = app.status_label.get_text()
        print("\n[Fix 6 Test Output] Refusal text:", refusal_text)
        self.assertIn("excluded from overlay execution for safety", refusal_text)

        # Type new query
        app.search_entry.set_text("max")
        self._pump_events(0.2)

        cleared_text = app.status_label.get_text()
        print("[Fix 6 Test Output] Status after typing 'max':", cleared_text)
        self.assertNotIn("excluded", cleared_text)
        self.assertNotIn("⚠", cleared_text)

        app.destroy()
        self._pump_events(0.1)

    def test_milestone_visible_window_control(self):
        """THE MILESTONE: Prove visible window control using an already-installed terminal (alacritty).
        1. Spawn alacritty via umbriel msg spawn alacritty.
        2. Verify 1 window exists in umbriel windows --json.
        3. Enter window-toggle-maximize: verify maximized: true in JSON and screenshot.
        4. Enter window-toggle-pinned: verify pinned: true in JSON and screenshot.
        5. Cleanly close window.
        """
        env = os.environ.copy()

        # Step a & b: Ensure clean start and spawn alacritty
        subprocess.run(["umbriel", "msg", "cheatsheet-close"], env=env)
        time.sleep(0.2)

        # Spawn exactly one terminal
        print("\n[Milestone] Spawning alacritty via umbriel msg spawn alacritty...")
        subprocess.run(["umbriel", "msg", "spawn", "alacritty"], env=env, check=True)
        time.sleep(1.2)

        # Verify window exists
        win_res = subprocess.run(["umbriel", "windows", "--json"], env=env, capture_output=True, text=True, check=True)
        windows = json.loads(win_res.stdout.strip())
        self.assertEqual(len(windows), 1, "Exactly one window must be present")
        w0 = windows[0]
        self.assertEqual(w0.get("app_id"), "Alacritty")
        self.assertFalse(w0.get("maximized"), "Initial window must not be maximized")
        self.assertFalse(w0.get("pinned"), "Initial window must not be pinned")
        print("[Milestone] Initial window JSON:", json.dumps(w0, indent=2))

        # Capture initial screenshot
        initial_shot = os.path.join(PROJECT_DIR, "screenshot-milestone-initial.png")
        subprocess.run(["grim", initial_shot], env=env, check=True)
        print("[Milestone] Saved screenshot:", initial_shot)

        # Step c: From overlay, trigger window-toggle-maximize
        app = overlay.KeybindOverlay(
            self.SCRATCH_CONFIG,
            scratch_config=self.SCRATCH_CONFIG,
            scratch_cache=self.SCRATCH_CACHE
        )
        self.addCleanup(app.destroy)
        app.show_all()
        self._pump_events(0.2)

        max_bind = next(b for b in app.binds_data if b.get("action") == "window-toggle-maximize" and b.get("context") == "default")
        app._execute_bind(max_bind)
        self._pump_events(0.3)
        time.sleep(0.4)

        # Verify maximized == True
        win_res_max = subprocess.run(["umbriel", "windows", "--json"], env=env, capture_output=True, text=True, check=True)
        windows_max = json.loads(win_res_max.stdout.strip())
        w_max = windows_max[0]
        print("[Milestone] Post-maximize window JSON:", json.dumps(w_max, indent=2))
        self.assertTrue(w_max.get("maximized"), "Window must be maximized")

        # Capture maximized screenshot
        max_shot = os.path.join(PROJECT_DIR, "screenshot-milestone-maximized.png")
        subprocess.run(["grim", max_shot], env=env, check=True)
        print("[Milestone] Saved screenshot:", max_shot)

        # Step d: From overlay, trigger window-toggle-pinned
        pin_bind = next(b for b in app.binds_data if b.get("action") == "window-toggle-pinned")
        app._execute_bind(pin_bind)
        self._pump_events(0.3)
        time.sleep(0.4)

        # Verify pinned == True
        win_res_pin = subprocess.run(["umbriel", "windows", "--json"], env=env, capture_output=True, text=True, check=True)
        windows_pin = json.loads(win_res_pin.stdout.strip())
        w_pin = windows_pin[0]
        print("[Milestone] Post-pinned window JSON:", json.dumps(w_pin, indent=2))
        self.assertTrue(w_pin.get("pinned"), "Window must be pinned")

        # Capture pinned screenshot
        pin_shot = os.path.join(PROJECT_DIR, "screenshot-milestone-pinned.png")
        subprocess.run(["grim", pin_shot], env=env, check=True)
        print("[Milestone] Saved screenshot:", pin_shot)

        # Step e: Close window
        subprocess.run(["umbriel", "msg", "window-close"], env=env, check=True)
        time.sleep(0.3)
        win_final = subprocess.run(["umbriel", "windows", "--json"], env=env, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(win_final.stdout.strip()), [], "Windows list must be empty after close")
        print("[Milestone] Window closed cleanly.")

        app.destroy()
        self._pump_events(0.1)


if __name__ == "__main__":
    unittest.main()
