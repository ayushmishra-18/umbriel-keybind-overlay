# Umbriel Keybind Overlay

A searchable keyboard-shortcut overlay for the [Umbriel](https://github.com/noctalia-dev/umbriel) Wayland compositor. It reads your live `config.toml`, displays your active keybindings in a compact launcher-style panel with keycap chords and clean labels, provides real-time text search, tracks compositor submaps over IPC, and lets you trigger bindings directly with `Enter` (high-risk action families are excluded - see Safety Model).

![Umbriel Keybind Overlay](screenshot-overlay-default.png)

---

## Installation & Prerequisites

### 1. Prerequisites

- **Compositor**: `umbriel` (developed and tested against commit `c6ad18d`). Compatibility with other compositors or other revisions has not been tested.
- **Python Runtime**: Python 3.11+ (tested on Python 3.14).
- **System Packages**:
  - **Fedora**: `sudo dnf install python3 python3-gobject gtk3 gtk-layer-shell`
  - **Debian / Ubuntu**: `sudo apt install python3 python3-gi gir1.2-gtk-3.0 libgtk-layer-shell0`
  - **Arch Linux**: `sudo pacman -S python python-gobject gtk3 gtk-layer-shell`
- **Optional Tools**:
  - `grim`: For capturing screenshots.
  - `ffmpeg`: For generating MP4/GIF walkthrough animations with `demo/demo.sh`.
  - `gcc` + `wayland-client`: For rebuilding the test pointer tool (`make -C tests`).

---

## Quickstart: Safe Nested Session (Recommended)

The safest and easiest way to try the overlay is inside an isolated nested Umbriel session. This lets you explore the overlay without altering your active desktop environment.

### 1. Launch a Nested Umbriel Session

From the project directory, create a temporary scratch directory and start a nested compositor using the provided desktop example configuration:

```bash
# Create isolated scratch environment
SCRATCH=$(mktemp -d /tmp/umbriel-trial.XXXXXX)
mkdir -p "$SCRATCH/config" "$SCRATCH/cache"

# Launch nested compositor with the typical desktop example
XDG_CONFIG_HOME="$SCRATCH/config" \
XDG_CACHE_HOME="$SCRATCH/cache" \
WLR_BACKENDS=wayland \
umbriel -c "$(pwd)/examples/typical-desktop.toml" &
```

A nested Umbriel window will appear on your desktop.

### 2. Launch the Overlay Against the Nested Session

Target the nested instance by detecting its Wayland display and socket:

```bash
# Wait for the nested compositor to report its display name
DISPLAY_NAME=""
for i in $(seq 1 50); do
  DISPLAY_NAME=$(grep -h 'running on WAYLAND_DISPLAY=' \
    "$SCRATCH/cache/umbriel/umbriel-stderr.log" \
    "$SCRATCH/cache/umbriel/umbriel.log" 2>/dev/null \
    | tail -1 | sed -n 's/.*WAYLAND_DISPLAY=\(wayland-[0-9]\+\).*/\1/p')
  [ -n "$DISPLAY_NAME" ] && break
  sleep 0.2
done
if [ -z "$DISPLAY_NAME" ]; then
  echo "Nested compositor did not report a display name; check $SCRATCH/cache/umbriel/" >&2
  exit 1
fi

# Run the overlay against the nested compositor
WAYLAND_DISPLAY="$DISPLAY_NAME" \
UMBRIEL_SOCKET="/run/user/$UID/umbriel-$DISPLAY_NAME.sock" \
python3 overlay.py -c "$(pwd)/examples/typical-desktop.toml"
```

### Overlay Controls

- **Type**: Filters bindings by chord, action name, or category. Search entry automatically maintains typing focus.
- **`↑` / `↓`** (or **`Ctrl+K` / `Ctrl+J`**): Move selection up and down.
- **Mouse Hover**: Pointer motion over a row moves the selection highlight.
- **`Enter` or Single-Click**: Trigger the selected/clicked action (executes via `umbriel msg` and closes the overlay).
- **`Escape`**: Close the overlay immediately without executing anything.
- **`Ctrl+R`**: Reload the configuration from disk.

---

## Running on Your Real Umbriel Session

You can run the overlay directly in your primary Umbriel session, whether it was launched with an explicit `-c` path or ordinarily launched as a systemd user service or desktop autostart (no `-c` flag):

```bash
python3 overlay.py -c ~/.config/umbriel/config.toml
```

If `-c` is omitted, the overlay defaults to `$XDG_CONFIG_HOME/umbriel/config.toml` (or `~/.config/umbriel/config.toml`).

### Session Safety Gate & Limitations

The overlay employs a strict five-layer safety gate before executing any action via `Enter` or single-click:
1. **Verification**: Matches the active `umbriel` process against the overlay's configuration (via command-line arguments or `/proc/<pid>/environ`), verifies the compositor log, and checks that `UMBRIEL_SOCKET` is actively owned by an open file descriptor of that compositor PID.
2. **Refusal**: Execution is refused if zero matching processes are found, multiple instances match the same config, `/proc` data is unreadable, or the socket inode does not match the compositor.
3. **Forbidden Families**: Actions in dangerous families (`session-*`, `config-*`, `output-*`, `input-capture-*`) are blocked and display an advisory error banner without executing.
4. **Honest Limitations**:
   - **Post-launch `config-replace` is undetectable**: Umbriel's IPC does not expose the in-memory config path.
   - **Spawn rows run as-is**: The overlay executes configured `spawn` commands directly without sandboxing. Only use configurations you trust.

### Configuring a Launch Shortcut in Umbriel

To bind `Mod+Space` in your Umbriel `config.toml` to open the overlay:

```toml
[keybinds]
"Mod+Space" = { action = "spawn /path/to/umbriel-overlay/overlay.py" }
```

---

## Running Tests & Reproducing Tooling

### Test Suites

Run the test suites from the project root:

```bash
# Non-graphical unit tests (parsing, actions, chord rendering, width clamping)
python3 -m unittest tests/test_overlay.py

# Native session integration tests (process association, socket ownership)
python3 -m unittest tests/test_native_session.py

# UI refinement tests (sizing extremes, cursor fallback, hover sync)
python3 -m unittest tests/test_ui_refinement.py
```

### Rebuilding the Virtual Pointer Tool

The test suite uses an in-repo virtual pointer tool (`tests/wlr_ptr`) based on the `zwlr_virtual_pointer_v1` Wayland protocol to simulate precise mouse motion and clicks. To rebuild it:

```bash
make -C tests
```

---

## Safety Model

The overlay employs a five-layer defense-in-depth architecture to reduce the risk of actions misfiring against the wrong compositor session. Two things it does not do: it does not make `spawn` rows safe - `Enter` runs the configured command as-is, so only use it with configs you trust - and it does not detect in-memory configuration changes if `umbriel config-replace` was run post-launch:

1. **Process & Config Match**: Discovers running `umbriel` processes and matches the overlay's canonical configuration path against the compositor's configuration. Supports both `-c <path>` invocations and default autostart sessions (resolving `$XDG_CONFIG_HOME/umbriel/config.toml` or `~/.config/umbriel/config.toml` directly from `/proc/<pid>/environ`). Ambiguity (zero or multiple matches) refuses execution.
2. **Compositor Log Verification**: Reads the running instance's log (`umbriel-stderr.log` / `umbriel.log` located via `XDG_CACHE_HOME` or process file descriptors) to confirm instance identity and extract its active `WAYLAND_DISPLAY`.
3. **Canonical Endpoint & Inode Ownership Verification**: Confirms that the caller's `WAYLAND_DISPLAY` matches the compositor log and that `UMBRIEL_SOCKET` canonical path matches `$XDG_RUNTIME_DIR/umbriel-$WAYLAND_DISPLAY.sock`. Furthermore, maps the socket path to its inode in `/proc/net/unix` and verifies that the matched compositor process holds an open file descriptor referencing that socket inode under `/proc/<pid>/fd/`. Sockets belonging to other processes or directories are rejected.
4. **Forbidden Action Blocklist**: Actions belonging to high-risk families are blocked from execution:
   - `session-*` (e.g. `session-quit`, logout)
   - `config-*` (e.g. `config-replace`)
   - `output-*` (e.g. `output-destroy`)
   - `input-capture-*`
   These rows display a red `not executable` danger badge in the UI. Pressing `Enter` on them refuses execution and shows a safety warning in the footer without affecting your session.
5. **Single-Instance Enforcement**: An advisory lockfile (`$XDG_RUNTIME_DIR/umbriel-overlay-<display>.lock`, e.g. `umbriel-overlay-wayland-0.lock`) enforces a single instance per Wayland display, preventing multiple overlay instances from stacking surfaces or conflicting on exclusive keyboard grab.

---

## Example Configurations

The `examples/` directory contains realistic configurations validated against Umbriel's native validator (`umbriel config validate -c <file> --json`):

- **`examples/minimal.toml`** (5 bindings): A minimal setup covering core window closing, terminal spawning, and basic navigation.
- **`examples/typical-desktop.toml`** (44 bindings, 1 submap): A complete daily driver setup with application launchers, vim-style navigation, full 1–9 workspace management, media keys, and a screencapture submap.
- **`examples/power-user.toml`** (58 bindings, 3 submaps): An advanced configuration featuring three distinct submaps (`screencapture`, `window_mgmt`, and `system`), custom window splits, and layout controls.

---

## Automated Demo & Verification

A self-contained automated demonstration script is available in `demo/`:

```bash
cd demo
./demo.sh
```

The script:
1. Launches an isolated nested session with `examples/typical-desktop.toml`.
2. Steps through the overlay UI (default view, search filtering, submap context banner).
3. Spawns an installed terminal emulator (`alacritty`), executes `window-toggle-maximize` from the overlay, and records before-and-after compositor window geometry in `windows-before-maximize.json` and `windows-after-maximize.json`.
4. Produces labeled screenshot artifacts and compiles an animated `demo.mp4` / `demo.gif` walkthrough (if `ffmpeg` is installed).
5. Cleanly terminates the nested session with zero lingering background processes.

See [`demo/DEMO.md`](demo/DEMO.md) for a narrated 2-minute walkthrough with screenshots and JSON deltas.

---

## Known Limitations & Development Status

- **Development Status**: This tool is an early-stage prototype developed and verified entirely in local isolated nested sessions.
- **Submap Inheritance**: Umbriel treats submaps as strict modal groups rather than hierarchical inheritance trees. The overlay reflects this behavior directly.
- **Layer-Shell Surface**: Requires Wayland compositor support for `zwlr_layer_shell_v1` (supported natively by Umbriel via wlroots).
- **Execution Limitation**: As described in the safety model, action triggering requires a running Umbriel instance whose configuration deterministically associates with the overlay's config (either via `-c <path>` or native `$XDG_CONFIG_HOME/umbriel/config.toml`). Post-launch in-memory config switches via `umbriel config-replace` cannot be detected.

---

## Third-Party Credits

- **`umbriel_keys.py`**: Config parsing and table-merge logic (a stdlib-only Python parser reproducing Umbriel's upstream C++ `config_merge.cpp` semantics).
