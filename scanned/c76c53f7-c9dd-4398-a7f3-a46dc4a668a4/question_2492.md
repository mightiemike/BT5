# Q2492: add parent cache TreeCache checkpoint restore via round trip through tree hash and bytes

## Question
Can an unprivileged attacker reach `add_parent` in `src/serde/tree_cache.rs` through public cache-backed serialization, hashing, interning, or incremental state through `add_parent`, using a crafted TreeCache checkpoint restore input and the round trip through tree hash and bytes validation path while controlling shared children across allocator checkpoints, so the code deduplicating nodes while changing pair order, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that cached length/hash/path must match uncached computation and causing Critical canonical serialization failure: cached path/bytes encode wrong tree?

## Target
- File/function: src/serde/tree_cache.rs::add_parent
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `add_parent`
- Attacker controls: shared children across allocator checkpoints
- Exploit idea: Build the smallest CLVM blob/program/API call for TreeCache checkpoint restore, drive it through round trip through tree hash and bytes, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: cached length/hash/path must match uncached computation
- Expected Immunefi impact: Critical canonical serialization failure: cached path/bytes encode wrong tree
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.
