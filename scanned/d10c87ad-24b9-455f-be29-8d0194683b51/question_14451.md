# Q14451: method permission mismatch at execution in logic::chunk_needs_to_be_fetched_from_archival

## Question
Can an unprivileged attacker submit a limited-key function-call transaction that reaches `chain/chunks/src/logic.rs::chunk_needs_to_be_fetched_from_archival` with control over method name encoding, receiver, and nested callback behavior and make nearcore authorize one contract method at the key layer but execute a broader or different method path at runtime, breaking the invariant that runtime dispatch must not exceed the exact method scope authorized by the access key, and leading to unauthorized transaction?

## Target
- File/function: `chain/chunks/src/logic.rs::chunk_needs_to_be_fetched_from_archival`
- Entrypoint: submit a limited-key function-call transaction
- Attacker controls: method name encoding, receiver, and nested callback behavior
- Exploit idea: authorize one contract method at the key layer but execute a broader or different method path at runtime
- Invariant to test: runtime dispatch must not exceed the exact method scope authorized by the access key
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a limited-key contract-call test that varies method encoding and callback structure and assert out-of-scope methods never execute
