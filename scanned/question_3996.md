# Q3996: signature normalization mismatch in account::from_local_code_hash

## Question
Can an unprivileged attacker submit a signed transaction through ordinary public paths that reaches `core/primitives-core/src/account.rs::from_local_code_hash` with control over valid signatures and keys near normalization or parsing edge cases and make nearcore normalize one signature or key representation differently across validation layers, breaking the invariant that all validation layers must agree on one canonical key and signature interpretation, and leading to unauthorized transaction?

## Target
- File/function: `core/primitives-core/src/account.rs::from_local_code_hash`
- Entrypoint: submit a signed transaction through ordinary public paths
- Attacker controls: valid signatures and keys near normalization or parsing edge cases
- Exploit idea: normalize one signature or key representation differently across validation layers
- Invariant to test: all validation layers must agree on one canonical key and signature interpretation
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a parsing-versus-runtime signature test and assert both layers accept or reject the exact same inputs
