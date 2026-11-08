#!/usr/bin/env bash
# run-nested.sh - Launch an isolated, nested Umbriel test session and overlay.
# Sets up a scratch environment, resolves the display name, closes the built-in
# cheatsheet, sets up visible cursor configuration, and runs the overlay.
# Leaves the session running after overlay exit until Enter is pressed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Verify required files exist
if [ ! -f "$REPO_ROOT/overlay.py" ]; then
    echo "Error: Cannot locate overlay.py in $REPO_ROOT" >&2
    exit 1
fi

EXAMPLE_CONFIG="$REPO_ROOT/examples/typical-desktop.toml"
if [ ! -f "$EXAMPLE_CONFIG" ]; then
    echo "Error: Cannot locate example configuration at $EXAMPLE_CONFIG" >&2
    exit 1
fi

# 1. Setup isolated scratch directories
SCRATCH=$(mktemp -d /tmp/umbriel-trial.XXXXXX)
CONFIG_DIR="$SCRATCH/config"
CACHE_DIR="$SCRATCH/cache"
mkdir -p "$CONFIG_DIR" "$CACHE_DIR/umbriel"

echo "[1/5] Created scratch environment: $SCRATCH"

# 2. Prepare scratch config and per-trial reopen wrapper
SCRATCH_CONFIG="$SCRATCH/config.toml"
REOPEN_WRAPPER="$SCRATCH/open-overlay.sh"

# Pre-create wrapper script; exact display and socket are finalized in step 4
cat << 'EOF' > "$REOPEN_WRAPPER"
#!/usr/bin/env bash
set -euo pipefail
exit 1
EOF
chmod +x "$REOPEN_WRAPPER"

# Generate TOML-encoded keybind action:
# Quote shell arguments and TOML-encode properly, even if paths contain spaces or apostrophes.
TOML_ACTION=$(python3 -c "import shlex, json, sys; print(json.dumps('spawn ' + shlex.quote(sys.argv[1])))" "$REOPEN_WRAPPER")

cp "$EXAMPLE_CONFIG" "$SCRATCH_CONFIG"
cat << EOF >> "$SCRATCH_CONFIG"

# Reopen overlay shortcut for this trial (repeat=false prevents stacking popups)
# Placed in the existing keybinds table before input.cursor settings
"Alt+Shift+Space" = { action = $TOML_ACTION, repeat = false }

# Cursor settings workaround for nested session testing:
# Disables hardware cursor planes and pins theme/size to ensure the pointer
# remains visible over the nested compositor window.
[input.cursor]
theme = "Adwaita"
size = 24
hardware_cursor = false
EOF

# Process management state
COMPOSITOR_PID=""

CLEANED_UP=0

cleanup() {
    if [ "$CLEANED_UP" -eq 1 ]; then
        return
    fi
    CLEANED_UP=1
    if [ -n "$COMPOSITOR_PID" ] && kill -0 "$COMPOSITOR_PID" 2>/dev/null; then
        echo "Terminating nested compositor (PID: $COMPOSITOR_PID)..."
        kill -TERM "$COMPOSITOR_PID" 2>/dev/null || true
        wait "$COMPOSITOR_PID" 2>/dev/null || true
    fi
    echo "Test session finished. Scratch directory: $SCRATCH"
}
trap cleanup EXIT INT TERM

# 3. Launch nested Umbriel compositor
STDOUT_LOG="$CACHE_DIR/umbriel/stdout.log"
STDERR_LOG="$CACHE_DIR/umbriel/stderr.log"

echo "[2/5] Starting nested Umbriel compositor..."
XDG_CONFIG_HOME="$CONFIG_DIR" \
XDG_CACHE_HOME="$CACHE_DIR" \
WLR_BACKENDS=wayland \
umbriel -c "$SCRATCH_CONFIG" > "$STDOUT_LOG" 2> "$STDERR_LOG" &
COMPOSITOR_PID=$!

# 4. Resolve the nested session's display name from logs
DISPLAY_NAME=""
for i in $(seq 1 50); do
    if [ ! -d "/proc/$COMPOSITOR_PID" ]; then
        echo "Error: Nested compositor exited unexpectedly (PID $COMPOSITOR_PID)" >&2
        cat "$STDERR_LOG" "$STDOUT_LOG" >&2
        exit 1
    fi

    DISPLAY_NAME=$(grep -h 'running on WAYLAND_DISPLAY=' \
        "$CACHE_DIR/umbriel/umbriel.log" \
        "$CACHE_DIR/umbriel/umbriel-stderr.log" \
        "$STDERR_LOG" \
        "$STDOUT_LOG" 2>/dev/null \
        | tail -1 | sed -n 's/.*WAYLAND_DISPLAY=\(wayland-[0-9]\+\).*/\1/p' || true)

    if [ -n "$DISPLAY_NAME" ]; then
        break
    fi
    sleep 0.2
done

if [ -z "$DISPLAY_NAME" ]; then
    echo "Error: Nested compositor did not report a WAYLAND_DISPLAY within timeout." >&2
    echo "Check logs in $CACHE_DIR/umbriel/" >&2
    exit 1
fi

echo "[3/5] Nested session active on WAYLAND_DISPLAY=$DISPLAY_NAME (PID: $COMPOSITOR_PID)"

NESTED_SOCKET="${XDG_RUNTIME_DIR:-/run/user/$UID}/umbriel-$DISPLAY_NAME.sock"

# Finalize the per-trial reopen wrapper with exact display, socket, and scratch paths
cat << 'EOF' > "$REOPEN_WRAPPER"
#!/usr/bin/env bash
set -euo pipefail
EOF
printf 'export WAYLAND_DISPLAY=%q\n' "$DISPLAY_NAME" >> "$REOPEN_WRAPPER"
printf 'export UMBRIEL_SOCKET=%q\n' "$NESTED_SOCKET" >> "$REOPEN_WRAPPER"
printf 'exec python3 %q -c %q\n' "$REPO_ROOT/overlay.py" "$SCRATCH_CONFIG" >> "$REOPEN_WRAPPER"
chmod +x "$REOPEN_WRAPPER"

# 5. Close the nested session's built-in cheatsheet
echo "[4/5] Closing nested session built-in cheatsheet..."
for i in $(seq 1 25); do
    if [ -S "$NESTED_SOCKET" ]; then
        break
    fi
    sleep 0.1
done

UMBRIEL_SOCKET="$NESTED_SOCKET" umbriel msg cheatsheet-close

# 6. Launch Keybind Overlay targeting the nested session
echo "[5/5] Launching Keybind Overlay against nested session..."
echo "      (In nested session: press Escape to close overlay; press Alt+Shift+Space to reopen)"

OVERLAY_EXIT_CODE=0
WAYLAND_DISPLAY="$DISPLAY_NAME" \
UMBRIEL_SOCKET="$NESTED_SOCKET" \
python3 "$REPO_ROOT/overlay.py" -c "$SCRATCH_CONFIG" || OVERLAY_EXIT_CODE=$?

if [ "$OVERLAY_EXIT_CODE" -eq 0 ]; then
    echo "Overlay closed normally."
else
    echo "Warning: Overlay exited with non-zero exit code $OVERLAY_EXIT_CODE." >&2
fi

# 7. Keep nested compositor alive for launched apps / exploration
echo ""
echo "Overlay closed. Nested compositor is still running (PID $COMPOSITOR_PID)."
echo "Press Enter to stop the test session..."
read -r _ || true
