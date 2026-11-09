# Release Validation Checklist

Every tagged release must fill in a copy of this checklist before the release is published, and the release notes may only claim the validation recorded here. Unit tests passing in CI is not, by itself, native validation.

## Release: vX.Y.Z (commit SHA, date, tested by)

## Environment
- Umbriel version/commit tested against: (the tested baseline, e.g. c6ad18d or newer)
- Distro and kernel:
- GPU and driver:
- Host Wayland session:
- Terminal emulator used for session tests:

## Fresh-clone validation
- [ ] Fresh clone, following only README steps
- [ ] Commands executed, pasted verbatim:
- [ ] Result:

## Test suites
| Suite | Where run | Result (passed/skipped) |
| --- | --- | --- |
| test_overlay | CI | |
| test_run_nested | CI | |
| test_native_session | local | |
| test_ui_refinement | local | |
| test_live_session | local | |

## Popup verification
- [ ] Popup opened in a nested Umbriel session
- [ ] Search and execution spot-checked
- [ ] Clean shutdown confirmed

## Notes
- Anything that failed, was skipped, or needs follow-up.

Releases that cannot complete a row say so in the release notes instead of omitting it silently.
