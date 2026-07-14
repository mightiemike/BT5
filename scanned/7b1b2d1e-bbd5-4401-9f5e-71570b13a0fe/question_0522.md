# Q522: is visited cache IdentityHash repeated key pattern via nil atom reused inside pair

## Question
Can an unprivileged attacker reach `is_visited` in `src/serde/bitset.rs` through public cache-backed serialization, hashing, interning, or incremental state through `is_visited`, using a crafted IdentityHash repeated key pattern input and the nil atom reused inside pair validation path while controlling shared children across allocator checkpoints, so the code deduplicating nodes while changing pair order, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that restore/undo must remove future state and causing Critical canonical serialization failure: cached path/bytes encode wrong tree?

## Target
- File/function: src/serde/bitset.rs::is_visited
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `is_visited`
- Attacker controls: shared children across allocator checkpoints
- Exploit idea: Build the smallest CLVM blob/program/API call for IdentityHash repeated key pattern, drive it through nil atom reused inside pair, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: restore/undo must remove future state
- Expected Immunefi impact: Critical canonical serialization failure: cached path/bytes encode wrong tree
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.
