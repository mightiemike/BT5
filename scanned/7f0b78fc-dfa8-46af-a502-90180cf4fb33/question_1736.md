# Q1736: pop2 and cons cache TreeCache checkpoint restore via execute then serialize legacy

## Question
Can an unprivileged attacker reach `pop2_and_cons` in `src/serde/tree_cache.rs` through public cache-backed serialization, hashing, interning, or incremental state through `pop2_and_cons`, using a crafted TreeCache checkpoint restore input and the execute then serialize legacy validation path while controlling shared children across allocator checkpoints, so the code deduplicating nodes while changing pair order, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that cached length/hash/path must match uncached computation and causing Critical canonical serialization failure: cached path/bytes encode wrong tree?

## Target
- File/function: src/serde/tree_cache.rs::pop2_and_cons
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `pop2_and_cons`
- Attacker controls: shared children across allocator checkpoints
- Exploit idea: Build the smallest CLVM blob/program/API call for TreeCache checkpoint restore, drive it through execute then serialize legacy, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: cached length/hash/path must match uncached computation
- Expected Immunefi impact: Critical canonical serialization failure: cached path/bytes encode wrong tree
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.
