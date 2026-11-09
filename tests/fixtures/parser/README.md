# Parser Compatibility Fixtures

This directory contains standalone TOML configuration fixtures used to test `umbriel_keys.py` config parsing and merge behavior.

## Compatibility Baseline

The test expectations in this directory document the parser's semantics against Umbriel's configuration format as pinned to baseline commit `c6ad18d` (Umbriel v0.1.0).

These fixtures test the overlay's documented merge and parsing rules; they are fixture-based consistency checks, not live runs against the upstream Umbriel binary.

## Documented Merge & Parse Rules

1. **Include Order & Traversal**: Required includes (`include.files`) and optional includes (`include.optional.files`) are processed recursively in listed order. The including file is processed last, giving it the highest precedence.
2. **Precedence**:
   - Scalar binding replacing an earlier definition replaces it wholesale.
   - Table-form binding redefining an earlier table-form binding merges field by field; un-overridden flags are inherited from earlier definitions and recorded in `inherited`.
3. **Include Cycle & Duplicate Handling**: Circular or duplicate includes are skipped with a warning; parser execution completes cleanly without recursion loops.
4. **Missing Required Includes**: Missing files listed in `include.files` record a warning and omit the missing bindings without aborting the parse of the remaining config.
5. **Missing Optional Includes**: Missing files listed in `include.optional.files` are silently ignored without warnings or errors.
6. **Submap Scoping**: Keybindings prefixed with `submap[<name>],<key>` are scoped to `<name>` context, leaving unprefixed keys in the `default` context.
7. **Unknown Flags**: Unrecognized fields on table-form bindings (outside `repeat`, `allow_when_locked`, `allow_when_inhibited`, `cooldown_ms`, `submap`) are preserved in `unknown_flags`.
8. **Syntax Errors**: A TOML syntax error in any file (root or included) generates an ERROR-class condition in `errors`.
