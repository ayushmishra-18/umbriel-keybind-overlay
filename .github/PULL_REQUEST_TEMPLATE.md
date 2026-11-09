## Summary

<!-- Concise description of changes and motivation -->

## Contributor Checklist

- [ ] One change per PR.
- [ ] Headless test suites pass (`python3 -m unittest -v tests.test_overlay tests.test_run_nested tests.test_parser_fixtures tests.test_installer`; see [Running the tests](https://github.com/ayushmishra-18/umbriel-keybind-overlay/blob/main/CONTRIBUTING.md#running-the-tests)).
- [ ] README coverage counts updated to actual numbers if tests changed.
- [ ] UI change: `grim -c` before/after screenshots attached.
- [ ] Safety-gate change: threat model explained, refusal of wrong-session and ambiguous targets still shown.
- [ ] Stay local: no system-settings writes (no gsettings or dconf writes), no host-session config changes, and installation happens only through the optional user-local scripts/install.sh.
- [ ] Keep the docs honest: README claims still match code and tests.
