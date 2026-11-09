#!/usr/bin/env bash
set -euo pipefail

# Uninstaller for umbriel-keybind-overlay.
# Removes user-local files installed by install.sh.

ASSUME_YES=false
FORCE=false

print_usage() {
    cat << 'EOF'
Usage: bash scripts/uninstall.sh [OPTIONS]

Uninstaller for umbriel-keybind-overlay.
Removes user-local files installed by install.sh.

Options:
  -y, --yes    Do not prompt for confirmation
  --force      Remove user-modified managed files and modified launcher
  -h, --help   Show this help message and exit
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -y|--yes)
            ASSUME_YES=true
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

# --- 2. Manifest Parsing ---

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

confirm_uninstall() {
    if [ "$ASSUME_YES" = "true" ]; then
        return 0
    fi

    read -r -p "Are you sure you want to uninstall umbriel-keybind-overlay? [y/N] " reply
    case "$reply" in
        [yY]|[yY][eE][sS])
            return 0
            ;;
        *)
            echo "Uninstall cancelled. Nothing was removed."
            exit 0
            ;;
    esac
}

perform_uninstall() {
    parse_manifest

    if [ "$MANIFEST_PRESENT" != "true" ]; then
        echo "Error: Manifest not found at '$RESOLVED_DATA_DIR/.install-manifest'. Refusing to delete unverified files. Please clean up manually." >&2
        exit 1
    fi

    if [ "$MANIFEST_VALID" != "true" ]; then
        echo "Error: Manifest at '$RESOLVED_DATA_DIR/.install-manifest' is invalid or corrupt. Refusing to delete files based on it. Please clean up manually." >&2
        exit 1
    fi

    local kept_force_eligible=()
    local kept_manual_only=()

    # 1. Delete or keep managed data files (overlay.py, umbriel_keys.py, style.css)
    for fn in overlay.py umbriel_keys.py style.css; do
        local target_file="$RESOLVED_DATA_DIR/$fn"
        if [ -e "$target_file" ]; then
            local cur_hash
            cur_hash="$(compute_sha256 "$target_file")"
            local recorded_hash="${MANIFEST_HASHES[$fn]:-}"

            if [ -n "$recorded_hash" ] && [ "$cur_hash" = "$recorded_hash" ]; then
                echo "Removing '$target_file'..."
                rm -f "$target_file"
            else
                if [ "$FORCE" = "true" ]; then
                    echo "Removing modified file '$target_file' (--force)..."
                    rm -f "$target_file"
                else
                    echo "Warning: File '$target_file' has been modified by the user. Keeping it."
                    kept_force_eligible+=("$target_file")
                fi
            fi
        fi
    done

    # 2. Check uninstall.sh if user-modified
    local uninstaller_path="$RESOLVED_DATA_DIR/uninstall.sh"
    if [ -e "$uninstaller_path" ]; then
        local cur_u_hash
        cur_u_hash="$(compute_sha256 "$uninstaller_path")"
        local recorded_u_hash="${MANIFEST_HASHES[uninstall.sh]:-}"
        if [ -n "$recorded_u_hash" ] && [ "$cur_u_hash" != "$recorded_u_hash" ]; then
            if [ "$FORCE" = "true" ]; then
                echo "Removing modified uninstaller '$uninstaller_path' (--force)..."
                rm -f "$uninstaller_path"
            else
                echo "Warning: File '$uninstaller_path' has been modified by the user. Keeping it."
                kept_force_eligible+=("$uninstaller_path")
            fi
        fi
    fi

    # 3. Handle launcher
    if [ -e "$RESOLVED_LAUNCHER" ]; then
        if grep -qF "# umbriel-keybind-overlay launcher (managed)" "$RESOLVED_LAUNCHER" 2>/dev/null; then
            local cur_l_hash
            cur_l_hash="$(compute_sha256 "$RESOLVED_LAUNCHER")"
            local rec_l_hash="${MANIFEST_HASHES[@LAUNCHER@]:-}"

            if [ -n "$rec_l_hash" ] && [ "$cur_l_hash" = "$rec_l_hash" ]; then
                echo "Removing launcher '$RESOLVED_LAUNCHER'..."
                rm -f "$RESOLVED_LAUNCHER"
            else
                if [ "$FORCE" = "true" ]; then
                    echo "Removing modified launcher '$RESOLVED_LAUNCHER' (--force)..."
                    rm -f "$RESOLVED_LAUNCHER"
                else
                    echo "Warning: Launcher '$RESOLVED_LAUNCHER' has been modified. Keeping it."
                    kept_force_eligible+=("$RESOLVED_LAUNCHER")
                fi
            fi
        else
            echo "Warning: Launcher '$RESOLVED_LAUNCHER' does not contain managed marker. Keeping it (requires manual cleanup)."
            kept_manual_only+=("$RESOLVED_LAUNCHER")
        fi
    fi

    # 4. Check for user-added files in data dir
    if [ -d "$RESOLVED_DATA_DIR" ]; then
        for item in "$RESOLVED_DATA_DIR"/* "$RESOLVED_DATA_DIR"/.*; do
            [ -e "$item" ] || continue
            local base
            base="$(basename "$item")"
            case "$base" in
                .|..|.install-manifest|overlay.py|umbriel_keys.py|style.css|uninstall.sh)
                    ;;
                *)
                    echo "Warning: Unmanaged file '$item' found in data directory. Keeping it (requires manual cleanup)."
                    kept_manual_only+=("$item")
                    ;;
            esac
        done
    fi

    # 5. Manifest and uninstaller retention or removal
    if [ ${#kept_force_eligible[@]} -gt 0 ]; then
        echo ""
        echo "Notice: Retaining manifest and uninstaller copy at '$RESOLVED_DATA_DIR/uninstall.sh' because modified file(s) were kept."
        echo "The following modified file(s) were kept:"
        for f in "${kept_force_eligible[@]}"; do
            echo "  - $f"
        done
        echo "To remove these modified file(s), re-run with --force:"
        echo "  '$(escape_single_quotes "$RESOLVED_DATA_DIR/uninstall.sh")' --yes --force"

        if [ ${#kept_manual_only[@]} -gt 0 ]; then
            echo ""
            echo "The following unmanaged item(s) cannot be removed by --force and require manual cleanup:"
            for f in "${kept_manual_only[@]}"; do
                echo "  - $f"
            done
        fi
    else
        # No force-eligible items kept: remove manifest and unmodified uninstall.sh
        if [ -e "$RESOLVED_DATA_DIR/.install-manifest" ]; then
            echo "Removing manifest '$RESOLVED_DATA_DIR/.install-manifest'..."
            rm -f "$RESOLVED_DATA_DIR/.install-manifest"
        fi

        if [ -e "$uninstaller_path" ]; then
            echo "Removing uninstaller copy '$uninstaller_path'..."
            rm -f "$uninstaller_path"
        fi

        if [ -d "$RESOLVED_DATA_DIR" ]; then
            if rmdir "$RESOLVED_DATA_DIR" 2>/dev/null; then
                echo "Removed directory '$RESOLVED_DATA_DIR'."
            else
                echo "Note: Directory '$RESOLVED_DATA_DIR' was not empty; remaining unmanaged files were preserved:"
                ls -A "$RESOLVED_DATA_DIR"
            fi
        fi

        if [ ${#kept_manual_only[@]} -gt 0 ]; then
            echo ""
            echo "The following unmanaged item(s) require manual cleanup:"
            for f in "${kept_manual_only[@]}"; do
                echo "  - $f"
            done
        fi
    fi

    echo ""
    echo "Uninstall complete."
    echo "Reminder: Please manually remove any keybind entry you added to your compositor configuration (~/.config/umbriel/config.toml)."
}

validate_paths_and_symlinks
confirm_uninstall
perform_uninstall
