# AGENTS.md

Guidance for AI coding agents working in this repository. CONTRIBUTING.md is
the source of truth for contributors; read it before making changes. This file
summarizes the operational rules that apply specifically to agent-driven work.
It guides agents; it does not replace maintainer review.

## Project overview

Umbriel Keybind Overlay is an unofficial companion tool for the Umbriel
Wayland compositor. It displays a searchable keybind overlay parsed from the
compositor's configuration. Before any action is dispatched, a multi-layer
safety gate (`verify_safety_gate` in `overlay.py`) verifies that the running
compositor instance matches the target configuration; the overlay refuses to
operate when verification fails.

## Repository layout

- `overlay.py` - GTK overlay, safety gate, and dispatch logic
- `umbriel_keys.py` - compositor configuration parser (no GTK dependency)
- `tests/` - test suites (see below)
- `examples/`, `scripts/`, `demo/` - sample configs, helper scripts, captures
- `RELEASE_VALIDATION.md` - release checklist
- `CONTRIBUTING.md` - contributor guide and threat model

## Building and testing

Run the headless suites from the project root. CI runs these on every push:

    python3 -m unittest tests/test_overlay.py
    python3 -m unittest tests/test_run_nested.py
    python3 -m unittest tests/test_parser_fixtures.py

The remaining suites require a graphical Wayland session and the `umbriel`
executable: `test_native_session` and `test_ui_refinement` start their own
nested Umbriel session (the UI suite also needs the virtual-pointer helper,
built with `make -C tests`), and `test_live_session` expects an
already-running nested session. Run them only under the session rules below.

## Safety-critical code

- Do not weaken, bypass, or remove the safety gate or any safety check unless
  the maintainer explicitly requests it. Safety-gate changes receive
  threat-model scrutiny as described in CONTRIBUTING.md.
- Safety-gate tests must exercise the real `verify_safety_gate` through the
  mocked `/proc` layer (the `_mock_gate_env` harness); do not mock the gate
  itself. Unrelated dispatch and UI unit tests may use isolation mocks.
- Fail-closed behavior is pinned by tests: refusal tests assert the exact
  refusal message. Do not relax assertions to make a change pass.
- Do not run or test against the user's host compositor session. Session tests
  run only in an isolated nested Umbriel session, and only when the maintainer
  has explicitly authorized that.

## Contribution workflow

- Keep diffs minimal and scoped to the task; preserve unrelated changes.
- When tests change, update the coverage counts in README.md to the actual
  numbers (count test methods per suite; the README explains the CI/local
  split). Keep feature and limitation descriptions truthful about current
  behavior.
- Do not commit, push, tag, or create a release without explicit maintainer
  approval.
