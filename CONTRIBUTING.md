# Contributing to umbriel-keybind-overlay

Thanks for your interest! This is an unofficial companion tool for the
[Umbriel](https://github.com/noctalia-dev/umbriel) Wayland compositor.
Contributions are welcome via issues and pull requests; the maintainer
reviews and merges all changes.

## Reporting issues

Please include:

- Umbriel version or commit (`umbriel --version` or the commit you built)
- Your distro, GTK 3 version, and `gtk-layer-shell` version
- What you ran, what you expected, and what happened
- Relevant logs (the overlay prints gate refusals and parse warnings to stderr)

## Development setup

1. Install the runtime dependencies (see the README's Installation section:
   GTK 3, gtk-layer-shell, PyGObject).
2. Run the overlay against a **nested** Umbriel session with a scratch
   config - never test against your live session. The README's Quickstart
   section walks through this exact setup.
3. For a map of the code, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Running the tests

```sh
# Non-graphical unit tests (parsing, actions, chord rendering, width clamping)
python3 tests/test_overlay.py

# Headless launcher behavior suite (fake compositor/overlay, no Wayland needed)
python3 tests/test_run_nested.py

# Parser fixture tests (compatibility baseline fixtures; headless)
python3 tests/test_parser_fixtures.py

# Installer and uninstaller behavior suite (user-local install/uninstall; headless)
python3 tests/test_installer.py

# UI behavior tests (sizing, hover, selection sync)
python3 tests/test_ui_refinement.py

# Native session gate tests (spins up an isolated nested compositor)
python3 tests/test_native_session.py

# Live session tests (expects an already-running nested Umbriel session; skips cleanly otherwise)
python3 tests/test_live_session.py

# Rebuild the virtual-pointer helper if you touch pointer code
make -C tests
```

CI (`.github/workflows/ci.yml`) runs the four headless suites on every push/PR; the live/native/UI suites need a real session locally. All tests must pass before a PR is merged. Before tagging a release, complete a copy of RELEASE_VALIDATION.md; release notes may only claim the validation recorded there.

## Pull request guidelines

- **One change per PR.** Small, focused changes are easier to review.
- **UI changes need pixel evidence.** Include `grim -c` screenshots from a
  nested session showing before/after states. `capture_v5_evidence.py` shows
  the expected capture flow (dismiss the built-in cheatsheet first).
- **The safety gate is load-bearing.** Changes to `verify_safety_gate` or its
  supporting functions get extra scrutiny: explain the threat model and show
  the gate still refuses wrong-session and ambiguous targets.
- **Stay local.** The overlay must never write to system settings
  (no gsettings/dconf writes), never touch the host session's config, and
  installation happens only through the optional user-local scripts/install.sh.
- **Keep the docs honest.** README claims must match what the code and tests
  actually do. Do not claim compatibility with compositors other than Umbriel.

## License

By contributing, you agree that your contributions are licensed under the
project's MIT license. Retain all existing copyright notices, including the
third-party notices listed in NOTICE.
