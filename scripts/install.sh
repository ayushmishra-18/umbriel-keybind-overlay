#!/usr/bin/env bash
set -euo pipefail

# Optional user-local installer for umbriel-keybind-overlay.
# Does NOT install system packages and does NOT modify compositor configuration.

SKIP_DEPS=false
FORCE=false

print_usage() {
    cat << 'EOF'
Usage: bash scripts/install.sh [OPTIONS]

Optional user-local installer for umbriel-keybind-overlay.

Options:
  --skip-deps-check  Skip checking Python and GTK runtime dependencies
  --force            Override existing unmanaged or modified files
  -h, --help         Show this help message and exit
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-deps-check)
            SKIP_DEPS=true
            shift
            ;;
        --force)
            FORCE=true
            shift
            ;;
        -h|--help)
            print_usage
            exit 0
            ;;
        *)
            echo "Error: Unknown argument '$1'." >&2
            print_usage >&2
            exit 1
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"

for src in overlay.py umbriel_keys.py style.css scripts/uninstall.sh; do
    if [ ! -f "$REPO_ROOT/$src" ]; then
        echo "Error: Source file '$REPO_ROOT/$src' not found." >&2
        exit 1
    fi
done

# --- 1. Path and Symlink Validation ---

validate_paths_and_symlinks() {
    if [ -z "${HOME:-}" ] || [[ "$HOME" != /* ]]; then
        echo "Error: HOME must be set and absolute (got: '${HOME:-}')." >&2
        exit 1
    fi

    if [ -n "${XDG_DATA_HOME:-}" ] && [[ "$XDG_DATA_HOME" != /* ]]; then
        echo "Error: XDG_DATA_HOME must be absolute when set (got: '$XDG_DATA_HOME')." >&2
        exit 1
    fi

    raw_base_data="${XDG_DATA_HOME:-$HOME/.local/share}"
    raw_data_dir="$raw_base_data/umbriel-keybind-overlay"
    raw_launcher_dir="$HOME/.local/bin"
    raw_launcher="$raw_launcher_dir/umbriel-keybind-overlay"

    # Pre-check symlinks on raw paths before realpath dereferences them
    if [ -L "$raw_data_dir" ]; then
        echo "Error: Data directory is a symlink ($raw_data_dir). Refusing." >&2
        exit 1
    fi

    if [ -L "$raw_launcher" ]; then
        echo "Error: Launcher is a symlink ($raw_launcher). Refusing." >&2
        exit 1
    fi

    if [ -e "$raw_data_dir" ]; then
        for fn in .install-manifest overlay.py umbriel_keys.py style.css uninstall.sh; do
            if [ -L "$raw_data_dir/$fn" ]; then
                echo "Error: Managed file or manifest '$fn' is a symlink. Refusing." >&2
                exit 1
            fi
        done
        local raw_symlinks
        raw_symlinks="$(find "$raw_data_dir" -type l 2>/dev/null || true)"
        if [ -n "$raw_symlinks" ]; then
            echo "Error: Found symlink(s) inside managed directory '$raw_data_dir':" >&2
            echo "$raw_symlinks" >&2
            exit 1
        fi
    fi

    RESOLVED_BASE_DATA="$(realpath -m "$raw_base_data")"
    RESOLVED_DATA_DIR="$(realpath -m "$raw_data_dir")"
    RESOLVED_LAUNCHER_DIR="$(realpath -m "$raw_launcher_dir")"
    RESOLVED_LAUNCHER="$(realpath -m "$raw_launcher")"

    if [[ "$RESOLVED_DATA_DIR" != "$RESOLVED_BASE_DATA/"* ]] || [ "$RESOLVED_DATA_DIR" = "$RESOLVED_BASE_DATA" ]; then
        echo "Error: Data directory '$RESOLVED_DATA_DIR' must live under base data directory '$RESOLVED_BASE_DATA'." >&2
        exit 1
    fi

    local expected_launcher_dir
    expected_launcher_dir="$(realpath -m "$HOME/.local/bin")"
    if [ "$RESOLVED_LAUNCHER_DIR" != "$expected_launcher_dir" ]; then
        echo "Error: Launcher directory '$RESOLVED_LAUNCHER_DIR' must be '$expected_launcher_dir'." >&2
        exit 1
    fi

    if [ -L "$RESOLVED_DATA_DIR" ]; then
        echo "Error: Data directory is a symlink ($RESOLVED_DATA_DIR). Refusing." >&2
        exit 1
    fi

    if [ -L "$RESOLVED_LAUNCHER" ]; then
        echo "Error: Launcher is a symlink ($RESOLVED_LAUNCHER). Refusing." >&2
        exit 1
    fi

    if [ -e "$RESOLVED_DATA_DIR" ]; then
        for fn in .install-manifest overlay.py umbriel_keys.py style.css uninstall.sh; do
            if [ -L "$RESOLVED_DATA_DIR/$fn" ]; then
                echo "Error: Managed file or manifest '$fn' is a symlink. Refusing." >&2
                exit 1
            fi
        done
        local symlinks
        symlinks="$(find "$RESOLVED_DATA_DIR" -type l 2>/dev/null || true)"
        if [ -n "$symlinks" ]; then
            echo "Error: Found symlink(s) inside managed directory '$RESOLVED_DATA_DIR':" >&2
            echo "$symlinks" >&2
            exit 1
        fi
    fi
}

# --- 2. Dependency Check ---

print_distro_instructions() {
    cat << 'EOF'
To install the required dependencies, use your distribution package manager:

  Fedora:
    sudo dnf install python3 python3-gobject gtk3 gtk-layer-shell

  Debian / Ubuntu:
    sudo apt install python3 python3-gi gir1.2-gtk-3.0 libgtk-layer-shell0 gir1.2-gtklayershell-0.1

  Arch Linux:
    sudo pacman -S python python-gobject gtk3 gtk-layer-shell
EOF
}

check_dependencies() {
    if [ "$SKIP_DEPS" = "true" ]; then
        echo "Warning: Skipping dependency check (--skip-deps-check). Ensure Python 3.11+ and GTK 3 / gtk-layer-shell are installed."
        return 0
    fi

    if ! command -v python3 >/dev/null 2>&1; then
        echo "Error: python3 executable not found on PATH." >&2
        print_distro_instructions >&2
        exit 1
    fi

    local ver_ok=false
    if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        ver_ok=true
    else
        local ver_str
        ver_str="$(python3 --version 2>&1 || true)"
        if [[ "$ver_str" =~ Python[[:space:]]+([0-9]+)\.([0-9]+) ]]; then
            local major="${BASH_REMATCH[1]}"
            local minor="${BASH_REMATCH[2]}"
            if (( major > 3 || (major == 3 && minor >= 11) )); then
                ver_ok=true
            fi
        fi
    fi

    if [ "$ver_ok" != "true" ]; then
        echo "Error: Python 3.11+ is required." >&2
        print_distro_instructions >&2
        exit 1
    fi

    if ! python3 -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0"); gi.require_version("GtkLayerShell", "0.1"); from gi.repository import Gtk, Gdk, GtkLayerShell' 2>/dev/null; then
        echo "Error: Required GTK 3, PyGObject, or gtk-layer-shell Python bindings could not be imported." >&2
        print_distro_instructions >&2
        exit 1
    fi
}

# --- 3. Manifest Parsing and Collision Handling ---

MANIFEST_VALID=false
MANIFEST_PRESENT=false
declare -A MANIFEST_HASHES

parse_manifest() {
    MANIFEST_VALID=false
    MANIFEST_PRESENT=false
    MANIFEST_HASHES=()

    local manifest_path="$RESOLVED_DATA_DIR/.install-manifest"
    if [ ! -f "$manifest_path" ]; then
        return 0
    fi
    MANIFEST_PRESENT=true

    if [ -L "$manifest_path" ]; then
        echo "Error: Manifest is a symlink." >&2
        return 0
    fi

    local line_count=0
    local pattern='^[0-9a-f]{64}  (overlay\.py|umbriel_keys\.py|style\.css|uninstall\.sh|@LAUNCHER@)$'

    while IFS= read -r line || [ -n "$line" ]; do
        if [[ ! "$line" =~ $pattern ]]; then
            return 0
        fi

        local hash="${line:0:64}"
        local name="${line:66}"

        if [ -n "${MANIFEST_HASHES[$name]:-}" ]; then
            return 0
        fi

        MANIFEST_HASHES["$name"]="$hash"
        ((line_count++)) || true
    done < "$manifest_path"

    if [ "$line_count" -eq 0 ]; then
        return 0
    fi

    MANIFEST_VALID=true
}

compute_sha256() {
    sha256sum "$1" | awk '{print $1}'
}

escape_single_quotes() {
    local str="$1"
    printf '%s' "${str//\'/\'\\\'\'}"
}

toml_escape() {
    local str="$1"
    str="${str//\\/\\\\}"
    str="${str//\"/\\\"}"
    printf '%s' "$str"
}

check_collisions() {
    local overrides=()

    parse_manifest

    if [ "$MANIFEST_PRESENT" = "true" ]; then
        if [ "$MANIFEST_VALID" != "true" ]; then
            if [ "$FORCE" != "true" ]; then
                echo "Error: Manifest at '$RESOLVED_DATA_DIR/.install-manifest' is invalid or corrupt. Refusing to proceed without --force." >&2
                exit 1
            else
                overrides+=("corrupt or invalid manifest at $RESOLVED_DATA_DIR/.install-manifest")
            fi
        else
            for fn in overlay.py umbriel_keys.py style.css uninstall.sh; do
                local target_file="$RESOLVED_DATA_DIR/$fn"
                if [ -e "$target_file" ]; then
                    local cur_hash
                    cur_hash="$(compute_sha256 "$target_file")"
                    local recorded_hash="${MANIFEST_HASHES[$fn]:-}"
                    if [ -z "$recorded_hash" ] || [ "$cur_hash" != "$recorded_hash" ]; then
                        if [ "$FORCE" != "true" ]; then
                            echo "Error: File '$target_file' has been modified by the user or was not recorded in manifest. Refusing to overwrite without --force." >&2
                            exit 1
                        else
                            overrides+=("user-modified or unrecorded file '$target_file'")
                        fi
                    fi
                fi
            done
        fi
    else
        if [ -d "$RESOLVED_DATA_DIR" ]; then
            local foreign_files=()
            for fn in overlay.py umbriel_keys.py style.css uninstall.sh; do
                if [ -e "$RESOLVED_DATA_DIR/$fn" ]; then
                    foreign_files+=("$fn")
                fi
            done
            if [ ${#foreign_files[@]} -gt 0 ]; then
                if [ "$FORCE" != "true" ]; then
                    echo "Error: Data directory contains managed file(s) (${foreign_files[*]}) without a valid manifest (foreign content). Refusing to proceed without --force." >&2
                    exit 1
                else
                    overrides+=("existing foreign files without manifest in $RESOLVED_DATA_DIR: ${foreign_files[*]}")
                fi
            fi
        fi
    fi

    if [ -e "$RESOLVED_LAUNCHER" ]; then
        local launcher_ours=false
        if grep -qF "# umbriel-keybind-overlay launcher (managed)" "$RESOLVED_LAUNCHER" 2>/dev/null; then
            if [ "$MANIFEST_PRESENT" = "true" ] && [ "$MANIFEST_VALID" = "true" ]; then
                local cur_launcher_hash
                cur_launcher_hash="$(compute_sha256 "$RESOLVED_LAUNCHER")"
                local rec_launcher_hash="${MANIFEST_HASHES[@LAUNCHER@]:-}"
                if [ -n "$rec_launcher_hash" ] && [ "$cur_launcher_hash" = "$rec_launcher_hash" ]; then
                    launcher_ours=true
                fi
            fi
        fi

        if [ "$launcher_ours" != "true" ]; then
            if [ "$FORCE" != "true" ]; then
                echo "Error: Existing launcher at '$RESOLVED_LAUNCHER' is not recognized as ours (missing marker or hash mismatch with manifest). Refusing to overwrite without --force." >&2
                exit 1
            else
                overrides+=("existing unmanaged or modified launcher at $RESOLVED_LAUNCHER")
            fi
        fi
    fi

    if [ ${#overrides[@]} -gt 0 ]; then
        echo "Notice: Overriding existing files (--force):"
        for o in "${overrides[@]}"; do
            echo "  - $o"
        done
    fi
}

# --- 4. Installation ---

perform_install() {
    mkdir -p "$RESOLVED_DATA_DIR"
    mkdir -p "$RESOLVED_LAUNCHER_DIR"

    cp -p "$REPO_ROOT/overlay.py" "$RESOLVED_DATA_DIR/overlay.py"
    cp -p "$REPO_ROOT/umbriel_keys.py" "$RESOLVED_DATA_DIR/umbriel_keys.py"
    cp -p "$REPO_ROOT/style.css" "$RESOLVED_DATA_DIR/style.css"
    cp -p "$REPO_ROOT/scripts/uninstall.sh" "$RESOLVED_DATA_DIR/uninstall.sh"
    chmod +x "$RESOLVED_DATA_DIR/uninstall.sh"

    local escaped_target_overlay
    escaped_target_overlay="$(escape_single_quotes "$RESOLVED_DATA_DIR/overlay.py")"

    cat > "$RESOLVED_LAUNCHER" << EOF
#!/bin/sh
# umbriel-keybind-overlay launcher (managed)
exec python3 '$escaped_target_overlay' "\$@"
EOF
    chmod +x "$RESOLVED_LAUNCHER"

    local h_overlay h_keys h_style h_uninstall h_launcher
    h_overlay="$(compute_sha256 "$RESOLVED_DATA_DIR/overlay.py")"
    h_keys="$(compute_sha256 "$RESOLVED_DATA_DIR/umbriel_keys.py")"
    h_style="$(compute_sha256 "$RESOLVED_DATA_DIR/style.css")"
    h_uninstall="$(compute_sha256 "$RESOLVED_DATA_DIR/uninstall.sh")"
    h_launcher="$(compute_sha256 "$RESOLVED_LAUNCHER")"

    cat > "$RESOLVED_DATA_DIR/.install-manifest" << EOF
$h_overlay  overlay.py
$h_keys  umbriel_keys.py
$h_style  style.css
$h_uninstall  uninstall.sh
$h_launcher  @LAUNCHER@
EOF
}

# --- 5. Print Summary ---

print_summary() {
    echo ""
    echo "============================================================"
    echo "umbriel-keybind-overlay installation successful!"
    echo "============================================================"
    echo "Installed data files to:"
    echo "  '$(escape_single_quotes "$RESOLVED_DATA_DIR")'"
    echo "Installed launcher script to:"
    echo "  '$(escape_single_quotes "$RESOLVED_LAUNCHER")'"
    echo ""
    echo "Note: The installer does NOT modify your compositor configuration"
    echo "      or any desktop/system settings."
    echo ""

    local in_path=false
    local path_var="${PATH:-}"
    IFS=':' read -ra path_dirs <<< "$path_var"
    for p in "${path_dirs[@]}"; do
        if [ -n "$p" ]; then
            local rp
            rp="$(realpath -m "$p" 2>/dev/null || echo "$p")"
            if [ "$rp" = "$RESOLVED_LAUNCHER_DIR" ] || [ "$p" = "$raw_launcher_dir" ]; then
                in_path=true
                break
            fi
        fi
    done

    echo "Add the following snippet to your ~/.config/umbriel/config.toml:"
    echo ""
    if [ "$in_path" = "true" ]; then
        cat << 'EOF'
[keybinds]
"Mod+Space" = { action = "spawn umbriel-keybind-overlay" }
EOF
    else
        local toml_cmd
        toml_cmd="$(toml_escape "$RESOLVED_LAUNCHER")"
        echo "Warning: '$raw_launcher_dir' is not currently in your PATH."
        if [[ "$RESOLVED_LAUNCHER" =~ [[:space:]\"\'\`\$\\] ]]; then
            echo "Note: Launcher path contains whitespace or special characters; verify that Umbriel's spawn parsing handles this path correctly."
        fi
        echo ""
        echo "[keybinds]"
        echo "\"Mod+Space\" = { action = \"spawn $toml_cmd\" }"
    fi

    echo ""
    echo "To uninstall at any time, run:"
    echo "  '$(escape_single_quotes "$RESOLVED_DATA_DIR/uninstall.sh")'"
    echo "  (or bash scripts/uninstall.sh from this repository)"
    echo "============================================================"
}

# Run steps
validate_paths_and_symlinks
check_dependencies
check_collisions
perform_install
print_summary
