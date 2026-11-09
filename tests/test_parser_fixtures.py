#!/usr/bin/env python3
"""Compatibility fixture tests for umbriel_keys configuration parsing and merge semantics.

Tests the overlay's documented merge and parse rules against file-based fixtures
representing Umbriel config patterns pinned to compatibility baseline commit c6ad18d.
Runs completely headless (imports umbriel_keys only; no GTK or display dependencies).
"""

import os
import sys
import unittest

# Ensure project directory is in sys.path
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import umbriel_keys

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "parser")


class TestParserFixtures(unittest.TestCase):
    """Verifies umbriel_keys parsing and merge rules against static TOML fixtures."""

    def test_nested_includes(self):
        """Root includes a.toml, which includes b.toml; binds from all three appear in order."""
        config_path = os.path.join(FIXTURES_DIR, "nested_includes", "root.toml")
        res = umbriel_keys.build(config_path)

        self.assertEqual(res["errors"], [])
        self.assertEqual(res["warnings"], [])
        self.assertEqual(res["counts"]["binds"], 3)

        bind_keys = [b["key"] for b in res["binds"]]
        self.assertEqual(bind_keys, ["Mod+B", "Mod+A", "Mod+R"])

        # Check sources, actions, and parse orders (deepest include parsed first)
        by_key = {b["key"]: b for b in res["binds"]}
        self.assertEqual(by_key["Mod+B"]["source"], "b.toml")
        self.assertEqual(by_key["Mod+B"]["action"], "spawn terminal-b")
        self.assertEqual(by_key["Mod+B"]["order"], 1)

        self.assertEqual(by_key["Mod+A"]["source"], "a.toml")
        self.assertEqual(by_key["Mod+A"]["action"], "spawn terminal-a")
        self.assertEqual(by_key["Mod+A"]["order"], 2)

        self.assertEqual(by_key["Mod+R"]["source"], "root.toml")
        self.assertEqual(by_key["Mod+R"]["action"], "spawn terminal-root")
        self.assertEqual(by_key["Mod+R"]["order"], 3)

        # Mod key reporting across files
        self.assertIn("root.toml sets 'alt'", res["mod_note"])
        self.assertIn("b.toml sets 'super'", res["mod_note"])

    def test_include_cycle(self):
        """Circular includes (cycle_a <-> cycle_b) emit an upstream-compatible warning and terminate safely."""
        config_path = os.path.join(FIXTURES_DIR, "include_cycle", "cycle_a.toml")
        res = umbriel_keys.build(config_path)

        self.assertEqual(res["errors"], [])
        self.assertEqual(len(res["warnings"]), 1)
        self.assertEqual(
            res["warnings"][0],
            "Include cycle or duplicate skipped: cycle_a.toml - matches upstream warning",
        )

        bind_keys = {b["key"] for b in res["binds"]}
        self.assertEqual(bind_keys, {"Mod+A", "Mod+B"})

    def test_optional_includes(self):
        """Present optional include is loaded; absent optional include is silently ignored without warning."""
        config_path = os.path.join(FIXTURES_DIR, "optional_includes", "root.toml")
        res = umbriel_keys.build(config_path)

        self.assertEqual(res["errors"], [])
        self.assertEqual(res["warnings"], [])
        self.assertEqual(res["counts"]["binds"], 2)

        bind_keys = [b["key"] for b in res["binds"]]
        self.assertEqual(bind_keys, ["Mod+P", "Mod+O"])

        by_key = {b["key"]: b for b in res["binds"]}
        self.assertEqual(by_key["Mod+P"]["source"], "present.toml")
        self.assertEqual(by_key["Mod+O"]["source"], "root.toml")

    def test_missing_required_include(self):
        """Missing required include records a warning; remaining binds from including file still parse."""
        config_path = os.path.join(FIXTURES_DIR, "missing_required", "root.toml")
        res = umbriel_keys.build(config_path)

        self.assertEqual(res["errors"], [])
        self.assertEqual(len(res["warnings"]), 1)
        self.assertEqual(
            res["warnings"][0],
            "Required include not found: nonexistent_required.toml (from root.toml); binds from it are MISSING above",
        )

        self.assertEqual(len(res["binds"]), 1)
        self.assertEqual(res["binds"][0]["key"], "Mod+M")
        self.assertEqual(res["binds"][0]["action"], "spawn root")

    def test_merge_precedence(self):
        """Scalar bind replaces table wholesale; table-form bind merges field-by-field and records inheritance."""
        config_path = os.path.join(FIXTURES_DIR, "merge_precedence", "root.toml")
        res = umbriel_keys.build(config_path)

        self.assertEqual(res["errors"], [])
        self.assertEqual(res["warnings"], [])
        self.assertEqual(len(res["binds"]), 2)

        by_key = {b["key"]: b for b in res["binds"]}

        # 1. Scalar replaces table wholesale:
        mod_s = by_key["Mod+S"]
        self.assertEqual(mod_s["action"], "spawn new-script")
        self.assertEqual(mod_s["source"], "root.toml")
        self.assertEqual(mod_s["flags"], {})
        self.assertNotIn("inherited", mod_s)

        # 2. Table-form merges field-by-field:
        mod_t = by_key["Mod+T"]
        self.assertEqual(mod_t["action"], "window-toggle-maximize")
        self.assertEqual(mod_t["source"], "root.toml")
        self.assertEqual(mod_t["flags"], {"repeat": True, "allow_when_locked": True})
        self.assertEqual(mod_t["inherited"], {"allow_when_locked": (True, "base.toml")})

        # Notes verification
        self.assertEqual(len(res["notes"]), 2)
        self.assertIn(
            "'Mod+S' (default): later definition (root.toml) replaces the earlier one (base.toml) entirely - verified upstream merge rule",
            res["notes"],
        )
        self.assertIn(
            "'Mod+T' (default): action from root.toml; fields allow_when_locked inherited from base.toml - verified upstream table-merge rule",
            res["notes"],
        )

    def test_submaps(self):
        """Submap-scoped binds (submap[name],chord) populate context and flags.submap correctly."""
        config_path = os.path.join(FIXTURES_DIR, "submaps", "root.toml")
        res = umbriel_keys.build(config_path)

        self.assertEqual(res["errors"], [])
        self.assertEqual(res["warnings"], [])
        self.assertEqual(res["counts"]["submaps"], ["screencapture"])
        self.assertEqual(res["counts"]["binds"], 4)

        by_key = {(b["context"], b["key"]): b for b in res["binds"]}

        # Default context
        self.assertEqual(by_key[("default", "Mod+S")]["action"], "submap screencapture")
        self.assertEqual(by_key[("default", "Mod+S")]["flags"], {"repeat": False})
        self.assertEqual(by_key[("default", "Mod+Return")]["action"], "spawn alacritty")

        # Screencapture submap context
        self.assertEqual(by_key[("screencapture", "1")]["action"], "spawn grim screenshot.png")
        self.assertEqual(by_key[("screencapture", "1")]["flags"], {"submap": "reset"})
        self.assertEqual(by_key[("screencapture", "Escape")]["action"], "submap reset")
        self.assertEqual(by_key[("screencapture", "Escape")]["flags"], {})

    def test_unknown_fields(self):
        """Table-form bind preserves non-standard fields in unknown_flags without dropping them."""
        config_path = os.path.join(FIXTURES_DIR, "unknown_fields", "root.toml")
        res = umbriel_keys.build(config_path)

        self.assertEqual(res["errors"], [])
        self.assertEqual(res["warnings"], [])
        self.assertEqual(len(res["binds"]), 1)

        bind = res["binds"][0]
        self.assertEqual(bind["key"], "Mod+U")
        self.assertEqual(bind["action"], "window-focus next")
        self.assertEqual(bind["flags"], {})
        self.assertEqual(
            bind["unknown_flags"],
            {"custom_vendor_flag": "experimental", "priority_tier": 42},
        )
        self.assertFalse(bind["unparsed"])

    def test_malformed_included_file(self):
        """Syntax error in an included TOML file generates an ERROR-class condition in errors."""
        config_path = os.path.join(FIXTURES_DIR, "malformed_included", "root.toml")
        res = umbriel_keys.build(config_path)

        self.assertGreaterEqual(len(res["errors"]), 1)
        err = res["errors"][0]
        self.assertIn("Could not parse bad_syntax.toml:", err)
        self.assertIn("(upstream Umbriel rejects the whole config load on this error)", err)


if __name__ == "__main__":
    unittest.main()
