#!/usr/bin/env bash
# ==============================================================================
# Umbriel Keybind Overlay: Self-Contained Automated Demo
# Launches an isolated nested session with examples/typical-desktop.toml,
# exercises search, submap tracking, and harmless window toggle,
# captures screenshots, assembles demo video/gif if ffmpeg is available,
# and cleans up completely.
# ==============================================================================

set -euo pipefail

DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$DEMO_DIR/.." && pwd)"
CONFIG_FILE="$PROJECT_DIR/examples/typical-desktop.toml"

echo "=============================================================================="
echo " Starting Umbriel Keybind Overlay Demo"
echo " Configuration: $CONFIG_FILE"
echo "=============================================================================="

# 1. Probe for installed terminal emulator
TERM_BIN=""
for t in alacritty foot kitty wezterm; do
    if command -v "$t" >/dev/null 2>&1; then
        TERM_BIN="$t"
        break
    fi
done

if [ -z "$TERM_BIN" ]; then
    echo "Error: No supported terminal emulator found (probed alacritty foot kitty wezterm)." >&2
    exit 1
fi
echo "[1/7] Detected installed terminal emulator: $TERM_BIN"

# 2. Setup scratch directories
SCRATCH_DIR=$(mktemp -d /tmp/umbriel-demo-run.XXXXXX)
mkdir -p "$SCRATCH_DIR/config" "$SCRATCH_DIR/cache"
echo "[2/7] Created scratch environment: $SCRATCH_DIR"

cleanup() {
    local exit_code=$?
    echo "[Cleanup] Ensuring nested session is terminated..."
    if [ -n "${DISPLAY_NAME:-}" ] && [ -n "${UMBRIEL_SOCKET:-}" ] && [ -S "$UMBRIEL_SOCKET" ]; then
        WAYLAND_DISPLAY="$DISPLAY_NAME" UMBRIEL_SOCKET="$UMBRIEL_SOCKET" umbriel msg session-quit skip-confirmation 2>/dev/null || true
    fi
    if [ -n "${COMPOSITOR_PID:-}" ] && kill -0 "$COMPOSITOR_PID" 2>/dev/null; then
        kill "$COMPOSITOR_PID" 2>/dev/null || true
        wait "$COMPOSITOR_PID" 2>/dev/null || true
    fi
    rm -rf "$SCRATCH_DIR"
    exit "$exit_code"
}
trap cleanup EXIT INT TERM

# 3. Launch nested Umbriel compositor session
echo "[3/7] Launching nested Umbriel session..."
XDG_CONFIG_HOME="$SCRATCH_DIR/config" \
XDG_CACHE_HOME="$SCRATCH_DIR/cache" \
WLR_BACKENDS=wayland \
umbriel -c "$CONFIG_FILE" > "$SCRATCH_DIR/umbriel.out" 2>&1 &
COMPOSITOR_PID=$!

# Wait for compositor log and WAYLAND_DISPLAY
LOG_FILE="$SCRATCH_DIR/cache/umbriel/umbriel.log"
STDERR_FILE="$SCRATCH_DIR/cache/umbriel/umbriel-stderr.log"
DISPLAY_NAME=""
for i in {1..60}; do
    for f in "$STDERR_FILE" "$LOG_FILE" "$SCRATCH_DIR/umbriel.out"; do
        if [ -f "$f" ]; then
            MATCH=$(grep 'running on WAYLAND_DISPLAY=' "$f" 2>/dev/null | tail -1 | sed -n 's/.*WAYLAND_DISPLAY=\(wayland-[0-9]\+\).*/\1/p' || true)
            if [ -n "$MATCH" ]; then
                DISPLAY_NAME="$MATCH"
                break 2
            fi
        fi
    done
    sleep 0.2
done

if [ -z "$DISPLAY_NAME" ]; then
    echo "Error: Compositor failed to start or did not report WAYLAND_DISPLAY in $LOG_FILE" >&2
    if [ -f "$SCRATCH_DIR/umbriel.out" ]; then
        echo "=== umbriel stdout/stderr ===" >&2
        cat "$SCRATCH_DIR/umbriel.out" >&2
    fi
    if [ -f "$LOG_FILE" ]; then
        echo "=== umbriel.log ===" >&2
        cat "$LOG_FILE" >&2
    fi
    exit 1
fi

UMBRIEL_SOCKET="${XDG_RUNTIME_DIR:-/run/user/1000}/umbriel-${DISPLAY_NAME}.sock"
echo "[4/7] Compositor online: PID=$COMPOSITOR_PID, DISPLAY=$DISPLAY_NAME, SOCKET=$UMBRIEL_SOCKET"

# Wait for socket availability
for i in {1..20}; do
    if [ -S "$UMBRIEL_SOCKET" ]; then
        break
    fi
    sleep 0.1
done

if [ ! -S "$UMBRIEL_SOCKET" ]; then
    echo "Error: Compositor socket $UMBRIEL_SOCKET not found" >&2
    exit 1
fi

# Sanity read-only probe
PROBE_RES=$(WAYLAND_DISPLAY="$DISPLAY_NAME" UMBRIEL_SOCKET="$UMBRIEL_SOCKET" umbriel submap --json)
echo "      Read-only sanity check (submap): $PROBE_RES"

# 4. Run automated demo UI steps
echo "[5/7] Executing demo UI sequence..."
export WAYLAND_DISPLAY="$DISPLAY_NAME"
export UMBRIEL_SOCKET="$UMBRIEL_SOCKET"

python3 -W ignore "$DEMO_DIR/run_demo_steps.py" \
    "$CONFIG_FILE" \
    "$SCRATCH_DIR/cache" \
    "$DEMO_DIR" \
    "$TERM_BIN"

# 5. Clean session termination
echo "[6/7] Shutting down nested session..."
WAYLAND_DISPLAY="$DISPLAY_NAME" UMBRIEL_SOCKET="$UMBRIEL_SOCKET" umbriel msg session-quit skip-confirmation
wait "$COMPOSITOR_PID" 2>/dev/null || true

# Verify with pgrep that nothing lingers
LINGERING=$(pgrep -af "umbriel -c $CONFIG_FILE" || true)
if [ -n "$LINGERING" ]; then
    echo "Warning: lingering processes detected: $LINGERING" >&2
    kill -9 $(pgrep -f "umbriel -c $CONFIG_FILE") 2>/dev/null || true
else
    echo "      Verification: zero lingering compositor processes confirmed."
fi

# 6. Video / GIF Assembly with ffmpeg (if installed)
echo "[7/7] Checking for ffmpeg to assemble demo media..."
if command -v ffmpeg >/dev/null 2>&1; then
    echo "      ffmpeg found: assembling demo mp4 and gif..."
    # Create MP4 (1.5 seconds per frame)
    ffmpeg -y -hide_banner -loglevel error \
        -framerate 0.67 \
        -pattern_type glob \
        -i "$DEMO_DIR/0*.png" \
        -c:v libx264 -pix_fmt yuv420p -r 30 \
        "$DEMO_DIR/demo.mp4" || true

    # Create animated GIF (1.5 seconds per frame)
    ffmpeg -y -hide_banner -loglevel error \
        -framerate 0.67 \
        -pattern_type glob \
        -i "$DEMO_DIR/0*.png" \
        -vf "scale=640:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" \
        "$DEMO_DIR/demo.gif" || true

    echo "      Media artifacts created: demo.mp4, demo.gif"
else
    echo "      ffmpeg not found; skipping animation creation silently."
fi

echo "=============================================================================="
echo " Demo Complete! Produced Artifacts:"
ls -lh "$DEMO_DIR"
echo "=============================================================================="
