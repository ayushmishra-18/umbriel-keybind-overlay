# Umbriel Keybind Overlay

**A searchable, keyboard-driven shortcut overlay for the [Umbriel](https://github.com/noctalia-dev/umbriel) Wayland compositor.**

Find keybindings, explore submaps, and execute actions without having to remember every shortcut.

[![CI](https://github.com/ayushmishra-18/umbriel-keybind-overlay/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ayushmishra-18/umbriel-keybind-overlay/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)

![Umbriel Keybind Overlay](screenshot-overlay-default.png)

> **Unofficial project.** This project is independently developed and is not affiliated with, endorsed by, or maintained by the Umbriel or Noctalia development team.

## See it in action

![Umbriel Keybind Overlay Demo](demo/demo.gif)

Automated walkthrough in a nested Umbriel session, showing live search, submap context, and action execution.

[View the full MP4 demo](demo/demo.mp4) · [Read the demo walkthrough](demo/DEMO.md)

## Why use it?

As your compositor configuration grows, remembering every keybinding becomes increasingly inconvenient. This overlay gives you a searchable interface to your existing configuration.

Instead of searching through TOML files or trying to remember a shortcut, open the overlay, type what you're looking for, and select the corresponding action.

### Features

- **Live configuration parsing** — Reads your Umbriel configuration, including nested `include` files and binding merges.
- **Instant search** — Search across key chords, action commands, labels, categories, and submaps using plain substring matching.
- **Submap awareness** — Displays the currently active submap and prioritizes bindings belonging to that context.
- **Direct action execution** — Execute supported actions using `Enter` or a mouse click.
- **Keyboard-driven navigation** — Move through results without leaving the keyboard.
- **Compact interface** — A GTK-based popup with keycap styling, action categories, and an active-submap indicator.
- **Configuration reload** — Refresh bindings from disk without restarting the overlay.
- **Session association checks** — Verifies the target compositor process and IPC endpoint before dispatching an action.

## Installation

### Requirements

| Component | Requirement |
|---|---|
| Compositor | Umbriel |
| Python | 3.11 or newer |
| GUI | GTK 3 and PyGObject |
| Wayland integration | gtk-layer-shell |

No Python packages need to be installed through `pip`.

Install the required system packages for your distribution.

**Fedora**

```bash
sudo dnf install python3 python3-gobject gtk3 gtk-layer-shell
```

**Debian / Ubuntu**

```bash
sudo apt install python3 python3-gi gir1.2-gtk-3.0 \
    libgtk-layer-shell0 gir1.2-gtklayershell-0.1
```

**Arch Linux**

```bash
sudo pacman -S python python-gobject gtk3 gtk-layer-shell
```

### User-local installer

Clone the repository:

```bash
git clone https://github.com/ayushmishra-18/umbriel-keybind-overlay.git
cd umbriel-keybind-overlay
```

Run the installer:

```bash
bash scripts/install.sh
```

The optional installer:

- Checks that Python and the required GTK bindings are available.
- Copies the application files into `${XDG_DATA_HOME:-$HOME/.local/share}/umbriel-keybind-overlay`.
- Creates a launcher at `~/.local/bin/umbriel-keybind-overlay`.
- Records SHA-256 hashes of installed files for ownership verification during updates and uninstall.
- Prints a keybinding snippet for you to add to your compositor configuration.

**The installer does not install system packages or modify your Umbriel configuration.**

If dependencies are missing, it displays the relevant package-manager commands.

Available options:

```bash
bash scripts/install.sh --help
```

- `--skip-deps-check` skips the dependency check.
- `--force` allows overwriting existing unmanaged or modified installation files.

## Quickstart: Safe Nested Session (Recommended)

The recommended way to evaluate the overlay is through the included nested-session launcher.

From the repository root, run:

```bash
./scripts/run-nested.sh
```

This script:

1. Creates an isolated temporary configuration and cache directory.
2. Starts a nested Umbriel compositor using an example configuration.
3. Resolves the nested session's Wayland display and IPC socket.
4. Closes the nested compositor's built-in cheatsheet.
5. Launches the overlay against that nested session.

The nested session uses a scratch configuration rather than your normal compositor configuration.

**Requirements for the nested launcher:**

- An installed `umbriel` executable.
- A working graphical Wayland session.
- A terminal with access to the host session's Wayland environment.

After closing the overlay, the nested compositor stays alive so you can continue exploring or use applications launched from it.

Press `Alt+Shift+Space` inside the nested session to reopen the overlay. Press `Enter` in the terminal running the launcher when you are finished.

The scratch directory is intentionally retained for inspection. The launcher prints its location when the session ends.

## Run directly from the repository

Try the nested Quickstart above first. Running directly uses your live Umbriel configuration, not a scratch one:

```bash
python3 overlay.py -c ~/.config/umbriel/config.toml
```

This runs the application without installing it, which is useful for development.

## Launching from a keybinding

After installation, add a keybinding to your existing Umbriel configuration.

For example, if `~/.local/bin` is in your `PATH`:

```toml
[keybinds]
"Mod+Space" = { action = "spawn umbriel-keybind-overlay" }
```

If you use a different installation path or `~/.local/bin` is not in your `PATH`, use the exact launcher path printed by the installer.

For a manual installation, point the action at the repository's `overlay.py` using an absolute path.

Do not create a second `[keybinds]` table if one already exists. Add the binding to your existing table instead.

## Controls

| Key / Input | Action |
|---|---|
| Type text | Search key chords, actions, labels, and categories |
| `↑` / `↓` | Move selection |
| `Ctrl+K` / `Ctrl+J` | Move selection |
| Mouse hover | Select a result |
| `Enter` | Execute the selected action |
| Single click | Execute the clicked action |
| `Escape` | Close the overlay |
| `Ctrl+R` | Reload configuration from disk |

Search uses literal substring matching, not fuzzy matching.

## Safety Model

The overlay can execute compositor actions, so it performs several checks before dispatching them.

### Session association

Before dispatching an action, the safety gate checks:

1. **Process and configuration matching** — Identifies the Umbriel process associated with the selected configuration. Ambiguous or unmatched instances are refused.
2. **Compositor log verification** — Uses the compositor's log to identify its active `WAYLAND_DISPLAY`.
3. **IPC endpoint verification** — Checks that the expected socket belongs to an open file descriptor of the matched compositor process.

These checks are designed to prevent dispatch to the wrong compositor instance. If the required information cannot be established unambiguously, execution is refused.

### Restricted actions

The overlay deliberately excludes several action families from direct execution:

- `session-*`
- `config-*`
- `output-*`
- `input-capture-*`
- `dpms-*`

These bindings remain visible, but are marked as not executable.

### Important limitations

The safety gate verifies session association; it is **not a command sandbox**.

In particular:

- Commands configured through `spawn` are executed as configured.
- Commands inside a `spawn` action are not inspected by the forbidden-action blocklist.
- A small race window remains between the final cancellation check and dispatch.
- If `config-replace` is run after the compositor starts, the changed configuration path cannot be detected over Umbriel IPC.
- An action already accepted by the compositor, or an external application already spawned, cannot be undone by closing the overlay.
- The single-instance advisory lock prevents duplicate overlay windows on the same display. It is not a compositor identity check or a security boundary.

Only use compositor configurations and executable commands that you trust.

## Example configurations

The repository includes example configurations to help you explore the overlay.

| Example | Bindings | Submaps | Description |
|---|---:|---:|---|
| [Minimal](examples/minimal.toml) | 5 | 0 | Basic navigation and terminal launching |
| [Typical desktop](examples/typical-desktop.toml) | 44 | 1 | Daily desktop workflow with workspaces, media keys, and screenshot controls |
| [Power user](examples/power-user.toml) | 58 | 3 | Advanced modal workflow with multiple submaps |

These examples can also be used when experimenting with a nested Umbriel session.

## Compatibility

- Designed specifically for the Umbriel Wayland compositor.
- Developed and tested against Umbriel commit [`c6ad18d`](https://github.com/noctalia-dev/umbriel/commit/c6ad18d5ed7e3f928e546e24a4a17719e0522ff6).
- Also run in nested sessions against commit `d3bff0f` (automated test runs). Other distributions and other Umbriel versions have not been tested.
- Configuration parsing and merge behavior are covered by compatibility fixtures based on that upstream baseline.
- Compatibility with other compositors, including Hyprland and Sway, is outside the project's scope.

The parser implements relevant configuration-reading and merge behavior independently. Fixture-based consistency checks should not be interpreted as execution against every version of the native compositor.

## Development and testing

See [CONTRIBUTING.md](CONTRIBUTING.md) before making changes.

### Run the headless test suites

```bash
python3 -m unittest tests/test_overlay.py
python3 -m unittest tests/test_run_nested.py
python3 -m unittest tests/test_parser_fixtures.py
python3 -m unittest tests/test_installer.py
```

There are 156 test methods across 7 suites. CI runs the 141 headless ones, which are the four suites below. These suites cover application logic, safety-gate behavior, parser compatibility fixtures, nested-launcher behavior, and installer/uninstaller handling.

### Run session-based tests

The following tests require a graphical Wayland session and an Umbriel executable:

```bash
python3 -m unittest tests/test_native_session.py
python3 -m unittest tests/test_ui_refinement.py
python3 -m unittest tests/test_live_session.py
```

The live-session suite additionally expects an already-running nested session with the appropriate environment variables. See the contributor guide for its setup.

To rebuild the virtual-pointer helper used by UI tests:

```bash
make -C tests
```

### CI

GitHub Actions runs the headless test suites on pushes and pull requests targeting `main`. It also checks shell-script syntax and byte-compiles the Python sources.

Passing CI does not imply that native Wayland integration tests have been performed.

## Project documentation

| Document | Purpose |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | Code structure, components, and data flow |
| [Contributing](CONTRIBUTING.md) | Development workflow and contribution guidelines |
| [Release validation](RELEASE_VALIDATION.md) | Checks required before publishing a release |
| [Third-party notices](NOTICE) | Third-party acknowledgments |

## Contributing

Contributions, bug reports, usability feedback, and feature suggestions are welcome.

You can help by:

- Reporting issues with your Umbriel version, distribution, and reproduction steps.
- Testing the overlay on different Linux distributions.
- Reporting configuration compatibility problems.
- Improving documentation.
- Submitting focused pull requests.

Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

Changes to the safety gate receive additional scrutiny because it is responsible for verifying which compositor instance receives an action.

## Uninstallation

If you installed through the user-local installer, run:

```bash
bash scripts/uninstall.sh
```

Or use the installed uninstaller:

```bash
"${XDG_DATA_HOME:-$HOME/.local/share}/umbriel-keybind-overlay/uninstall.sh"
```

The uninstaller uses the installation manifest to identify managed files. It removes unmodified files, preserves user-modified files by default, and leaves unrelated files alone.

Use `--force` only when you explicitly intend to remove modified managed files:

```bash
bash scripts/uninstall.sh --yes --force
```

Remember to manually remove any keybinding you added to your Umbriel configuration.

If you only cloned the repository and ran the application directly, stop the overlay, remove your compositor keybinding if one was added, and delete the cloned directory.

## License

Released under the [MIT License](LICENSE).

Copyright © 2026 Ayush Mishra.

See [NOTICE](NOTICE) for third-party acknowledgments.
