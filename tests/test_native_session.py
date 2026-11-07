#!/usr/bin/env python3
"""Integration tests for Round 4: Native session (default launch without -c) and
strengthened endpoint-process association.
"""

import json
import os
import re
import shutil
import socket
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
from gi.repository import Gtk, Gdk, GLib

import overlay


class TestNativeSessionSupport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 1. Probe for installed terminal
        cls.term_bin = None
        for t in ["alacritty", "foot", "kitty", "wezterm"]:
            if shutil.which(t):
                cls.term_bin = t
                break
        if not cls.term_bin:
            raise unittest.SkipTest("No supported terminal emulator found")

        # 2. Setup scratch directories
        cls.scratch_dir = tempfile.mkdtemp(prefix="umbriel-v4-test-")
        cls.config_dir = os.path.join(cls.scratch_dir, "config", "umbriel")
        cls.cache_dir = os.path.join(cls.scratch_dir, "cache")
        os.makedirs(cls.config_dir, exist_ok=True)
        os.makedirs(os.path.join(cls.cache_dir, "umbriel"), exist_ok=True)

        # 3. Create realistic scratch config at $XDG_CONFIG_HOME/umbriel/config.toml
        cls.config_path = os.path.join(cls.config_dir, "config.toml")
        with open(cls.config_path, "w") as f:
            f.write(f"""# Round 4 Native Session Test Config
[general]
mod_key = "alt"

[keybinds]
"Mod+Escape" = {{ action = "session-quit skip-confirmation", repeat = false }}
"Mod+Return" = "spawn {cls.term_bin}"
"Mod+B" = {{ action = "window-toggle-maximize" }}
"Mod+Q" = "window-close"
""")

        # 4. Launch nested Umbriel compositor session WITHOUT -c
        cls.env = os.environ.copy()
        cls.env["XDG_CONFIG_HOME"] = os.path.join(cls.scratch_dir, "config")
        cls.env["XDG_CACHE_HOME"] = cls.cache_dir
        cls.env["WLR_BACKENDS"] = "wayland"

        cls.stdout_log_path = os.path.join(cls.cache_dir, "umbriel", "umbriel-stderr.log")
        cls.stdout_fh = open(cls.stdout_log_path, "w")
        cls.compositor_proc = subprocess.Popen(
            ["umbriel"],
            env=cls.env,
            stdout=cls.stdout_fh,
            stderr=subprocess.STDOUT
        )

        # 5. Wait for compositor to report display name
        cls.display_name = None
        stderr_log = os.path.join(cls.cache_dir, "umbriel", "umbriel-stderr.log")
        stdout_log = os.path.join(cls.cache_dir, "umbriel", "umbriel.log")

        for _ in range(60):
            for log_file in (cls.stdout_log_path, stderr_log, stdout_log):
                if os.path.exists(log_file):
                    try:
                        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                            for line in f:
                                if "running on WAYLAND_DISPLAY=" in line:
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

        # Wait for socket availability
        for _ in range(30):
            if os.path.exists(cls.expected_socket):
                break
            time.sleep(0.1)

        # Set environment for tests
        os.environ["WAYLAND_DISPLAY"] = cls.display_name
        os.environ["UMBRIEL_SOCKET"] = cls.expected_socket

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "display_name") and hasattr(cls, "expected_socket") and os.path.exists(cls.expected_socket):
            subprocess.run(
                ["umbriel", "msg", "session-quit", "skip-confirmation"],
                env=os.environ,
                capture_output=True
            )
        if hasattr(cls, "compositor_proc") and cls.compositor_proc.poll() is None:
            cls.compositor_proc.terminate()
            try:
                cls.compositor_proc.wait(timeout=2.0)
            except Exception:
                cls.compositor_proc.kill()
        if hasattr(cls, "stdout_fh") and not cls.stdout_fh.closed:
            cls.stdout_fh.close()
        if hasattr(cls, "expected_socket") and os.path.exists(cls.expected_socket):
            try:
                os.unlink(cls.expected_socket)
            except Exception:
                pass
        if hasattr(cls, "scratch_dir") and os.path.exists(cls.scratch_dir):
            shutil.rmtree(cls.scratch_dir, ignore_errors=True)
        time.sleep(0.5)

    def _pump_events(self, duration=0.2):
        end = time.time() + duration
        while time.time() < end:
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            time.sleep(0.01)

    def test_01_positive_default_launch_gate(self):
        """Default-launch simulation: verify that the gate successfully matches the
        compositor process launched without -c via its XDG_CONFIG_HOME.
        """
        safe, msg = overlay.verify_safety_gate(
            self.config_path,
            scratch_config=self.config_path,
            scratch_cache=self.cache_dir
        )
        print("\n[Test 1] Positive default-launch gate verification:", safe, msg)
        self.assertTrue(safe, f"Expected safety gate to pass, got: {msg}")
        self.assertIn(f"Verified instance PID {self.compositor_proc.pid} on {self.display_name}", msg)

    def test_02_negative_same_basename_different_dir(self):
        """Negative test: UMBRIEL_SOCKET pointing to a same-basename path in a
        DIFFERENT directory must refuse (canonical path comparison).
        """
        orig_socket = os.environ.get("UMBRIEL_SOCKET")
        fake_dir = tempfile.mkdtemp(prefix="fake-sock-dir-")
        fake_socket = os.path.join(fake_dir, f"umbriel-{self.display_name}.sock")
        # Touch or create fake socket file
        with open(fake_socket, "w") as f:
            f.write("fake")

        try:
            os.environ["UMBRIEL_SOCKET"] = fake_socket
            safe, msg = overlay.verify_safety_gate(
                self.config_path,
                scratch_config=self.config_path,
                scratch_cache=self.cache_dir
            )
            print("\n[Test 2] Same-basename different-dir refusal:", safe, msg)
            self.assertFalse(safe)
            self.assertIn("Safety gate: UMBRIEL_SOCKET path mismatch", msg)
            self.assertIn(fake_socket, msg)
        finally:
            if orig_socket:
                os.environ["UMBRIEL_SOCKET"] = orig_socket
            shutil.rmtree(fake_dir, ignore_errors=True)

    def test_03_negative_socket_different_process(self):
        """Negative test: UMBRIEL_SOCKET pointing to a socket that exists but
        belongs to a different process must refuse.
        """
        orig_socket = os.environ.get("UMBRIEL_SOCKET")
        # Point to host compositor socket if available, or another socket
        host_socket = f"/run/user/{os.getuid()}/wayland-1"
        if not os.path.exists(host_socket):
            runtime_dir = f"/run/user/{os.getuid()}"
            candidates = [
                os.path.join(runtime_dir, s) for s in os.listdir(runtime_dir)
                if s.endswith(".sock") and not s.endswith(f"umbriel-{self.display_name}.sock")
            ]
            host_socket = candidates[0] if candidates else None

        if not host_socket or not os.path.exists(host_socket):
            self.skipTest("No alternate process socket available to test")

        try:
            os.environ["UMBRIEL_SOCKET"] = host_socket
            safe, msg = overlay.verify_safety_gate(
                self.config_path,
                scratch_config=self.config_path,
                scratch_cache=self.cache_dir
            )
            print("\n[Test 3] Socket belonging to different process refusal:", safe, msg)
            self.assertFalse(safe)
            self.assertTrue(
                "UMBRIEL_SOCKET path mismatch" in msg or "not owned by compositor PID" in msg,
                f"Unexpected refusal message: {msg}"
            )
        finally:
            if orig_socket:
                os.environ["UMBRIEL_SOCKET"] = orig_socket

    def test_03b_negative_socket_unowned_by_matched_process(self):
        """Negative test: UMBRIEL_SOCKET matches expected path in runtime dir but
        its inode in /proc/net/unix is owned by a different process (not the matched PID).
        Must be refused by inode ownership verification.
        """
        orig_runtime = os.environ.get("XDG_RUNTIME_DIR")
        orig_socket = os.environ.get("UMBRIEL_SOCKET")
        mock_runtime = tempfile.mkdtemp(prefix="mock-runtime-")
        mock_socket_path = os.path.join(mock_runtime, f"umbriel-{self.display_name}.sock")

        # Bind a listening socket in current process
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(mock_socket_path)
        srv.listen(1)

        try:
            os.environ["XDG_RUNTIME_DIR"] = mock_runtime
            os.environ["UMBRIEL_SOCKET"] = mock_socket_path
            safe, msg = overlay.verify_safety_gate(
                self.config_path,
                scratch_config=self.config_path,
                scratch_cache=self.cache_dir
            )
            print("\n[Test 3b] Unowned socket inode refusal:", safe, msg)
            self.assertFalse(safe)
            self.assertIn("is not owned by compositor PID", msg)
            self.assertIn(str(self.compositor_proc.pid), msg)
        finally:
            srv.close()
            if orig_runtime:
                os.environ["XDG_RUNTIME_DIR"] = orig_runtime
            else:
                os.environ.pop("XDG_RUNTIME_DIR", None)
            if orig_socket:
                os.environ["UMBRIEL_SOCKET"] = orig_socket
            else:
                os.environ.pop("UMBRIEL_SOCKET", None)
            shutil.rmtree(mock_runtime, ignore_errors=True)

    def test_04_negative_no_matching_process(self):
        """Negative test: un-launched config path must refuse."""
        unlaunched_config = os.path.join(self.scratch_dir, "unlaunched.toml")
        with open(unlaunched_config, "w") as f:
            f.write("[general]\n")

        safe, msg = overlay.verify_safety_gate(
            unlaunched_config,
            scratch_config=unlaunched_config,
            scratch_cache=self.cache_dir
        )
        print("\n[Test 4] No matching process refusal:", safe, msg)
        self.assertFalse(safe)
        self.assertIn("Safety gate: no running compositor found", msg)

    def test_05_live_window_control_execution(self):
        """Live evidence for positive case: spawn probed terminal, overlay-driven
        window-toggle-maximize, umbriel windows --json before/after.
        """
        env = os.environ.copy()
        # Spawn terminal
        print(f"\n[Test 5] Spawning terminal: {self.term_bin}")
        subprocess.run(["umbriel", "msg", "spawn", self.term_bin], env=env, check=True)
        time.sleep(1.2)

        # Before maximize state
        win_before_res = subprocess.run(
            ["umbriel", "windows", "--json"],
            env=env,
            capture_output=True,
            text=True,
            check=True
        )
        windows_before = json.loads(win_before_res.stdout.strip())
        self.assertGreaterEqual(len(windows_before), 1)
        win_before = windows_before[0]
        self.assertFalse(win_before.get("maximized"), "Window should initially be unmaximized")
        print("[Test 5] Windows before maximize:", json.dumps(win_before, indent=2))

        # Launch overlay and trigger window-toggle-maximize
        app = overlay.KeybindOverlay(
            self.config_path,
            scratch_config=self.config_path,
            scratch_cache=self.cache_dir
        )
        app.show_all()
        self._pump_events(0.4)

        max_bind = next((b for b in app.binds_data if b.get("action") == "window-toggle-maximize"), None)
        self.assertIsNotNone(max_bind, "window-toggle-maximize binding must be found")

        # Execute through overlay
        quit_called = []
        orig_quit = Gtk.main_quit
        Gtk.main_quit = lambda: quit_called.append(True)
        try:
            app._execute_bind(max_bind)
            self._pump_events(0.4)
            time.sleep(0.4)
        finally:
            Gtk.main_quit = orig_quit

        # After maximize state
        win_after_res = subprocess.run(
            ["umbriel", "windows", "--json"],
            env=env,
            capture_output=True,
            text=True,
            check=True
        )
        windows_after = json.loads(win_after_res.stdout.strip())
        win_after = windows_after[0]
        print("[Test 5] Windows after maximize:", json.dumps(win_after, indent=2))
        self.assertTrue(win_after.get("maximized"), "Window must be maximized after overlay execution")
        self.assertGreater(win_after.get("w"), win_before.get("w"), "Maximized window width must increase")

        # Screenshot capture
        screenshot_path = os.path.join(PROJECT_DIR, "screenshot-v4-default-launch-maximized.png")
        subprocess.run(["grim", screenshot_path], env=env, check=True)
        print(f"[Test 5] Saved screenshot: {screenshot_path}")

        # Clean up window
        subprocess.run(["umbriel", "msg", "window-close"], env=env, check=True)
        app.destroy()
        self._pump_events(0.1)


if __name__ == "__main__":
    unittest.main()
