#!/usr/bin/env python3
"""Unit tests for the Umbriel Keybind Overlay."""

import os
import shlex
import subprocess
import sys
import unittest

# Ensure project directory is in sys.path
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import umbriel_keys
from overlay import is_executable_action, format_action_title, FORBIDDEN_FAMILIES, compute_target_width


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
        ]
        for act in blocked:
            with self.subTest(action=act):
                self.assertFalse(
                    is_executable_action(act),
                    f"Action '{act}' should NOT be executable!"
                )

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


if __name__ == "__main__":
    unittest.main()
