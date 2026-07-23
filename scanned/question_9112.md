# Q9112: host-function charging mismatch in logic::make_partial_encoded_chunk_from_owned_parts_and_needed_receipts

## Question
Can an unprivileged attacker call a contract method that exercises runtime host functions that reaches `chain/chunks/src/logic.rs::make_partial_encoded_chunk_from_owned_parts_and_needed_receipts` with control over bounded inputs to hashing, elliptic-curve, promise, or storage host calls and make nearcore take a path where host work or copied bytes exceed the gas model that was charged, breaking the invariant that host-function gas must cover all work and copied data on every bounded path, and leading to high: non-network-level dos?

## Target
- File/function: `chain/chunks/src/logic.rs::make_partial_encoded_chunk_from_owned_parts_and_needed_receipts`
- Entrypoint: call a contract method that exercises runtime host functions
- Attacker controls: bounded inputs to hashing, elliptic-curve, promise, or storage host calls
- Exploit idea: take a path where host work or copied bytes exceed the gas model that was charged
- Invariant to test: host-function gas must cover all work and copied data on every bounded path
- Expected Immunefi impact: High: non-network-level DoS
- Fast validation: write a bounded-input runtime test that hits the most expensive host path and assert gas exhaustion occurs before heavy work is completed
