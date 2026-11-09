# Architecture

This document provides a concise map of the umbriel-keybind-overlay codebase for contributors.

## Components

### Core Application (`overlay.py`)

- `KeybindOverlay`: Top-level `Gtk.Window` managing search filtering, list rendering, submap state, and execution.
  - When gtk-layer-shell is available and supported, the window uses the overlay layer with exclusive keyboard mode; otherwise it is a plain GTK window.
  - Runs a submap listener thread (`_start_submap_listener`) to monitor compositor submap changes.
  - Applies dynamic sizing clamped by `compute_target_width`.
- `verify_safety_gate`: Multi-layer verification function verifying that the running Umbriel process, its resolved config path, the compositor log's `WAYLAND_DISPLAY`, and the IPC socket path and endpoint ownership match the target configuration.
- Action helpers:
  - `split_action`: Splits shell-like action command strings into argument lists while preserving quoted strings.
  - `is_executable_action`: Determines if a binding can be invoked, verifying it does not start with a forbidden family prefix.
  - `format_action_title`: Generates clean, human-readable display labels for actions.
- `resolve_compositor_config`: Resolves candidate compositor configurations from process command-line arguments and `/proc/<pid>/cwd`.
- `acquire_single_instance_lock`: Enforces an advisory single-instance lock per `WAYLAND_DISPLAY` using `$XDG_RUNTIME_DIR/umbriel-overlay-<display>.lock`.
- `main`: CLI entrypoint parsing arguments `-c` (or `--config`), `--scratch-config`, and `--scratch-cache`.

### Parser Module (`umbriel_keys.py`)

- Standalone parser for Umbriel TOML configuration files, operating with no GTK dependency.
- `Parser`: Internal class implementing recursive TOML loading, include directive resolution, cycle detection, and table binding merges.
- `build`: Main parsing entrypoint taking a root config path and returning structured bindings, errors, warnings, and merge notes.
- `categorize`: Maps an action's verb prefix to a display category (for example Focus, Workspaces, Resize, Launch / shell) and falls back to Other.
- `clean_label`: Cleans raw action strings for display.
- CLI: Standalone tool supporting `--json` and `--compact`. Returns exit code 0 on clean parse and exit code 3 on hard parse errors.

### Styling (`style.css`)

- GTK CSS styling for overlay window, search bar, rows, keys, and badges, loaded via `Gtk.CssProvider`.

### Scripts (`scripts/`)

- `scripts/run-nested.sh`: Launches a safe, isolated nested Umbriel session for testing with a scratch configuration.
- `scripts/install.sh`: User-local installer copying files to data directory and generating a launcher script.
- `scripts/uninstall.sh`: Manifest-verified uninstaller for removing user-local files.

### Test Suites (`tests/`)

- Headless suites (run in CI on every push):
  - `tests/test_overlay.py`: Unit tests for parsing, action handling, width clamping, and safety gate refusals via mocked `/proc`.
  - `tests/test_run_nested.py`: Headless launcher tests verifying process monitoring, signals, and socket cleanup.
  - `tests/test_parser_fixtures.py`: Compatibility baseline tests verifying TOML merge behavior against fixtures.
  - `tests/test_installer.py`: Tests for user-local install, manifest verification, symlink refusal, and quoting.
- Session-based suites (require a Wayland display server and the `umbriel` executable):
  - `tests/test_native_session.py`: Native session gate tests that spin up an isolated nested compositor.
  - `tests/test_ui_refinement.py`: UI behavior tests (sizing, hover, selection) using the `tests/wlr_ptr` virtual-pointer helper.
  - `tests/test_live_session.py`: Tests expecting an already-running nested Umbriel session.

## Data Flow

1. **Config resolution**: `main()` in `overlay.py` resolves the config file path from `-c` arguments or XDG environment defaults.
2. **Parsing**: `KeybindOverlay._load_config()` invokes `umbriel_keys.build()` to parse the root configuration and any included TOML files into binding dictionaries.
3. **UI rendering**: `KeybindOverlay` populates rows with key combinations, badges, and action descriptions, styled via `style.css`.
4. **User activation**: The user selects and activates a keybind row via keyboard or pointer.
5. **Forbidden-action check**: `KeybindOverlay._execute_bind()` inspects the first word of the action against forbidden action families (`session-*`, `config-*`, `output-*`, `input-capture-*`, `dpms-*`).
6. **Off-thread dispatch**: A background worker thread checks for cancellation, runs `verify_safety_gate()` to confirm compositor identity, and dispatches the action via `umbriel msg` if verified. Cancellation is checked before gate verification, before dispatch, and before follow-up submap commands.

## Safety Gate

The safety gate (`verify_safety_gate` in `overlay.py`) is load-bearing defense-in-depth verification. It verifies that the running Umbriel process, its resolved config path, the compositor log's `WAYLAND_DISPLAY`, and the IPC socket path and endpoint ownership match before any action is executed. These layers are not equivalent guarantees; see the README section [Safety Model](../README.md#safety-model) for full details on each layer.

## Where to Add Things

| Goal | Primary Files | Associated Tests |
|---|---|---|
| Parser behavior & TOML merge | `umbriel_keys.py` | `tests/test_parser_fixtures.py`, `tests/fixtures/parser/` |
| UI rendering, search, or style | `overlay.py`, `style.css` | `tests/test_overlay.py`, `tests/test_ui_refinement.py` |
| Nested launcher workflow | `scripts/run-nested.sh` | `tests/test_run_nested.py` |
| User-local installer/uninstaller | `scripts/install.sh`, `scripts/uninstall.sh` | `tests/test_installer.py` |
| Safety gate checks | `overlay.py` (`verify_safety_gate`) | `tests/test_overlay.py`, `tests/test_native_session.py` |

## Parser Consistency Baseline

Merge semantics implemented in `umbriel_keys.py` were consistency-tested against a Python translation of Umbriel's src/config/config_merge.cpp during development. That was a fixture-based consistency check, not an execution against native Umbriel.

## Non-Goals

- **Other compositors**: The overlay is designed specifically for Umbriel; support for Hyprland, Sway, or other Wayland compositors is out of scope.
- **Command sandboxing**: The overlay verifies session association, but does not sandbox commands. Triggered `spawn` actions run commands as configured by the user.
