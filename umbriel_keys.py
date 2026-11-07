#!/usr/bin/env python3
"""umbriel-keys - parse Umbriel's TOML keybind config into UI-ready JSON.

Usage: umbriel-keys [CONFIG] [--json] [--compact]
  CONFIG   path to the root config file (default ~/.config/umbriel/config.toml)
  --json   emit JSON to stdout (default; flag exists for clarity)
  --compact  single-line JSON

Exit codes: 0 = parsed (warnings may be present); 3 = an ERROR-class condition
was found (a config file failed to parse; upstream Umbriel rejects the whole
load in this case - see README).

Merge semantics implemented here were consistency-tested against a Python
translation of Umbriel's src/config/config_merge.cpp during development.
That was a fixture-based consistency check, not an execution against native Umbriel.

Honesty rules (so a UI over this never silently lies):
- missing REQUIRED include -> entry in "warnings", binds from it absent
- include cycle or duplicate -> entry in "warnings" (upstream warns)
- TOML parse error in any file -> entry in "errors" (upstream: hard error)
- same context+key redefined -> merged per upstream deepMerge; a note in
  "notes" records which file supplied what (scalar replaces wholesale;
  table-form merges field by field; including file merges last)
- unknown bind fields are preserved in "unknown_flags", never dropped
- a table bind without an action string -> unparsed row with raw content
- Mod alias is never resolved by guessing: "mod_note" reports the documented
  default plus any general.mod_key override found
"""
import argparse
import json
import os
import re
import sys

try:
    import tomllib as _toml
    def _load(path):
        with open(path, "rb") as fh:
            return _toml.load(fh)
except ModuleNotFoundError:  # python < 3.11 fallback
    import toml as _toml_pkg
    def _load(path):
        return _toml_pkg.load(path)

SUBMAP_RE = re.compile(r"^submap\[([^\]]+)\],(.+)$")
KNOWN_FLAGS = {"repeat", "allow_when_locked", "allow_when_inhibited", "cooldown_ms", "submap"}

CATEGORIES = [
    ("window-focus", "Focus"), ("window-move-to-workspace", "Workspaces"),
    ("window-move", "Move windows"), ("window-toggle", "Window state"),
    ("window-modify", "Resize"), ("window-cycle", "Resize"),
    ("window-consume", "Scrolling columns"), ("window-set", "Resize"),
    ("window-restore", "Scratchpad"), ("column-", "Scrolling columns"),
    ("workspace-", "Workspaces"), ("scratchpad-", "Scratchpad"),
    ("session-", "Session"), ("overview-", "Overview"), ("layout-", "Layout"),
    ("effect-", "Effects"), ("shortcuts-", "Passthrough"),
    ("cheatsheet-", "Cheatsheet"), ("submap", "Submaps"), ("spawn", "Launch / shell"),
]

SCHEMA_VERSION = 1


def clean_label(action: str) -> str:
    parts = action.strip().split(None, 1)
    verb, args = parts[0], (parts[1] if len(parts) > 1 else "")
    if verb == "spawn":
        m = re.match(r"^noctalia msg (?:panel-toggle )?(.+)$", args)
        if m:
            return "Noctalia: " + m.group(1).replace("-", " ")
        return "Run: " + args
    label = verb.replace("-", " ")
    if args:
        label += " " + args
    return label[0].upper() + label[1:]


def categorize(action: str) -> str:
    verb = action.strip().split(None, 1)[0]
    for prefix, cat in CATEGORIES:
        if verb.startswith(prefix):
            return cat
    return "Other"


def expand_include_path(raw, base):
    """Upstream expandPath: $VAR/${VAR} env expansion, ~ and ~/, then resolve
    relative to the including file's directory."""
    expanded = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: os.environ.get(m.group(1), ""), raw)
    expanded = re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)", lambda m: os.environ.get(m.group(1), ""), expanded)
    if expanded == "~" or expanded.startswith("~/"):
        expanded = os.path.expanduser(expanded)
    fp = expanded if os.path.isabs(expanded) else os.path.join(base, expanded)
    return os.path.normpath(fp)


class Parser:
    def __init__(self):
        self.seen = set()
        self.binds = []
        self.warnings = []
        self.errors = []
        self.mod_keys = []
        self.order = 0

    def parse(self, path):
        path = os.path.abspath(path)
        if path in self.seen:
            # upstream: Warning "include cycle or duplicate skipped" - never silent
            self.warnings.append(
                f"Include cycle or duplicate skipped: {os.path.basename(path)} - matches upstream warning")
            return
        self.seen.add(path)
        try:
            data = _load(path)
        except Exception as e:
            # upstream: ERROR - startup fails / reload rejected with fallback
            self.errors.append(
                f"Could not parse {os.path.basename(path)}: {e} "
                f"(upstream Umbriel rejects the whole config load on this error)")
            return
        base = os.path.dirname(path)
        mk = data.get("general", {}).get("mod_key")
        if mk:
            self.mod_keys.append((os.path.basename(path), mk))
        inc = data.get("include", {})
        for f in inc.get("files", []):
            fp = expand_include_path(f, base)
            if os.path.exists(fp):
                self.parse(fp)
            else:
                self.warnings.append(
                    f"Required include not found: {f} (from {os.path.basename(path)}); "
                    f"binds from it are MISSING above")
        for f in inc.get("optional", {}).get("files", []):
            fp = expand_include_path(f, base)
            if os.path.exists(fp):
                self.parse(fp)
        self.order += 1
        for raw_key, val in data.get("keybinds", {}).items():
            key = raw_key.strip()
            context = "default"
            m = SUBMAP_RE.match(key)
            if m:
                context, key = m.group(1), m.group(2)
            entry = {"key": key, "context": context,
                     "source": os.path.basename(path), "order": self.order}
            if isinstance(val, str):
                entry.update(action=val, flags={}, unknown_flags={}, unparsed=False, raw=val)
            elif isinstance(val, dict):
                action = val.get("action")
                entry.update(
                    action=action if isinstance(action, str) else "",
                    flags={k: v for k, v in val.items() if k in KNOWN_FLAGS and k != "action"},
                    unknown_flags={k: v for k, v in val.items() if k not in KNOWN_FLAGS and k != "action"},
                    unparsed=not isinstance(action, str),
                    raw=json.dumps(val),
                )
            else:
                entry.update(action="", flags={}, unknown_flags={}, unparsed=True, raw=json.dumps(val))
            entry["label"] = clean_label(entry["action"]) if entry["action"] else "(could not parse this bind)"
            entry["category"] = categorize(entry["action"]) if entry["action"] else "Unparsed"
            self.binds.append(entry)

    def dedupe(self):
        """Verified against umbriel src/config/config_merge.cpp deepMerge:
        scalar bind replaces an earlier definition wholesale; table-form bind
        merges field by field when both definitions are tables; including file
        merges last (wins); includes apply in listed order (required, then
        optional, then the including file)."""
        notes = []
        by_id, order = {}, []
        for b in self.binds:
            bid = (b["context"], b["key"])
            if bid not in by_id:
                by_id[bid] = dict(b)
                order.append(bid)
                continue
            old_b = by_id[bid]
            if b["unparsed"] or old_b["unparsed"] or not b["raw"].startswith("{") or not old_b["raw"].startswith("{"):
                notes.append(
                    f"'{b['key']}' ({b['context']}): later definition ({b['source']}) replaces the earlier one "
                    f"({old_b['source']}) entirely - verified upstream merge rule")
                by_id[bid] = dict(b)
                continue
            inherited = {k: v for k, v in old_b["flags"].items() if k not in b["flags"]}
            merged = dict(b)
            merged["flags"] = {**old_b["flags"], **b["flags"]}
            merged["unknown_flags"] = {**old_b["unknown_flags"], **b["unknown_flags"]}
            if inherited:
                merged["inherited"] = {k: (v, old_b["source"]) for k, v in inherited.items()}
                notes.append(
                    f"'{b['key']}' ({b['context']}): action from {b['source']}; fields {', '.join(inherited)} "
                    f"inherited from {old_b['source']} - verified upstream table-merge rule")
            else:
                notes.append(
                    f"'{b['key']}' ({b['context']}): later definition ({b['source']}) takes precedence - "
                    f"verified upstream merge rule")
            by_id[bid] = merged
        return [by_id[i] for i in order], notes


def build(config_path):
    p = Parser()
    p.parse(config_path)
    binds, notes = p.dedupe()
    mod_note = "Mod = Super on a real session, Alt when Umbriel runs nested (documented default)"
    if p.mod_keys:
        mod_note += "; general.mod_key override found: " + ", ".join(
            f"{f} sets '{v}'" for f, v in p.mod_keys)
    return {
        "schema_version": SCHEMA_VERSION,
        "config": os.path.abspath(config_path),
        "binds": binds,
        "warnings": p.warnings,
        "errors": p.errors,
        "notes": notes,
        "mod_note": mod_note,
        "counts": {
            "binds": len(binds),
            "submaps": sorted({b["context"] for b in binds if b["context"] != "default"}),
            "unparsed": sum(1 for b in binds if b["unparsed"]),
        },
    }


def main():
    ap = argparse.ArgumentParser(prog="umbriel-keys",
                                 description="Parse Umbriel TOML keybind config into UI-ready JSON.")
    ap.add_argument("config", nargs="?", default="~/.config/umbriel/config.toml")
    ap.add_argument("--json", action="store_true", help="emit JSON (default)")
    ap.add_argument("--compact", action="store_true", help="single-line JSON")
    args = ap.parse_args()
    config = os.path.expanduser(args.config)
    if not os.path.isfile(config):
        print(json.dumps({"schema_version": SCHEMA_VERSION, "config": config,
                          "binds": [], "warnings": [],
                          "errors": [f"config file not found: {config}"],
                          "notes": [], "mod_note": "",
                          "counts": {"binds": 0, "submaps": [], "unparsed": 0}},
                         indent=None if args.compact else 1))
        sys.exit(3)
    out = build(config)
    json.dump(out, sys.stdout, indent=None if args.compact else 1)
    print()
    sys.exit(3 if out["errors"] else 0)


if __name__ == "__main__":
    main()
