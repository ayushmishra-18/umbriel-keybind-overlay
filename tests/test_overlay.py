#!/usr/bin/env python3
"""Unit tests for the Umbriel Keybind Overlay."""

import os
import shlex
import subprocess
import sys
import threading
import unittest
import unittest.mock

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gtk, Gdk

# Ensure project directory is in sys.path
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import umbriel_keys
import overlay
from overlay import (
    split_action,
    is_executable_action,
    format_action_title,
    FORBIDDEN_FAMILIES,
    compute_target_width,
)


class TestActionSafety(unittest.TestCase):
    def test_forbidden_families_detection(self):
        blocked = [
            "session-quit skip-confirmation",
            "session-quit",
            "session-lock",
            "config-replace /path/to/config.toml",
            "config-reload",
            "output-create HDMI-1 1920x1080@60",
            "output-destroy HDMI-1",
            "output-disable eDP-1",
            "output-enable eDP-1",
            "input-capture-revoke all",
            "input-capture-revoke 12",
            "dpms-off",
            "dpms-on",
        ]
        for act in blocked:
            with self.subTest(action=act):
                self.assertFalse(
                    is_executable_action(act),
                    f"Action '{act}' should NOT be executable!"
                )

    def test_dpms_action_family_blocked(self):
        """is_executable_action returns False for 'dpms-off' and 'dpms-on'
        (use these exact hyphenated action names), and still returns True for ordinary actions like 'window-close'.
        """
        self.assertFalse(is_executable_action("dpms-off"))
        self.assertFalse(is_executable_action("dpms-on"))
        self.assertTrue(is_executable_action("window-close"))

    def test_unbalanced_quote_action_not_executable(self):
        """is_executable_action('window-open \"unbalanced') returns False and raises nothing."""
        self.assertFalse(is_executable_action('window-open "unbalanced'))

    def test_spawn_with_unbalanced_quote_is_executable(self):
        """An unbalanced quote in a spawn tail is NOT an overlay parse error:
        is_executable_action returns True so it passes through raw to the compositor."""
        self.assertTrue(is_executable_action('spawn printf "%s'))

    def test_head_only_family_check(self):
        """Head-only family check: the forbidden-family decision uses only the first word."""
        self.assertTrue(is_executable_action('spawn dpms-off'))
        self.assertTrue(is_executable_action('window-close output-disable'))
        self.assertFalse(is_executable_action('dpms-off'))
        self.assertFalse(is_executable_action('output-disable eDP-1'))

    def test_executable_actions_allowed(self):
        allowed = [
            "window-toggle-maximize",
            "window-toggle-pinned",
            "submap screencapture",
            "submap reset",
            "spawn grim screenshot.png",
            "window-focus next",
            "workspace-switch 2",
        ]
        for act in allowed:
            with self.subTest(action=act):
                self.assertTrue(
                    is_executable_action(act),
                    f"Action '{act}' should be executable!"
                )


class TestArgumentSplitting(unittest.TestCase):
    def test_shlex_splitting_preserves_quoted_args(self):
        """Fix #2 Regression: unit-test that the action string
        spawn sh -c 'echo hello world'
        becomes argv ["umbriel","msg","spawn","sh","-c","echo hello world"].
        """
        action = "spawn sh -c 'echo hello world'"
        words = shlex.split(action, posix=True)
        argv = ["umbriel", "msg"] + words
        expected = ["umbriel", "msg", "spawn", "sh", "-c", "echo hello world"]
        self.assertEqual(argv, expected)

    def test_shlex_splitting_simple_action(self):
        action = "window-toggle-maximize"
        words = shlex.split(action, posix=True)
        self.assertEqual(words, ["window-toggle-maximize"])


class TestActionFormatting(unittest.TestCase):
    def test_format_titles(self):
        test_cases = [
            ("window-toggle-maximize", "Toggle maximize"),
            ("window-toggle-pinned", "Toggle pinned"),
            ("session-quit skip-confirmation", "Quit session (skip confirmation)"),
            ("submap screencapture", "Enter submap: screencapture"),
            ("submap reset", "Reset submap"),
            ("spawn grim screenshot.png", "Run: grim screenshot.png"),
        ]
        for action, expected in test_cases:
            with self.subTest(action=action):
                self.assertEqual(format_action_title(action), expected)


class TestUmbrielKeysReader(unittest.TestCase):
    def test_scratch_fixture_parsing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = os.path.join(tmpdir, "config.toml")
            with open(cfg, "w") as f:
                f.write("""[general]
mod_key = "alt"

[keybinds]
"Mod+Escape" = { action = "session-quit skip-confirmation", repeat = false }
"Mod+T" = "window-toggle-pinned"
"Mod+B" = { action = "window-toggle-maximize" }
"Mod+S" = { action = "submap screencapture", repeat = false }
"submap[screencapture],1" = { action = "window-toggle-maximize", submap = "reset" }
"submap[screencapture],Escape" = "submap reset"
""")
            data = umbriel_keys.build(cfg)
            self.assertIn("binds", data)
            self.assertEqual(len(data["binds"]), 6)

            keys = [b["key"] for b in data["binds"]]
            self.assertIn("Mod+Escape", keys)
            self.assertIn("Mod+T", keys)
            self.assertIn("Mod+B", keys)
            self.assertIn("Mod+S", keys)
            self.assertIn("1", keys)
            self.assertIn("Escape", keys)

            # Check that '1' row carries submap == 'reset'
            one_row = next(b for b in data["binds"] if b["key"] == "1")
            self.assertEqual(one_row.get("flags", {}).get("submap"), "reset")

    def test_include_merge_fixture_parsing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            shared = os.path.join(tmpdir, "shared")
            os.makedirs(shared)
            with open(os.path.join(shared, "media.toml"), "w") as f:
                f.write("""[keybinds]
"Mod+T" = "spawn firefox"
"Mod+B" = { action = "window-toggle-fullscreen", repeat = false, allow_when_locked = true }
""")
            cfg = os.path.join(tmpdir, "main.toml")
            with open(cfg, "w") as f:
                f.write("""[general]
mod_key = "alt"

[include]
files = ["shared/media.toml", "shared/missing-required.toml"]

[include.optional]
files = ["shared/not-there-optional.toml"]

[keybinds]
"Mod+T" = "window-toggle-pinned"
"Mod+B" = { action = "window-toggle-maximize" }
"Mod+S" = { action = "submap screencapture", repeat = false }
"submap[screencapture],1" = { action = "spawn grim screenshot.png", submap = "reset" }
"submap[screencapture],Escape" = "submap reset"
""")
            data = umbriel_keys.build(cfg)
            self.assertIn("binds", data)
            self.assertEqual(len(data["binds"]), 5)

            mod_b = next((b for b in data["binds"] if b["key"] == "Mod+B"), None)
            self.assertIsNotNone(mod_b)
            self.assertIn("inherited", mod_b)
            self.assertIn("repeat", mod_b["inherited"])


class TestFilteringLogic(unittest.TestCase):
    def setUp(self):
        self.binds = [
            {"key": "Mod+Escape", "action": "session-quit skip-confirmation", "context": "default", "category": "Session"},
            {"key": "Mod+T", "action": "window-toggle-pinned", "context": "default", "category": "Window state"},
            {"key": "Mod+B", "action": "window-toggle-maximize", "context": "default", "category": "Window state"},
            {"key": "Mod+S", "action": "submap screencapture", "context": "default", "category": "Submaps"},
            {"key": "1", "action": "window-toggle-maximize", "context": "screencapture", "category": "Window state"},
            {"key": "Escape", "action": "submap reset", "context": "screencapture", "category": "Submaps"},
        ]

    def _filter(self, query: str, active_submap: str = None):
        words = query.strip().lower().split()
        matched = []
        for b in self.binds:
            key = b.get("key", "").lower()
            action = b.get("action", "").lower()
            label = b.get("label", "").lower()
            context = b.get("context", "default").lower()
            category = b.get("category", "").lower()
            text = f"{key} {action} {label} {context} {category}"
            if all(w in text for w in words):
                score = 0
                if active_submap and context == active_submap.lower():
                    score -= 100
                if query:
                    if key.startswith(query.lower()):
                        score -= 50
                    elif action.startswith(query.lower()):
                        score -= 40
                matched.append((score, b))
        matched.sort(key=lambda x: (x[0], x[1].get("order", 0), x[1].get("key", "")))
        return [item[1] for item in matched]

    def test_search_by_key(self):
        res = self._filter("Mod+B")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["key"], "Mod+B")

    def test_search_by_action_name(self):
        res = self._filter("maximize")
        self.assertEqual(len(res), 2)

    def test_search_by_submap(self):
        res = self._filter("screencapture")
        self.assertEqual(len(res), 3)

    def test_submap_priority(self):
        res = self._filter("maximize", active_submap="screencapture")
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["context"], "screencapture")
        self.assertEqual(res[0]["key"], "1")


class TestConfigResolution(unittest.TestCase):
    def test_bare_launch_exits_code_3_when_no_config(self):
        """Fix #3 Regression: bare launch with none of these present exits 3 with that message, no silent fallback."""
        env = os.environ.copy()
        env["XDG_CONFIG_HOME"] = "/tmp/nonexistent-xdg-config-test"
        res = subprocess.run(
            [sys.executable, os.path.join(PROJECT_DIR, "overlay.py")],
            env=env,
            capture_output=True,
            text=True
        )
        self.assertEqual(res.returncode, 3)
        self.assertIn("Umbriel config file not found", res.stderr)

    def test_compute_target_width_clamping(self):
        """Verify overlay width clamping logic for varying output geometries."""
        # Default fallback
        w_default = compute_target_width(620, margin=40)
        self.assertLessEqual(w_default, 620)
        self.assertGreaterEqual(w_default, 320)


class TestEdgeCasesAndRobustness(unittest.TestCase):
    def test_empty_and_whitespace_actions(self):
        """Verify clean_label and categorize handle empty/whitespace inputs without IndexError."""
        self.assertEqual(umbriel_keys.clean_label(""), "")
        self.assertEqual(umbriel_keys.clean_label("   "), "")
        self.assertEqual(umbriel_keys.categorize(""), "Other")
        self.assertEqual(umbriel_keys.categorize("   "), "Other")
        self.assertEqual(format_action_title(""), "")
        self.assertEqual(format_action_title("   "), "")

    def test_chord_rendering_plus_key(self):
        """Verify split_chord handles chords containing '+' as the target key."""
        from overlay import split_chord
        self.assertEqual(split_chord("Mod++"), ["Mod", "+"])
        self.assertEqual(split_chord("+"), ["+"])
        self.assertEqual(split_chord("Ctrl+Shift++"), ["Ctrl", "Shift", "+"])
        self.assertEqual(split_chord("Mod+B"), ["Mod", "B"])
        self.assertEqual(split_chord(""), [])

    def test_malformed_toml_structures(self):
        """Verify Parser.parse safely records errors on malformed TOML structures rather than crashing."""
        import tempfile

        # 1. Non-dict root
        with tempfile.NamedTemporaryFile("w", suffix=".toml") as tf:
            tf.write('"not a table"\n')
            tf.flush()
            p = umbriel_keys.Parser()
            p.parse(tf.name)
            self.assertGreater(len(p.errors), 0)

        # 2. Non-dict general
        with tempfile.NamedTemporaryFile("w", suffix=".toml") as tf:
            tf.write('general = "invalid string"\n')
            tf.flush()
            p = umbriel_keys.Parser()
            p.parse(tf.name)
            # Should not raise AttributeError

        # 3. Non-dict include
        with tempfile.NamedTemporaryFile("w", suffix=".toml") as tf:
            tf.write('include = "invalid string"\n')
            tf.flush()
            p = umbriel_keys.Parser()
            p.parse(tf.name)
            # Should not raise AttributeError

        # 4. Non-dict keybinds
        with tempfile.NamedTemporaryFile("w", suffix=".toml") as tf:
            tf.write('keybinds = ["invalid", "list"]\n')
            tf.flush()
            p = umbriel_keys.Parser()
            p.parse(tf.name)
            # Should not raise AttributeError


class TestSplitAction(unittest.TestCase):
    def test_split_action_normal(self):
        self.assertEqual(split_action("window-close"), ["window-close"])
        self.assertEqual(split_action("workspace-switch 2"), ["workspace-switch", "2"])

    def test_split_action_quoted_args_preserved(self):
        self.assertEqual(
            split_action("spawn sh -c 'echo hello world'"),
            ["spawn", "sh", "-c", "echo hello world"]
        )
        self.assertEqual(
            split_action('spawn "app name" --flag "arg with spaces"'),
            ["spawn", "app name", "--flag", "arg with spaces"]
        )

    def test_split_action_unbalanced_quote(self):
        self.assertIsNone(split_action('window-open "unbalanced'))
        self.assertIsNone(split_action("spawn 'unclosed"))

    def test_split_action_empty_and_whitespace(self):
        self.assertEqual(split_action(""), [])
        self.assertEqual(split_action("   "), [])
        self.assertEqual(split_action(None), [])


class TestNonBlockingExecution(unittest.TestCase):
    def setUp(self):
        self.ctx = GLib.MainContext.default()

    def _drain_idle(self):
        while self.ctx.pending():
            self.ctx.iteration(False)

    def test_execute_bind_unbalanced_quote(self):
        """_execute_bind with an unbalanced-quote action sets an error status and does not raise,
        call verify_safety_gate, or spawn a subprocess."""
        mock_gate = unittest.mock.MagicMock()
        mock_subproc = unittest.mock.MagicMock()

        class DummyOverlay:
            def __init__(self):
                self._executing = False
                self._destroyed = False
                self.config_path = "/path/to/config.toml"
                self.scratch_config = None
                self.scratch_cache = None
                self.status_calls = []

            def set_status(self, text, status_type="info"):
                self.status_calls.append((text, status_type))

        app = DummyOverlay()
        bind = {"action": 'window-open "unbalanced'}

        with unittest.mock.patch("overlay.verify_safety_gate", mock_gate), \
             unittest.mock.patch("subprocess.run", mock_subproc):
            overlay.KeybindOverlay._execute_bind(app, bind)

        mock_gate.assert_not_called()
        mock_subproc.assert_not_called()
        self.assertFalse(app._executing)
        self.assertEqual(len(app.status_calls), 1)
        status_text, status_type = app.status_calls[0]
        self.assertEqual(status_type, "error")
        self.assertIn("Invalid action syntax", status_text)

    def test_reentry_ignored_while_in_flight(self):
        """Re-entry: a second _execute_bind while one execution is in flight is ignored
        (no second worker/subprocess)."""
        gate_started = threading.Event()
        gate_release = threading.Event()
        gate_call_count = [0]

        def slow_gate(*args, **kwargs):
            gate_call_count[0] += 1
            gate_started.set()
            gate_release.wait(timeout=2.0)
            return (True, "ok")

        class DummyOverlay:
            def __init__(self):
                self._executing = False
                self._destroyed = False
                self.config_path = "/path/to/config.toml"
                self.scratch_config = None
                self.scratch_cache = None
                self.status_calls = []

            def set_status(self, text, status_type="info"):
                self.status_calls.append((text, status_type))

        app = DummyOverlay()
        mock_subproc = unittest.mock.MagicMock()
        mock_subproc.return_value = subprocess.CompletedProcess(args=["umbriel"], returncode=0, stdout="", stderr="")

        with unittest.mock.patch("overlay.verify_safety_gate", side_effect=slow_gate), \
             unittest.mock.patch("subprocess.run", mock_subproc), \
             unittest.mock.patch("gi.repository.Gtk.main_quit") as mock_quit:

            bind1 = {"action": "window-close"}
            bind2 = {"action": "workspace-switch 2"}

            overlay.KeybindOverlay._execute_bind(app, bind1)
            self.assertTrue(gate_started.wait(timeout=1.0))
            self.assertTrue(app._executing, "Execution must be marked in flight")

            # Fire second execution while first is in flight
            overlay.KeybindOverlay._execute_bind(app, bind2)

            gate_release.set()
            if hasattr(app, "_worker_thread"):
                app._worker_thread.join(timeout=1.0)
            self._drain_idle()

            self.assertEqual(gate_call_count[0], 1, "Gate must only be called once")
            self.assertEqual(mock_subproc.call_count, 1, "Subprocess must only be spawned once")
            mock_subproc.assert_called_once_with(["umbriel", "msg", "window-close"], capture_output=True, text=True, timeout=3)
            self.assertFalse(app._executing, "Flag must reset after completion")
            mock_quit.assert_called_once()

    def test_failure_reset_gate_and_subprocess(self):
        """Failure reset: after a failed execution (gate failure and subprocess error),
        the in-flight guard resets and a subsequent _execute_bind runs."""
        class DummyOverlay:
            def __init__(self):
                self._executing = False
                self._destroyed = False
                self.config_path = "/path/to/config.toml"
                self.scratch_config = None
                self.scratch_cache = None
                self.status_calls = []

            def set_status(self, text, status_type="info"):
                self.status_calls.append((text, status_type))

        app = DummyOverlay()

        # Part 1: Gate failure
        with unittest.mock.patch("overlay.verify_safety_gate", return_value=(False, "Compositor mismatch")), \
             unittest.mock.patch("subprocess.run") as mock_sub:
            overlay.KeybindOverlay._execute_bind(app, {"action": "window-close"})
            if hasattr(app, "_worker_thread"):
                app._worker_thread.join(timeout=1.0)
            self._drain_idle()

            self.assertFalse(app._executing, "Flag must reset after gate failure")
            mock_sub.assert_not_called()
            self.assertEqual(app.status_calls[-1], ("Compositor mismatch", "error"))

        # Part 2: Subprocess error (non-zero exit)
        with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
             unittest.mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=["umbriel"], returncode=1, stdout="", stderr="IPC failure")):
            overlay.KeybindOverlay._execute_bind(app, {"action": "window-close"})
            if hasattr(app, "_worker_thread"):
                app._worker_thread.join(timeout=1.0)
            self._drain_idle()

            self.assertFalse(app._executing, "Flag must reset after subprocess error")
            self.assertEqual(app.status_calls[-1], ("Compositor error: IPC failure", "error"))

        # Part 3: Subsequent execution succeeds cleanly
        with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
             unittest.mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=["umbriel"], returncode=0, stdout="", stderr="")), \
             unittest.mock.patch("gi.repository.Gtk.main_quit") as mock_quit:
            overlay.KeybindOverlay._execute_bind(app, {"action": "window-close"})
            if hasattr(app, "_worker_thread"):
                app._worker_thread.join(timeout=1.0)
            self._drain_idle()

            self.assertFalse(app._executing, "Flag must reset after success")
            mock_quit.assert_called_once()

    def test_destroyed_app_safety(self):
        """Destroyed-app safety: a worker completing after the overlay is destroyed does not raise."""
        worker_can_finish = threading.Event()

        def gate_wait(*args, **kwargs):
            worker_can_finish.wait(timeout=2.0)
            return (True, "ok")

        class DummyOverlay:
            def __init__(self):
                self._executing = False
                self._destroyed = False
                self.config_path = "/path/to/config.toml"
                self.scratch_config = None
                self.scratch_cache = None
                self.status_calls = []

            def set_status(self, text, status_type="info"):
                if self._destroyed:
                    raise RuntimeError("Called set_status on destroyed widget!")
                self.status_calls.append((text, status_type))

        app = DummyOverlay()

        with unittest.mock.patch("overlay.verify_safety_gate", side_effect=gate_wait), \
             unittest.mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=["umbriel"], returncode=0, stdout="", stderr="")), \
             unittest.mock.patch("gi.repository.Gtk.main_quit", side_effect=RuntimeError("Called main_quit on destroyed widget!")):

            overlay.KeybindOverlay._execute_bind(app, {"action": "window-close"})
            app._destroyed = True
            worker_can_finish.set()
            if hasattr(app, "_worker_thread"):
                app._worker_thread.join(timeout=1.0)

            try:
                self._drain_idle()
            except Exception as e:
                self.fail(f"Idle callback raised on destroyed app: {e}")

            self.assertFalse(app._executing, "Flag must reset even when app is destroyed")
            self.assertEqual(len(app.status_calls), 0, "No status updates should occur after destroy")


class TestSpawnTailPassthrough(unittest.TestCase):
    def setUp(self):
        self.ctx = GLib.MainContext.default()

    def _drain_idle(self):
        while self.ctx.pending():
            self.ctx.iteration(False)

    def _make_dummy_app(self):
        class DummyOverlay:
            def __init__(self):
                self._executing = False
                self._destroyed = False
                self.config_path = "/path/to/config.toml"
                self.scratch_config = None
                self.scratch_cache = None
                self.status_calls = []

            def set_status(self, text, status_type="info"):
                if self._destroyed:
                    raise RuntimeError("Called set_status on destroyed widget!")
                self.status_calls.append((text, status_type))

        return DummyOverlay()

    def test_spawn_exact_full_action_preserved(self):
        """'spawn printf \'%s\\n\' "hello world" > out.txt' arrives at the subprocess mock as argv
        ['umbriel', 'msg', 'spawn', 'printf \'%s\\n\' "hello world" > out.txt'] (one tail element)."""
        app = self._make_dummy_app()
        action = "spawn printf '%s\\n' \"hello world\" > out.txt"
        with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
             unittest.mock.patch("subprocess.run") as mock_sub, \
             unittest.mock.patch("gi.repository.Gtk.main_quit"):
            mock_sub.return_value = subprocess.CompletedProcess(args=["umbriel"], returncode=0, stdout="", stderr="")
            overlay.KeybindOverlay._execute_bind(app, {"action": action})
            if hasattr(app, "_worker_thread"):
                app._worker_thread.join(timeout=1.0)
            self._drain_idle()

            expected_cmd = ["umbriel", "msg", "spawn", "printf '%s\\n' \"hello world\" > out.txt"]
            mock_sub.assert_called_once_with(expected_cmd, capture_output=True, text=True, timeout=3)

    def test_spawn_tail_preservation_cases(self):
        """Tail preservation across:
        - quoted semicolons ('spawn printf x "a;b"')
        - command substitution ('spawn grim -g "$(slurp)" shot.png')
        - single-quoted literals ('spawn printf \'%s\\n\' \'$(echo no)\'')
        - escaped quotes ('spawn printf x "say \\"hi\\""')
        - inner whitespace runs ('spawn printf x "a  b"')
        """
        cases = [
            ('spawn printf x "a;b"', 'printf x "a;b"'),
            ('spawn grim -g "$(slurp)" shot.png', 'grim -g "$(slurp)" shot.png'),
            ("spawn printf '%s\\n' '$(echo no)'", "printf '%s\\n' '$(echo no)'"),
            ('spawn printf x "say \\"hi\\""', 'printf x "say \\"hi\\""'),
            ('spawn printf x "a  b"', 'printf x "a  b"'),
        ]
        for action, expected_tail in cases:
            with self.subTest(action=action):
                app = self._make_dummy_app()
                with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
                     unittest.mock.patch("subprocess.run") as mock_sub, \
                     unittest.mock.patch("gi.repository.Gtk.main_quit"):
                    mock_sub.return_value = subprocess.CompletedProcess(args=["umbriel"], returncode=0, stdout="", stderr="")
                    overlay.KeybindOverlay._execute_bind(app, {"action": action})
                    if hasattr(app, "_worker_thread"):
                        app._worker_thread.join(timeout=1.0)
                    self._drain_idle()

                    expected_cmd = ["umbriel", "msg", "spawn", expected_tail]
                    mock_sub.assert_called_once_with(expected_cmd, capture_output=True, text=True, timeout=3)

    def test_spawn_empty_and_whitespace_only(self):
        """Empty spawn passes as ['umbriel', 'msg', 'spawn']"""
        for act in ["spawn", "spawn   "]:
            with self.subTest(action=act):
                app = self._make_dummy_app()
                with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
                     unittest.mock.patch("subprocess.run") as mock_sub, \
                     unittest.mock.patch("gi.repository.Gtk.main_quit"):
                    mock_sub.return_value = subprocess.CompletedProcess(args=["umbriel"], returncode=0, stdout="", stderr="")
                    overlay.KeybindOverlay._execute_bind(app, {"action": act})
                    if hasattr(app, "_worker_thread"):
                        app._worker_thread.join(timeout=1.0)
                    self._drain_idle()

                    mock_sub.assert_called_once_with(["umbriel", "msg", "spawn"], capture_output=True, text=True, timeout=3)

    def test_spawn_unbalanced_quote_passed_raw(self):
        """An unbalanced quote in a spawn tail is passed through raw with no parse error."""
        app = self._make_dummy_app()
        action = 'spawn printf "%s'
        with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")) as mock_gate, \
             unittest.mock.patch("subprocess.run") as mock_sub, \
             unittest.mock.patch("gi.repository.Gtk.main_quit"):
            mock_sub.return_value = subprocess.CompletedProcess(args=["umbriel"], returncode=0, stdout="", stderr="")
            overlay.KeybindOverlay._execute_bind(app, {"action": action})
            if hasattr(app, "_worker_thread"):
                app._worker_thread.join(timeout=1.0)
            self._drain_idle()

            mock_gate.assert_called_once()
            mock_sub.assert_called_once_with(["umbriel", "msg", "spawn", 'printf "%s'], capture_output=True, text=True, timeout=3)

    def test_non_spawn_unbalanced_quote_sets_error_status(self):
        """In a non-spawn action, an unbalanced quote sets an error status, does not raise,
        and does not call the gate or subprocess."""
        app = self._make_dummy_app()
        action = 'window-open "unbalanced'
        with unittest.mock.patch("overlay.verify_safety_gate") as mock_gate, \
             unittest.mock.patch("subprocess.run") as mock_sub:
            overlay.KeybindOverlay._execute_bind(app, {"action": action})
            if hasattr(app, "_worker_thread"):
                app._worker_thread.join(timeout=1.0)
            self._drain_idle()

            mock_gate.assert_not_called()
            mock_sub.assert_not_called()
            self.assertEqual(len(app.status_calls), 1)
            self.assertEqual(app.status_calls[0][1], "error")
            self.assertIn("Invalid action syntax", app.status_calls[0][0])

    def test_non_spawn_actions_arrive_as_separate_words(self):
        """Non-spawn actions still arrive at subprocess.run as separate words."""
        cases = [
            ("submap reset", ["umbriel", "msg", "submap", "reset"]),
            ("workspace-switch 2 --output DP-1", ["umbriel", "msg", "workspace-switch", "2", "--output", "DP-1"]),
            ("window-close", ["umbriel", "msg", "window-close"]),
        ]
        for action, expected_cmd in cases:
            with self.subTest(action=action):
                app = self._make_dummy_app()
                with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
                     unittest.mock.patch("subprocess.run") as mock_sub, \
                     unittest.mock.patch("gi.repository.Gtk.main_quit"):
                    mock_sub.return_value = subprocess.CompletedProcess(args=["umbriel"], returncode=0, stdout="", stderr="")
                    overlay.KeybindOverlay._execute_bind(app, {"action": action})
                    if hasattr(app, "_worker_thread"):
                        app._worker_thread.join(timeout=1.0)
                    self._drain_idle()

                    mock_sub.assert_called_once_with(expected_cmd, capture_output=True, text=True, timeout=3)

    def test_head_only_family_check(self):
        """Head-only family check: the forbidden-family decision uses only the first word,
        for spawn and non-spawn actions alike."""
        # 'spawn dpms-off' should be allowed to run (head is spawn)
        app = self._make_dummy_app()
        with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
             unittest.mock.patch("subprocess.run") as mock_sub, \
             unittest.mock.patch("gi.repository.Gtk.main_quit"):
            mock_sub.return_value = subprocess.CompletedProcess(args=["umbriel"], returncode=0, stdout="", stderr="")
            overlay.KeybindOverlay._execute_bind(app, {"action": "spawn dpms-off"})
            if hasattr(app, "_worker_thread"):
                app._worker_thread.join(timeout=1.0)
            self._drain_idle()
            mock_sub.assert_called_once_with(["umbriel", "msg", "spawn", "dpms-off"], capture_output=True, text=True, timeout=3)

        # 'window-close dpms-off' should be allowed to run (head is window-close)
        app2 = self._make_dummy_app()
        with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
             unittest.mock.patch("subprocess.run") as mock_sub2, \
             unittest.mock.patch("gi.repository.Gtk.main_quit"):
            mock_sub2.return_value = subprocess.CompletedProcess(args=["umbriel"], returncode=0, stdout="", stderr="")
            overlay.KeybindOverlay._execute_bind(app2, {"action": "window-close dpms-off"})
            if hasattr(app2, "_worker_thread"):
                app2._worker_thread.join(timeout=1.0)
            self._drain_idle()
            mock_sub2.assert_called_once_with(["umbriel", "msg", "window-close", "dpms-off"], capture_output=True, text=True, timeout=3)

        # 'dpms-off' itself is blocked at head
        app3 = self._make_dummy_app()
        with unittest.mock.patch("overlay.verify_safety_gate") as mock_gate, \
             unittest.mock.patch("subprocess.run") as mock_sub3:
            overlay.KeybindOverlay._execute_bind(app3, {"action": "dpms-off"})
            if hasattr(app3, "_worker_thread"):
                app3._worker_thread.join(timeout=1.0)
            self._drain_idle()
            mock_gate.assert_not_called()
            mock_sub3.assert_not_called()
            self.assertEqual(len(app3.status_calls), 1)
            self.assertEqual(app3.status_calls[0][1], "error")
            self.assertIn("excluded from overlay execution", app3.status_calls[0][0])


class TestNestedLauncher(unittest.TestCase):
    def test_launcher_script_exists_and_executable(self):
        script_path = os.path.join(PROJECT_DIR, "scripts", "run-nested.sh")
        self.assertTrue(os.path.isfile(script_path), "scripts/run-nested.sh must exist")
        self.assertTrue(os.access(script_path, os.X_OK), "scripts/run-nested.sh must be executable")

    def test_launcher_bash_syntax(self):
        script_path = os.path.join(PROJECT_DIR, "scripts", "run-nested.sh")
        res = subprocess.run(["bash", "-n", script_path], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Bash syntax error in {script_path}: {res.stderr}")

    def test_launcher_bakes_cursor_configuration(self):
        """Verify launcher configures cursor workaround for nested sessions."""
        script_path = os.path.join(PROJECT_DIR, "scripts", "run-nested.sh")
        with open(script_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[input.cursor]", content)
        self.assertIn('theme = "Adwaita"', content)
        self.assertIn("size = 24", content)
        self.assertIn("hardware_cursor = false", content)
        self.assertIn("COMPOSITOR_PID", content)
        self.assertIn("kill -TERM", content)

    def test_launcher_popup_reopen_shortcut(self):
        """Verify launcher adds Alt+Shift+Space popup reopen shortcut without error masking."""
        script_path = os.path.join(PROJECT_DIR, "scripts", "run-nested.sh")
        with open(script_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn('"Alt+Shift+Space"', content)
        self.assertIn("repeat = false", content)
        # Verify placed before [input.cursor]
        pos_reopen = content.find('"Alt+Shift+Space"')
        pos_cursor = content.find("\n[input.cursor]")
        self.assertGreater(pos_reopen, 0)
        self.assertGreater(pos_cursor, pos_reopen, "Alt+Shift+Space must be in [keybinds] before [input.cursor]")
        # Verify no error masking || true
        self.assertNotIn('overlay.py" -c "$SCRATCH_CONFIG" || true', content)
        self.assertIn("OVERLAY_EXIT_CODE", content)

    def test_reopen_shortcut_quoting_spaces_and_apostrophes(self):
        """Verify shell quoting and TOML encoding for paths with spaces and apostrophes."""
        import json, tempfile
        dummy_path = "/tmp/tester's path with spaces/open-overlay.sh"
        cmd = f"spawn {shlex.quote(dummy_path)}"
        toml_val = json.dumps(cmd)
        toml_line = f'"Alt+Shift+Space" = {{ action = {toml_val}, repeat = false }}'

        # Verify parsing with overlay split_action
        tokens = split_action(cmd)
        self.assertEqual(tokens, ["spawn", dummy_path])

        # Parse TOML and check the quoted command without requiring Umbriel.
        with tempfile.NamedTemporaryFile("w", suffix=".toml") as tf:
            tf.write(toml_line)
            tf.flush()
            parsed = umbriel_keys._load(tf.name)
        binding = parsed["Alt+Shift+Space"]
        self.assertEqual(binding["action"], cmd)
        self.assertFalse(binding["repeat"])

    def test_reopen_shortcut_external_config_validation(self):
        """Validate with Umbriel when an executable is installed."""
        import json, shutil, tempfile
        umbriel = shutil.which("umbriel")
        if umbriel is None:
            self.skipTest("Umbriel executable is unavailable; pure quoting/TOML checks run separately")
        dummy_path = "/tmp/tester's path with spaces/open-overlay.sh"
        toml_val = json.dumps("spawn " + shlex.quote(dummy_path))
        toml_line = f'"Alt+Shift+Space" = {{ action = {toml_val}, repeat = false }}'

        with open(os.path.join(PROJECT_DIR, "examples", "typical-desktop.toml"), "r") as f:
            base_cfg = f.read()
        test_cfg = f"{base_cfg}\n{toml_line}\n\n[input.cursor]\ntheme = \"Adwaita\"\nsize = 24\nhardware_cursor = false\n"
        with tempfile.NamedTemporaryFile("w", suffix=".toml") as tf:
            tf.write(test_cfg)
            tf.flush()
            val_res = subprocess.run([umbriel, "config", "validate", "-c", tf.name], capture_output=True, text=True)
            self.assertEqual(val_res.returncode, 0, f"Config validation failed: {val_res.stderr}")

    def test_style_popup_elevation(self):
        css_path = os.path.join(PROJECT_DIR, "style.css")
        with open(css_path, "r", encoding="utf-8") as f:
            css = f.read()
        self.assertIn(".main-container", css)
        self.assertIn("#1e1e2e", css)
        self.assertIn("#585b70", css)


class TestRowRendering(unittest.TestCase):
    def test_row_building_unbalanced_quote_action(self):
        """A row-building test with an unbalanced-quote action (verifies no raise +
        'not executable' badge) needs GTK widget creation and may require a display."""
        if Gdk.Display.get_default() is None:
            raise unittest.SkipTest("GTK row-building requires an active display connection")

        from overlay import KeybindOverlay
        bind = {"key": "Mod+U", "action": 'window-open "unbalanced', "context": "default"}
        app = KeybindOverlay.__new__(KeybindOverlay)
        row = app._create_row_widget(bind)
        self.assertIsNotNone(row)
        badges = []

        def find_labels(widget):
            if isinstance(widget, Gtk.Label):
                badges.append(widget.get_text())
            elif hasattr(widget, "get_children"):
                for child in widget.get_children():
                    find_labels(child)

        find_labels(row)
        self.assertIn("not executable", badges)


if __name__ == "__main__":
    unittest.main()
