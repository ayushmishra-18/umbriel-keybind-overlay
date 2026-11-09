# Release Validation Checklist

Every tagged release must fill in a copy of this checklist before the release is published, and the release notes may only claim the validation recorded here. Unit tests passing in CI is not, by itself, native validation.

## Release: vX.Y.Z (commit SHA, date, tested by)

## Environment
- Umbriel version/commit tested against: (the tested baseline, e.g. c6ad18d or newer)
- How the Umbriel build was obtained (package NEVRA or built from commit):
- Distro and kernel:
- GPU and driver:
- Host Wayland session:
- Terminal emulator used for session tests:

## Fresh-clone validation
- [ ] Fresh clone, following only README steps
- [ ] Commands executed, pasted verbatim:
- [ ] Result:

## Installer validation (isolated)
- [ ] Isolation set up: temporary directory `T=$(mktemp -d)`; `HOME="$T"`, `XDG_DATA_HOME="$T/.local/share"`, `XDG_CONFIG_HOME="$T/.config"`, and `XDG_CACHE_HOME="$T/.cache"` all point inside `T` (install.sh and uninstall.sh use `HOME` and `XDG_DATA_HOME`; overlay config discovery uses `XDG_CONFIG_HOME`/`HOME`; run-nested.sh isolates `XDG_CONFIG_HOME` and `XDG_CACHE_HOME`). Do not redirect `XDG_RUNTIME_DIR` or the host `WAYLAND_DISPLAY`: the nested compositor needs them to reach the host display.
- [ ] Ran `HOME="$T" XDG_DATA_HOME="$T/.local/share" XDG_CONFIG_HOME="$T/.config" XDG_CACHE_HOME="$T/.cache" bash scripts/install.sh` under that isolation.
- [ ] Installed launcher run against the nested scratch session only: started with the nested session's `WAYLAND_DISPLAY` and `UMBRIEL_SOCKET` and an explicit `-c <scratch config.toml>` (as `scripts/run-nested.sh` writes into `open-overlay.sh`), never with auto-discovery of a host session or the real `~/.config`.
- [ ] Clean removal case: starts from a fresh install in temporary directory `T` (with `HOME="$T"`, `XDG_DATA_HOME="$T/.local/share"`, `XDG_CONFIG_HOME="$T/.config"`, `XDG_CACHE_HOME="$T/.cache"`). Create a sentinel file at `$T/.config/umbriel/config.toml` (`mkdir -p "$T/.config/umbriel" && echo "# sentinel" > "$T/.config/umbriel/config.toml"`) before running uninstall, and check afterwards that it still exists with the same content, as `test_uninstall_happy_path` does. Then ran `HOME="$T" XDG_DATA_HOME="$T/.local/share" XDG_CONFIG_HOME="$T/.config" XDG_CACHE_HOME="$T/.cache" bash scripts/uninstall.sh --yes` under that isolation. Expected (per `tests/test_installer.py` `test_uninstall_happy_path` and `test_uninstall_installed_copy_complete_cleanup`): exit status 0, output contains "Uninstall complete" and the config reminder, the data directory and launcher no longer exist, and the scratch sentinel config at `$T/.config/umbriel/config.toml` is untouched.
- [ ] Modified-file preservation case: separate run starting from a fresh install in a new temporary directory `T2=$(mktemp -d)` (repointing `HOME="$T2"`, `XDG_DATA_HOME="$T2/.local/share"`, `XDG_CONFIG_HOME="$T2/.config"`, `XDG_CACHE_HOME="$T2/.cache"`; do not reuse leftovers from `T`). Append one line to installed `style.css`, then ran `HOME="$T2" XDG_DATA_HOME="$T2/.local/share" XDG_CONFIG_HOME="$T2/.config" XDG_CACHE_HOME="$T2/.cache" bash scripts/uninstall.sh --yes`. Expected (per `tests/test_installer.py` `test_uninstall_preserves_modified_and_foreign_files` and `test_uninstall_installed_copy_sequential_flow_with_modification`): exit status 0; a "Warning: File ... has been modified by the user. Keeping it." line; modified `style.css` and any user-added file are kept; unmodified `overlay.py`, `umbriel_keys.py`, and the unmodified launcher are removed; manifest and installed `uninstall.sh` copy are RETAINED on purpose, with notice "Notice: Retaining manifest and uninstaller copy" and the printed `--yes --force` rerun command. Follow-up rerun via the installed copy (`"$T2/.local/share/umbriel-keybind-overlay/uninstall.sh" --yes --force`): exit status 0; removes the manifest, installed `uninstall.sh`, and modified `style.css`. The launcher is not part of this rerun: it was unmodified and was already removed by the first `--yes` run. The data directory is removed only if it is then empty. If the tester also added an unmanaged file, `--force` does not remove it: the data directory stays and contains only that file, the script prints "Directory ... was not empty; remaining unmanaged files were preserved", and notes that unmanaged files require manual cleanup.

## Test suites
| Suite | Where run | Result (passed/skipped) |
| --- | --- | --- |
| test_overlay | CI | |
| test_run_nested | CI | |
| test_parser_fixtures | CI | |
| test_installer | CI | |
| test_native_session | local | |
| test_ui_refinement | local | |
| test_live_session | local | |

Headless total (CI command: python3 -m unittest -v tests.test_overlay tests.test_run_nested tests.test_parser_fixtures tests.test_installer): ___ discovered / ___ passed / ___ skipped.

## Popup verification
Popup checks run only in a nested session started by scripts/run-nested.sh with a scratch config. Never against the host session.
- [ ] Popup opened in a nested Umbriel session
- [ ] Search and execution spot-checked
- [ ] Clean shutdown confirmed

## Notes
- Anything that failed, was skipped, or needs follow-up.
- Claims in release notes are limited to rows marked complete above.

Releases that cannot complete a row say so in the release notes instead of omitting it silently.
