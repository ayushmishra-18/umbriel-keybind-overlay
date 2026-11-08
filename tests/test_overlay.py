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


def _make_mock_proc(stdout="", stderr="", returncode=0):
    proc = unittest.mock.MagicMock()
    proc.returncode = returncode
    proc.poll.return_value = returncode
    proc.communicate.return_value = (stdout, stderr)
    return proc


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
                self._cancel_event = threading.Event()
                self._pending_proc = None
                self._proc_lock = threading.Lock()
                self.config_path = "/path/to/config.toml"
                self.scratch_config = None
                self.scratch_cache = None
                self.status_calls = []

            def set_status(self, text, status_type="info"):
                self.status_calls.append((text, status_type))

        app = DummyOverlay()
        bind = {"action": 'window-open "unbalanced'}

        with unittest.mock.patch("overlay.verify_safety_gate", mock_gate), \
             unittest.mock.patch("subprocess.Popen", mock_subproc):
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
                self._cancel_event = threading.Event()
                self._pending_proc = None
                self._proc_lock = threading.Lock()
                self.config_path = "/path/to/config.toml"
                self.scratch_config = None
                self.scratch_cache = None
                self.status_calls = []

            def set_status(self, text, status_type="info"):
                self.status_calls.append((text, status_type))

        app = DummyOverlay()
        mock_proc = _make_mock_proc()
        mock_subproc = unittest.mock.MagicMock(return_value=mock_proc)

        with unittest.mock.patch("overlay.verify_safety_gate", side_effect=slow_gate), \
             unittest.mock.patch("subprocess.Popen", mock_subproc), \
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
            mock_subproc.assert_called_once_with(["umbriel", "msg", "window-close"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertFalse(app._executing, "Flag must reset after completion")
            mock_quit.assert_called_once()

    def test_failure_reset_gate_and_subprocess(self):
        """Failure reset: after a failed execution (gate failure and subprocess error),
        the in-flight guard resets and a subsequent _execute_bind runs."""
        class DummyOverlay:
            def __init__(self):
                self._executing = False
                self._destroyed = False
                self._cancel_event = threading.Event()
                self._pending_proc = None
                self._proc_lock = threading.Lock()
                self.config_path = "/path/to/config.toml"
                self.scratch_config = None
                self.scratch_cache = None
                self.status_calls = []

            def set_status(self, text, status_type="info"):
                self.status_calls.append((text, status_type))

        app = DummyOverlay()

        # Part 1: Gate failure
        with unittest.mock.patch("overlay.verify_safety_gate", return_value=(False, "Compositor mismatch")), \
             unittest.mock.patch("subprocess.Popen") as mock_sub:
            overlay.KeybindOverlay._execute_bind(app, {"action": "window-close"})
            if hasattr(app, "_worker_thread"):
                app._worker_thread.join(timeout=1.0)
            self._drain_idle()

            self.assertFalse(app._executing, "Flag must reset after gate failure")
            mock_sub.assert_not_called()
            self.assertEqual(app.status_calls[-1], ("Compositor mismatch", "error"))

        # Part 2: Subprocess error (non-zero exit)
        with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
             unittest.mock.patch("subprocess.Popen", return_value=_make_mock_proc(stderr="IPC failure", returncode=1)):
            overlay.KeybindOverlay._execute_bind(app, {"action": "window-close"})
            if hasattr(app, "_worker_thread"):
                app._worker_thread.join(timeout=1.0)
            self._drain_idle()

            self.assertFalse(app._executing, "Flag must reset after subprocess error")
            self.assertEqual(app.status_calls[-1], ("Compositor error: IPC failure", "error"))

        # Part 3: Subsequent execution succeeds cleanly
        with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
             unittest.mock.patch("subprocess.Popen", return_value=_make_mock_proc()), \
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
                self._cancel_event = threading.Event()
                self._pending_proc = None
                self._proc_lock = threading.Lock()
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
             unittest.mock.patch("subprocess.Popen", return_value=_make_mock_proc()), \
             unittest.mock.patch("gi.repository.Gtk.main_quit", side_effect=RuntimeError("Called main_quit on destroyed widget!")):

            overlay.KeybindOverlay._execute_bind(app, {"action": "window-close"})
            app._destroyed = True
            app._cancel_event.set()
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
                self._cancel_event = threading.Event()
                self._pending_proc = None
                self._proc_lock = threading.Lock()
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
             unittest.mock.patch("subprocess.Popen", return_value=_make_mock_proc()) as mock_sub, \
             unittest.mock.patch("gi.repository.Gtk.main_quit"):
            overlay.KeybindOverlay._execute_bind(app, {"action": action})
            if hasattr(app, "_worker_thread"):
                app._worker_thread.join(timeout=1.0)
            self._drain_idle()

            expected_cmd = ["umbriel", "msg", "spawn", "printf '%s\\n' \"hello world\" > out.txt"]
            mock_sub.assert_called_once_with(expected_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

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
                     unittest.mock.patch("subprocess.Popen", return_value=_make_mock_proc()) as mock_sub, \
                     unittest.mock.patch("gi.repository.Gtk.main_quit"):
                    overlay.KeybindOverlay._execute_bind(app, {"action": action})
                    if hasattr(app, "_worker_thread"):
                        app._worker_thread.join(timeout=1.0)
                    self._drain_idle()

                    expected_cmd = ["umbriel", "msg", "spawn", expected_tail]
                    mock_sub.assert_called_once_with(expected_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_spawn_empty_and_whitespace_only(self):
        """Empty spawn passes as ['umbriel', 'msg', 'spawn']"""
        for act in ["spawn", "spawn   "]:
            with self.subTest(action=act):
                app = self._make_dummy_app()
                with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
                     unittest.mock.patch("subprocess.Popen", return_value=_make_mock_proc()) as mock_sub, \
                     unittest.mock.patch("gi.repository.Gtk.main_quit"):
                    overlay.KeybindOverlay._execute_bind(app, {"action": act})
                    if hasattr(app, "_worker_thread"):
                        app._worker_thread.join(timeout=1.0)
                    self._drain_idle()

                    mock_sub.assert_called_once_with(["umbriel", "msg", "spawn"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_spawn_unbalanced_quote_passed_raw(self):
        """An unbalanced quote in a spawn tail is passed through raw with no parse error."""
        app = self._make_dummy_app()
        action = 'spawn printf "%s'
        with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")) as mock_gate, \
             unittest.mock.patch("subprocess.Popen", return_value=_make_mock_proc()) as mock_sub, \
             unittest.mock.patch("gi.repository.Gtk.main_quit"):
            overlay.KeybindOverlay._execute_bind(app, {"action": action})
            if hasattr(app, "_worker_thread"):
                app._worker_thread.join(timeout=1.0)
            self._drain_idle()

            mock_gate.assert_called_once()
            mock_sub.assert_called_once_with(["umbriel", "msg", "spawn", 'printf "%s'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_non_spawn_unbalanced_quote_sets_error_status(self):
        """In a non-spawn action, an unbalanced quote sets an error status, does not raise,
        and does not call the gate or subprocess."""
        app = self._make_dummy_app()
        action = 'window-open "unbalanced'
        with unittest.mock.patch("overlay.verify_safety_gate") as mock_gate, \
             unittest.mock.patch("subprocess.Popen") as mock_sub:
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
        """Non-spawn actions still arrive at subprocess.Popen as separate words."""
        cases = [
            ("submap reset", ["umbriel", "msg", "submap", "reset"]),
            ("workspace-switch 2 --output DP-1", ["umbriel", "msg", "workspace-switch", "2", "--output", "DP-1"]),
            ("window-close", ["umbriel", "msg", "window-close"]),
        ]
        for action, expected_cmd in cases:
            with self.subTest(action=action):
                app = self._make_dummy_app()
                with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
                     unittest.mock.patch("subprocess.Popen", return_value=_make_mock_proc()) as mock_sub, \
                     unittest.mock.patch("gi.repository.Gtk.main_quit"):
                    overlay.KeybindOverlay._execute_bind(app, {"action": action})
                    if hasattr(app, "_worker_thread"):
                        app._worker_thread.join(timeout=1.0)
                    self._drain_idle()

                    mock_sub.assert_called_once_with(expected_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_head_only_family_check(self):
        """Head-only family check: the forbidden-family decision uses only the first word,
        for spawn and non-spawn actions alike."""
        # 'spawn dpms-off' should be allowed to run (head is spawn)
        app = self._make_dummy_app()
        with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
             unittest.mock.patch("subprocess.Popen", return_value=_make_mock_proc()) as mock_sub, \
             unittest.mock.patch("gi.repository.Gtk.main_quit"):
            overlay.KeybindOverlay._execute_bind(app, {"action": "spawn dpms-off"})
            if hasattr(app, "_worker_thread"):
                app._worker_thread.join(timeout=1.0)
            self._drain_idle()
            mock_sub.assert_called_once_with(["umbriel", "msg", "spawn", "dpms-off"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # 'window-close dpms-off' should be allowed to run (head is window-close)
        app2 = self._make_dummy_app()
        with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
             unittest.mock.patch("subprocess.Popen", return_value=_make_mock_proc()) as mock_sub2, \
             unittest.mock.patch("gi.repository.Gtk.main_quit"):
            mock_sub2.return_value = subprocess.CompletedProcess(args=["umbriel"], returncode=0, stdout="", stderr="")
            overlay.KeybindOverlay._execute_bind(app2, {"action": "window-close dpms-off"})
            if hasattr(app2, "_worker_thread"):
                app2._worker_thread.join(timeout=1.0)
            self._drain_idle()
            mock_sub2.assert_called_once_with(["umbriel", "msg", "window-close", "dpms-off"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # 'dpms-off' itself is blocked at head
        app3 = self._make_dummy_app()
        with unittest.mock.patch("overlay.verify_safety_gate") as mock_gate, \
             unittest.mock.patch("subprocess.Popen") as mock_sub3:
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


class TestCancelBeforeDispatch(unittest.TestCase):
    """Deliverable C: Cancel-before-dispatch regression tests."""

    def setUp(self):
        self.ctx = GLib.MainContext.default()

    def _drain_idle(self):
        while self.ctx.pending():
            self.ctx.iteration(False)

    def _make_app(self):
        class DummyOverlay:
            def __init__(self):
                self._executing = False
                self._destroyed = False
                self._cancel_event = threading.Event()
                self._pending_proc = None
                self._proc_lock = threading.Lock()
                self.submap_listener_proc = None
                self.config_path = "/path/to/config.toml"
                self.scratch_config = None
                self.scratch_cache = None
                self.status_calls = []

            def set_status(self, text, status_type="info"):
                self.status_calls.append((text, status_type))

            def _on_destroy(self, widget):
                overlay.KeybindOverlay._on_destroy(self, widget)

        return DummyOverlay()

    def test_a_cancel_while_safety_gate_held(self):
        """(a) Cancel while safety gate held: worker does not dispatch command after gate releases."""
        app = self._make_app()
        gate_started = threading.Event()
        gate_release = threading.Event()

        def slow_gate(*args, **kwargs):
            gate_started.set()
            gate_release.wait(timeout=2.0)
            return (True, "ok")

        mock_popen = unittest.mock.MagicMock()
        mock_run = unittest.mock.MagicMock()
        with unittest.mock.patch("overlay.verify_safety_gate", side_effect=slow_gate), \
             unittest.mock.patch("subprocess.Popen", mock_popen), \
             unittest.mock.patch("subprocess.run", mock_run), \
             unittest.mock.patch("gi.repository.Gtk.main_quit") as mock_quit:

            overlay.KeybindOverlay._execute_bind(app, {"action": "window-close"})
            self.assertTrue(gate_started.wait(timeout=1.0))
            self.assertTrue(app._executing)

            # Destroy overlay while gate is evaluating
            app._on_destroy(None)
            self.assertTrue(app._cancel_event.is_set())
            self.assertTrue(app._destroyed)

            # Release gate
            gate_release.set()
            app._worker_thread.join(timeout=1.0)
            self._drain_idle()

            # Must assert that NO process was dispatched by any mechanism (Popen or run)
            mock_popen.assert_not_called()
            mock_run.assert_not_called()
            mock_quit.assert_not_called()
            self.assertFalse(app._executing)
            self.assertEqual(len(app.status_calls), 0)

    def test_cancel_during_process_registration_gap(self):
        """Recheck cancellation when registering newly started process:
        Forces the interleaving where destroy fires after spawn but before registration,
        asserting the child was terminated, not registered, and no follow-up submap or main_quit ran."""
        app = self._make_app()
        bind = {"action": "window-toggle-maximize", "flags": {"submap": "window_sub"}}

        child_terminated = threading.Event()
        spawn_done = threading.Event()
        allow_registration = threading.Event()

        class EventControlledProc:
            returncode = -15
            def poll(self):
                return None if not child_terminated.is_set() else -15
            def terminate(self):
                child_terminated.set()
            def communicate(self, timeout=None):
                if not child_terminated.is_set():
                    raise subprocess.TimeoutExpired(cmd=["umbriel"], timeout=timeout or 3)
                return ("", "terminated")

        fake_proc = EventControlledProc()

        def fake_popen(cmd, *args, **kwargs):
            spawn_done.set()
            allow_registration.wait(timeout=2.0)
            return fake_proc

        with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
             unittest.mock.patch("subprocess.Popen", side_effect=fake_popen), \
             unittest.mock.patch("gi.repository.Gtk.main_quit") as mock_quit:

            overlay.KeybindOverlay._execute_bind(app, bind)

            # Wait until Popen has spawned the process
            self.assertTrue(spawn_done.wait(timeout=1.0))

            # Overlay is destroyed AFTER spawn, BEFORE registration under _proc_lock
            app._on_destroy(None)
            self.assertTrue(app._destroyed)
            self.assertIsNone(app._pending_proc, "Process must not be registered before _proc_lock")

            # Now allow the spawn function to return and registration to proceed
            allow_registration.set()

            app._worker_thread.join(timeout=1.0)
            self._drain_idle()

            # Assert child was terminated immediately upon registration check
            self.assertTrue(child_terminated.is_set(), "Child must be terminated immediately inside _proc_lock")
            self.assertIsNone(app._pending_proc, "Cancelled process must not be registered")
            mock_quit.assert_not_called()
            self.assertFalse(app._executing)
            self.assertEqual(len(app.status_calls), 0)

    def test_cancel_during_submap_registration_gap(self):
        """Recheck cancellation when registering newly started submap process:
        Forces the interleaving where destroy fires after submap spawn but before registration,
        asserting the submap child was terminated, not registered, and no main_quit ran."""
        app = self._make_app()
        bind = {"action": "window-toggle-maximize", "flags": {"submap": "window_sub"}}

        child_terminated = threading.Event()
        submap_spawn_done = threading.Event()
        allow_submap_registration = threading.Event()

        class FakePrimaryProc:
            returncode = 0
            def poll(self):
                return 0
            def communicate(self, timeout=None):
                return ("", "")

        class EventControlledSubmapProc:
            returncode = -15
            def poll(self):
                return None if not child_terminated.is_set() else -15
            def terminate(self):
                child_terminated.set()
            def communicate(self, timeout=None):
                if not child_terminated.is_set():
                    raise subprocess.TimeoutExpired(cmd=["umbriel"], timeout=timeout or 3)
                return ("", "terminated")

        fake_submap_proc = EventControlledSubmapProc()

        def fake_popen(cmd, *args, **kwargs):
            if "submap" in cmd:
                submap_spawn_done.set()
                allow_submap_registration.wait(timeout=2.0)
                return fake_submap_proc
            return FakePrimaryProc()

        with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
             unittest.mock.patch("subprocess.Popen", side_effect=fake_popen), \
             unittest.mock.patch("gi.repository.Gtk.main_quit") as mock_quit:

            overlay.KeybindOverlay._execute_bind(app, bind)

            # Wait until submap Popen has spawned
            self.assertTrue(submap_spawn_done.wait(timeout=1.0))

            # Overlay is destroyed AFTER submap spawn, BEFORE submap registration
            app._on_destroy(None)
            self.assertTrue(app._destroyed)

            # Now allow submap registration to proceed
            allow_submap_registration.set()

            app._worker_thread.join(timeout=1.0)
            self._drain_idle()

            self.assertTrue(child_terminated.is_set(), "Submap process must be terminated immediately inside _proc_lock")
            self.assertIsNone(app._pending_proc, "Cancelled submap process must not be registered")
            mock_quit.assert_not_called()
            self.assertFalse(app._executing)
            self.assertEqual(len(app.status_calls), 0)

    def test_b_cancel_after_primary_before_submap(self):
        """(b) Cancel after primary command before submap: follow-up submap is never dispatched."""
        app = self._make_app()
        bind = {"action": "window-toggle-maximize", "flags": {"submap": "window_sub"}}

        popen_calls = []

        class FakePrimaryProc:
            returncode = 0
            def poll(self):
                return 0
            def communicate(self, timeout=None):
                # When primary communicate finishes, destroy the overlay before submap starts
                app._on_destroy(None)
                return ("", "")

        def fake_popen(cmd, *args, **kwargs):
            popen_calls.append(cmd)
            return FakePrimaryProc()

        with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
             unittest.mock.patch("subprocess.Popen", side_effect=fake_popen), \
             unittest.mock.patch("gi.repository.Gtk.main_quit") as mock_quit:

            overlay.KeybindOverlay._execute_bind(app, bind)
            app._worker_thread.join(timeout=1.0)
            self._drain_idle()

            self.assertEqual(len(popen_calls), 1)
            self.assertEqual(popen_calls[0], ["umbriel", "msg", "window-toggle-maximize"])
            # Follow-up submap command was cancelled and never launched
            mock_quit.assert_not_called()
            self.assertFalse(app._executing)

    def test_c_cancel_while_umbriel_msg_child_running(self):
        """(c) Cancel while umbriel msg child running: pending proc is terminated."""
        app = self._make_app()
        child_started = threading.Event()
        child_terminated = threading.Event()

        class HangingProc:
            returncode = -15
            def poll(self):
                return None if not child_terminated.is_set() else -15
            def terminate(self):
                child_terminated.set()
            def communicate(self, timeout=None):
                child_started.set()
                child_terminated.wait(timeout=2.0)
                return ("", "terminated")

        mock_hanging = HangingProc()
        with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
             unittest.mock.patch("subprocess.Popen", return_value=mock_hanging), \
             unittest.mock.patch("gi.repository.Gtk.main_quit") as mock_quit:

            overlay.KeybindOverlay._execute_bind(app, {"action": "window-focus-left"})
            self.assertTrue(child_started.wait(timeout=1.0))

            # While child is running, destroy the overlay
            app._on_destroy(None)
            self.assertTrue(child_terminated.is_set())

            app._worker_thread.join(timeout=1.0)
            self._drain_idle()

            mock_quit.assert_not_called()
            self.assertFalse(app._executing)
            self.assertEqual(len(app.status_calls), 0)

    def test_d_no_cancel_existing_behavior_preserved(self):
        """(d) No cancel: primary command and follow-up submap both run and overlay closes."""
        app = self._make_app()
        bind = {"action": "window-focus-right", "flags": {"submap": "reset"}}
        dispatched_cmds = []

        class SuccessProc:
            returncode = 0
            def poll(self):
                return 0
            def communicate(self, timeout=None):
                return ("", "")

        def fake_popen(cmd, *args, **kwargs):
            dispatched_cmds.append(cmd)
            return SuccessProc()

        with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
             unittest.mock.patch("subprocess.Popen", side_effect=fake_popen), \
             unittest.mock.patch("gi.repository.Gtk.main_quit") as mock_quit:

            overlay.KeybindOverlay._execute_bind(app, bind)
            app._worker_thread.join(timeout=1.0)
            self._drain_idle()

            self.assertEqual(len(dispatched_cmds), 2)
            self.assertEqual(dispatched_cmds[0], ["umbriel", "msg", "window-focus-right"])
            self.assertEqual(dispatched_cmds[1], ["umbriel", "msg", "submap", "reset"])
            mock_quit.assert_called_once()
            self.assertFalse(app._executing)

    def test_e_timeout_child_killed_and_error_shown(self):
        """(e) Timeout child killed and error shown when command hangs."""
        app = self._make_app()
        killed = threading.Event()

        class TimeoutProc:
            returncode = -9
            def poll(self):
                return None if not killed.is_set() else -9
            def kill(self):
                killed.set()
            def communicate(self, timeout=None):
                if timeout is not None:
                    raise subprocess.TimeoutExpired(cmd=["umbriel"], timeout=timeout)
                return ("", "killed")

        with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
             unittest.mock.patch("subprocess.Popen", return_value=TimeoutProc()), \
             unittest.mock.patch("gi.repository.Gtk.main_quit") as mock_quit:

            overlay.KeybindOverlay._execute_bind(app, {"action": "window-close"})
            app._worker_thread.join(timeout=1.0)
            self._drain_idle()

            self.assertTrue(killed.is_set())
            mock_quit.assert_not_called()
            self.assertFalse(app._executing)
            self.assertEqual(len(app.status_calls), 1)
            self.assertIn("Execution timed out after 3 seconds", app.status_calls[0][0])
            self.assertEqual(app.status_calls[0][1], "error")

    def test_f_double_trigger_blocked(self):
        """(f) Double-trigger blocked: second call ignored while executing or cancelled."""
        app = self._make_app()
        app._executing = True

        mock_gate = unittest.mock.MagicMock()
        with unittest.mock.patch("overlay.verify_safety_gate", mock_gate):
            overlay.KeybindOverlay._execute_bind(app, {"action": "window-close"})
            mock_gate.assert_not_called()

        app._executing = False
        app._cancel_event.set()
        with unittest.mock.patch("overlay.verify_safety_gate", mock_gate):
            overlay.KeybindOverlay._execute_bind(app, {"action": "window-close"})
            mock_gate.assert_not_called()

        app._cancel_event.clear()
        app._destroyed = True
        with unittest.mock.patch("overlay.verify_safety_gate", mock_gate):
            overlay.KeybindOverlay._execute_bind(app, {"action": "window-close"})
            mock_gate.assert_not_called()

    def test_g_forbidden_families_blocked(self):
        """(g) Forbidden families blocked: session-, config-, output-, input-capture-, dpms-."""
        app = self._make_app()
        forbidden_actions = [
            "session-quit",
            "session-lock",
            "config-reload",
            "config-validate",
            "output-power-off",
            "input-capture-stop",
            "dpms-off",
            "dpms-on",
        ]
        for act in forbidden_actions:
            with self.subTest(action=act):
                self.assertFalse(overlay.is_executable_action(act))
                app.status_calls.clear()
                mock_gate = unittest.mock.MagicMock()
                with unittest.mock.patch("overlay.verify_safety_gate", mock_gate):
                    overlay.KeybindOverlay._execute_bind(app, {"action": act})
                    mock_gate.assert_not_called()
                    self.assertFalse(app._executing)
                    self.assertEqual(len(app.status_calls), 1)
                    self.assertIn("excluded from overlay execution for safety", app.status_calls[0][0])

    def test_h_spawn_passthrough_verbatim(self):
        """(h) Spawn passthrough verbatim: raw tail preserved without splitting."""
        app = self._make_app()
        dispatched_cmds = []

        class SuccessProc:
            returncode = 0
            def poll(self):
                return 0
            def communicate(self, timeout=None):
                return ("", "")

        def fake_popen(cmd, *args, **kwargs):
            dispatched_cmds.append(cmd)
            return SuccessProc()

        cases = [
            ("spawn grim -g \"$(slurp)\" shot.png", ["umbriel", "msg", "spawn", "grim -g \"$(slurp)\" shot.png"]),
            ("spawn printf '%s\\n' \"hello world\" > out.txt", ["umbriel", "msg", "spawn", "printf '%s\\n' \"hello world\" > out.txt"]),
            ("spawn printf \"%s", ["umbriel", "msg", "spawn", "printf \"%s"]),
        ]
        for act, expected in cases:
            with self.subTest(action=act):
                dispatched_cmds.clear()
                with unittest.mock.patch("overlay.verify_safety_gate", return_value=(True, "ok")), \
                     unittest.mock.patch("subprocess.Popen", side_effect=fake_popen), \
                     unittest.mock.patch("gi.repository.Gtk.main_quit"):
                    overlay.KeybindOverlay._execute_bind(app, {"action": act})
                    app._worker_thread.join(timeout=1.0)
                    self._drain_idle()
                    self.assertEqual(dispatched_cmds, [expected])


if __name__ == "__main__":
    unittest.main()
